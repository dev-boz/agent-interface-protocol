from __future__ import annotations

import json
import re
from typing import Any

from .block import load_block_rules, match_block
from .handoff import reject_handoff, validate_handoff_packet
from .workspace import AipWorkspace

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_EVENT_ALIASES = {
    "sessionstart": "session_start",
    "sessionstarthook": "session_start",
    "session_start": "session_start",
    "sessionend": "session_end",
    "session_end": "session_end",
    "userpromptsubmit": "session_start",
    "beforeagent": "session_start",
    "afteragent": "task_completed",
    "pretooluse": "pre_tool_use",
    "pre_tool_use": "pre_tool_use",
    "beforetool": "pre_tool_use",
    "toolexecutebefore": "pre_tool_use",
    "posttooluse": "post_tool_use",
    "post_tool_use": "post_tool_use",
    "aftertool": "post_tool_use",
    "toolexecuteafter": "post_tool_use",
    "taskcompleted": "task_completed",
    "task_completed": "task_completed",
    "agentturncomplete": "task_completed",
    "stop": "task_completed",
    "subagentstart": "subagent_start",
    "subagent_start": "subagent_start",
    "agentspawn": "subagent_start",
    "subagentstop": "subagent_stop",
    "subagent_stop": "subagent_stop",
    # TeammateIdle fires when a Claude Code teammate goes idle. Map to session_end
    # so the workspace records the agent as idle (spec §"Native Hooks" table).
    "teammateidle": "session_end",
    "teammate_idle": "session_end",
}


class HookError(ValueError):
    """Raised when a hook payload or event is invalid."""


def normalize_hook_event(name: str) -> str:
    canonical = _NON_ALNUM.sub("", name.strip().lower())
    if not canonical:
        raise HookError("hook event must not be empty")
    try:
        return _EVENT_ALIASES[canonical]
    except KeyError as exc:
        raise HookError(f"Unsupported hook event: {name}") from exc


def parse_hook_stdin(stdin_text: str) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise HookError(f"Invalid JSON hook payload from stdin: {exc}") from exc
    if not isinstance(payload, dict):
        raise HookError("Hook stdin payload must decode to a JSON object")
    event_name = _first_non_empty(payload, "hook_event_name", "hookEventName")
    if event_name is None:
        raise HookError("Hook stdin payload missing hook_event_name")
    return event_name, payload


