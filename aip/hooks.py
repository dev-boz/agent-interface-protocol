from __future__ import annotations

import json
import re
from typing import Any

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


class HookRuntime:
    def __init__(self, workspace_root: str, agent_name: str) -> None:
        self.workspace = AipWorkspace(workspace_root)
        self.workspace.ensure()
        self.agent_name = agent_name

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