def parse_codex_notification(notification_text: str) -> tuple[str | None, dict[str, Any]]:
    try:
        payload = json.loads(notification_text)
    except json.JSONDecodeError as exc:
        raise HookError(f"Invalid JSON Codex notification payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise HookError("Codex notification payload must decode to a JSON object")

    notification_type = _first_non_empty(payload, "type")
    if notification_type == "agent-turn-complete":
        return "agent-turn-complete", payload
    return None, payload


def _first_non_empty(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _extract_command(payload: dict[str, Any]) -> str:
    """Extract the command string from a PreToolUse payload.

    Native hooks (e.g. Claude Code) deliver the command nested as
    ``tool_input={"command": "..."}``; stringifying that dict would defeat
    anchored block patterns, so the nested value is pulled out explicitly.
    """
    tool_input = payload.get("tool_input")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if isinstance(tool_input, dict):
        nested = _first_non_empty(tool_input, "command")
        if nested:
            return nested
    return (
        _first_non_empty(payload, "command")
        or _first_non_empty(data, "command")
        or (_first_non_empty(payload, "tool_input") if not isinstance(tool_input, dict) else None)
        or ""
    )


class HookRuntime:
    def __init__(self, workspace_root: str, agent_name: str) -> None:
        self.workspace = AipWorkspace(workspace_root)
        self.workspace.ensure()
        self.agent_name = agent_name
        # Dangerous-command guard patterns from .aip/block (spec §"Hook Guard
        # Patterns"). Loaded once so native PreToolUse hooks intercept the same
        # commands the Tier-2 shim blocks.
        self._block_rules = load_block_rules(workspace_root)

    def emit(self, event_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = normalize_hook_event(event_name)
        hook_payload = payload or {}

        if normalized == "session_start":
            return self._handle_status_event(
                status="working",
                event_name=event_name,
                payload=hook_payload,
                message=_first_non_empty(hook_payload, "message", "prompt", "summary"),
                active=True,
            )
        if normalized == "task_completed":
            return self._handle_status_event(
                status="finished",
                event_name=event_name,
                payload=hook_payload,
                message=_first_non_empty(hook_payload, "message", "summary"),
                active=True,
            )
        if normalized == "session_end":
            return self._handle_status_event(
                status="idle",
                event_name=event_name,
                payload=hook_payload,
                message=_first_non_empty(hook_payload, "message", "summary"),
                active=False,
                remove_keys=("current_tool", "last_tool_status"),
            )
        if normalized == "pre_tool_use":
            blocked = self._check_block(event_name, hook_payload)
            if blocked is not None:
                return blocked
            rejected = self._check_handoff(event_name, hook_payload)
            if rejected is not None:
                return rejected
            return self._handle_tool_event(
                event_name=event_name,
                payload=hook_payload,
                tool_status="started",
            )
        if normalized == "post_tool_use":
            return self._handle_tool_event(
                event_name=event_name,
                payload=hook_payload,
                tool_status="completed",
                remove_keys=("current_tool",),
            )
        if normalized == "subagent_start":
            return self._handle_subagent_event(
                event_name=event_name,
                payload=hook_payload,
                action="spawned",
            )
        if normalized == "subagent_stop":
            return self._handle_subagent_event(
                event_name=event_name,
                payload=hook_payload,
                action="terminated",
            )
        raise HookError(f"Unsupported normalized hook event: {normalized}")

    def _handle_status_event(
        self,
        *,
        status: str,
        event_name: str,
        payload: dict[str, Any],
        message: str | None,
        active: bool,
        remove_keys: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        snapshot = self.workspace.write_status(
            self.agent_name,
            remove_keys=remove_keys,
            status=status,
            message=message,
            active=active,
            hook_event=normalize_hook_event(event_name),
        )
        event = self.workspace.append_event(
            self.agent_name,
            "status",
            status=status,
            message=message,
            source="hook",
            hook_event=event_name,
        )
        return {
            "handled": True,
            "normalized_event": normalize_hook_event(event_name),
            "status": snapshot,
            "event": event,
        }

    def _check_block(self, event_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Enforce .aip/block on a PreToolUse command (spec §"Hook Guard Patterns").

        On match: write status ``{status: blocked, reason: dangerous_command}``,
        emit a ``DENY`` event, and return a handled result carrying a non-zero
        ``exit_code`` so native hooks can refuse the command. Returns None when
        nothing matches.
        """
        if not self._block_rules:
            return None
        command = _extract_command(payload)
        rule = match_block(command, self._block_rules)
        if rule is None:
            return None

        tool_name = _first_non_empty(payload, "tool", "tool_name", "name") or "bash"
        snapshot = self.workspace.write_status(
            self.agent_name,
            status="blocked",
            reason="dangerous_command",
            current_tool=tool_name,
            last_tool_status="blocked",
            active=True,
            hook_event=normalize_hook_event(event_name),
        )
        event = self.workspace.append_event(
            self.agent_name,
            "DENY",
            tool=tool_name,
            status="blocked",
            reason="dangerous_command",
            data={"command": command, "matched": rule.reason},
            source="hook",
            hook_event=event_name,
        )
        return {
            "handled": True,
            "blocked": True,
            "exit_code": 1,
            "normalized_event": normalize_hook_event(event_name),
            "status": snapshot,
            "event": event,
        }

    def _incoming_chain_depth(self) -> int:
        """Read provenance.chain_depth from workspace/tasks/current.json (or 0)."""
        path = self.workspace.root / "tasks" / "current.json"
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        provenance = data.get("provenance") or {}
        try:
            return int(provenance.get("chain_depth", 0))
        except (TypeError, ValueError):
            return 0

    def _check_handoff(self, event_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Enforce contamination-aware handoff rules on write_handoff_packet.

        Applies before EXTERNAL dispatch (spec §"Contamination-Aware Handoffs").
        On failure: reject the packet to workspace/gates/{handoff_id}-rejected.json,
        emit a DENY event, and return a blocked result with exit_code 1.
        """
        tool = _first_non_empty(payload, "tool", "tool_name", "name") or ""
        if tool != "write_handoff_packet":
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        packet = payload.get("handoff_packet")
        if not isinstance(packet, dict):
            packet = data.get("handoff_packet")
        if not isinstance(packet, dict):
            packet = tool_input.get("handoff_packet")
        if not isinstance(packet, dict):
            return None  # no packet to validate
        # The guard is scoped to EXTERNAL dispatch per spec.
        if packet.get("risk_tier") != "EXTERNAL":
            return None

        incoming = payload.get("incoming_chain_depth")
        if incoming is None:
            incoming = self._incoming_chain_depth()
        ok, reason = validate_handoff_packet(packet, incoming_chain_depth=int(incoming))
        if ok:
            return None

        handoff_id = packet.get("handoff_id") or packet.get("task_id") or "handoff"
        reject_path = reject_handoff(self.workspace.root, handoff_id, reason, packet)
        rejected_ref = reject_path.relative_to(self.workspace.root.parent).as_posix()
        snapshot = self.workspace.write_status(
            self.agent_name,
            status="blocked",
            reason="handoff_rejected",
            current_tool="write_handoff_packet",
            last_tool_status="blocked",
            active=True,
            hook_event=normalize_hook_event(event_name),
        )
        event = self.workspace.append_event(
            self.agent_name,
            "DENY",
            tool="write_handoff_packet",
            status="blocked",
            reason="handoff_rejected",
            data={"handoff_id": handoff_id, "detail": reason, "rejected_ref": rejected_ref},
            source="hook",
            hook_event=event_name,
        )
        return {
            "handled": True,
            "blocked": True,
            "exit_code": 1,
            "rejected_ref": rejected_ref,
            "normalized_event": normalize_hook_event(event_name),
            "status": snapshot,
            "event": event,
        }

    def _handle_tool_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        tool_status: str,
        remove_keys: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        tool_name = _first_non_empty(
            payload,
            "tool",
            "tool_name",
            "toolName",
            "name",
            "command",
        ) or "unknown"
        message = _first_non_empty(payload, "message", "summary")
        snapshot = self.workspace.write_status(
            self.agent_name,
            remove_keys=remove_keys,
            status="working",
            current_tool=None if "current_tool" in remove_keys else tool_name,
            last_tool_status=tool_status,
            message=message,
            active=True,
            hook_event=normalize_hook_event(event_name),
        )
        event = self.workspace.append_event(
            self.agent_name,
            "tool",
            tool=tool_name,
            status=tool_status,
            message=message,
            source="hook",
            hook_event=event_name,
        )
        return {
            "handled": True,
            "normalized_event": normalize_hook_event(event_name),
            "status": snapshot,
            "event": event,
        }

    def _handle_subagent_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        child = _first_non_empty(
            payload,
            "child",
            "child_name",
            "childName",
            "agent",
            "agent_name",
            "agentName",
            "name",
            "teammate",
        )
        event = self.workspace.append_event(
            self.agent_name,
            "subagent",
            action=action,
            child=child,
            source="hook",
            hook_event=event_name,
        )
        return {
            "handled": True,
            "normalized_event": normalize_hook_event(event_name),
            "status": None,
            "event": event,
        }


def parse_hook_payload(payload_json: str | None, payload_file: str | None) -> dict[str, Any]:
    if payload_json and payload_file:
        raise HookError("Specify only one of --payload-json or --payload-file")
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise HookError(f"Invalid JSON in --payload-json: {exc}") from exc
    elif payload_file:
        try:
            with open(payload_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise HookError(f"Unable to read payload file: {payload_file}") from exc
        except json.JSONDecodeError as exc:
            raise HookError(f"Invalid JSON in payload file: {payload_file}: {exc}") from exc
    else:
        return {}

    if not isinstance(payload, dict):
        raise HookError("Hook payload must decode to a JSON object")
    return payload
