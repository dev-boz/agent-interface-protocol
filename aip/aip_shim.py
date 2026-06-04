"""Tier 2 interactive intercept shim (``aip-shim``).

For CLIs without native hooks (Amp, Aider, Open Interpreter, etc.), the shim
watches tmux pane output via ``pipe-pane``, regex-matches approval prompts,
emits standard events to the workspace, and injects responses via
``send-keys``.

Shim profiles are per-CLI YAML files that define the prompt pattern and
response keys.  Adding a new CLI to the protocol is a 5-line YAML profile.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hooks import HookRuntime
from .imx_profile import ImxProfileConstraints, check_tool_allowed, load_imx_profile
from .tmux import TmuxController, TmuxError
from .workspace import isoformat_z, utc_now

logger = logging.getLogger("aip.aip-shim")


def _extract_tool_name(command: str) -> str:
    """Best-effort tool name for a captured interactive command line.

    Strips common prompt decoration ("$ ", "> ", "Running:") and returns the
    first bare token so PreToolUse events carry a meaningful ``tool`` field
    (spec Tier 2 pending_approval shape). Falls back to ``"interactive"``.
    """
    cleaned = command.strip().lstrip("$># ").strip()
    for prefix in ("running:", "execute:", "command:", "tool:"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    token = cleaned.split(maxsplit=1)[0] if cleaned else ""
    return token.rstrip(":").lower() or "interactive"


# Built-in shim profiles.  These can be overridden by YAML files but are
# included so common CLIs work out of the box.
BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    # --- Tier 1: native hooks ---
    "claude-code": {
        "tier": "native",
    },
    "copilot": {
        "tier": "native",
    },
    "gemini": {
        "tier": "native",
    },
    "kiro": {
        "tier": "native",
    },
    "codex": {
        "tier": "native",
    },
    "opencode": {
        "tier": "native",
    },
    "kilo": {
        "tier": "native",
    },
    "cursor": {
        "tier": "native",
    },
    "qwen": {
        "tier": "native",
    },
    # --- Tier 2: interactive intercept (shim) ---
    "amp": {
        "tier": "intercept",
        "interactive_intercept": {
            "prompt_regex": r"Allow this action\? \[Y/n\]",
            "approve_keys": "y\n",
            "deny_keys": "n\n",
        },
    },
    "aider": {
        "tier": "intercept",
        "interactive_intercept": {
            "prompt_regex": r"Run this command\? \(Y/n\):",
            "approve_keys": "y\n",
            "deny_keys": "n\n",
        },
    },
    "open-interpreter": {
        "tier": "intercept",
        "interactive_intercept": {
            "prompt_regex": r"Would you like to run this code\?",
            "approve_keys": "y\n",
            "deny_keys": "n\n",
        },
    },
    "vibe": {
        "tier": "intercept",
        "interactive_intercept": {
            "prompt_regex": r"(?:Allow|Approve) (?:this action|tool execution)\?",
            "approve_keys": "y\n",
            "deny_keys": "n\n",
        },
    },
}


@dataclass
class ShimProfile:
    """Parsed shim profile for a CLI."""

    cli_name: str
    tier: str  # "native" or "intercept"
    prompt_regex: re.Pattern[str] | None = None
    approve_keys: str = "y\n"
    deny_keys: str = "n\n"

    @classmethod
    def from_dict(cls, cli_name: str, data: dict[str, Any]) -> ShimProfile:
        tier = data.get("tier", "intercept")
        if tier == "native":
            return cls(cli_name=cli_name, tier="native")
        intercept = data.get("interactive_intercept", {})
        regex_str = intercept.get("prompt_regex", "")
        if not regex_str:
            raise ValueError(f"Shim profile for {cli_name} missing prompt_regex")
        return cls(
            cli_name=cli_name,
            tier="intercept",
            prompt_regex=re.compile(regex_str),
            approve_keys=intercept.get("approve_keys", "y\n"),
            deny_keys=intercept.get("deny_keys", "n\n"),
        )


def load_profile(cli_name: str, profiles_dir: str | Path | None = None) -> ShimProfile:
    """Load a shim profile from YAML file or built-in defaults."""
    # Try external YAML file first
    if profiles_dir is not None:
        yaml_path = Path(profiles_dir) / f"{cli_name}.yaml"
        if yaml_path.exists():
            return _load_yaml_profile(cli_name, yaml_path)
        yml_path = Path(profiles_dir) / f"{cli_name}.yml"
        if yml_path.exists():
            return _load_yaml_profile(cli_name, yml_path)

    # Fall back to built-in
    if cli_name in BUILTIN_PROFILES:
        return ShimProfile.from_dict(cli_name, BUILTIN_PROFILES[cli_name])

    raise ValueError(f"No shim profile found for CLI: {cli_name}")


def _load_yaml_profile(cli_name: str, path: Path) -> ShimProfile:
    """Load a profile from a YAML file.

    Uses a simple key: value parser to avoid requiring PyYAML as a dependency.
    """
    data = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    return ShimProfile.from_dict(cli_name, data)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML-like parser for shim profile files.

    Handles top-level keys and one level of nesting (indented keys).
    Sufficient for the shim profile format without requiring PyYAML.
    """
    result: dict[str, Any] = {}
    current_section: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if indent == 0 and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                # Strip quotes
                if (value.startswith("'") and value.endswith("'")) or \
                   (value.startswith('"') and value.endswith('"')):
                    value = value[1:-1]
                result[key] = value
                current_section = None
            else:
                result[key] = {}
                current_section = key
        elif indent > 0 and current_section is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if (value.startswith("'") and value.endswith("'")) or \
               (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
            # Handle escape sequences in values
            value = value.replace("\\n", "\n").replace("\\t", "\t")
            if isinstance(result[current_section], dict):
                result[current_section][key] = value

    return result


# BlockRule / loaders live in aip.block so the native hook path (aip.hooks)
# can share them without a circular import. Re-exported here for compatibility.
from .block import (  # noqa: E402,F401
    BlockRule,
    load_block_rules,
    load_block_rules_from_file,
    load_escalate_rules,
    match_block,
)
from .gates import gate_ref, open_gate_path, read_gate, resolve_gate, write_gate  # noqa: E402


@dataclass
class ShimState:
    """Runtime state for a shim watching one agent pane."""

    agent_name: str
    profile: ShimProfile
    last_pane_content: str = ""
    pending_prompt: bool = False


class AipShim:
    """Interactive intercept shim for Tier 2 CLIs.

    Watches a tmux pane for approval prompts, emits events to the workspace,
    and injects approve/deny responses.
    """

    def __init__(
        self,
        workspace_root: str,
        session_name: str = "aip",
        *,
        tmux_controller: TmuxController | None = None,
        poll_interval: float = 0.3,
        auto_approve: bool = True,
        block_rules: list[BlockRule] | None = None,
        escalate_rules: list[BlockRule] | None = None,
        imx_profile: ImxProfileConstraints | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.session_name = session_name
        self.tmux = tmux_controller or TmuxController(session_name=session_name)
        self.poll_interval = poll_interval
        self.auto_approve = auto_approve
        self.block_rules = block_rules or []
        # Commands matching an escalation rule require a human gate (block-and-
        # wait) instead of auto-decision (spec §"Human escalation shims").
        self.escalate_rules = escalate_rules or []
        self.imx_profile = imx_profile
        self._states: dict[str, ShimState] = {}
        self._running = False

    @classmethod
    def from_workspace(
        cls,
        workspace_root: str,
        session_name: str = "aip",
        *,
        poll_interval: float = 0.3,
        auto_approve: bool = True,
        imx_profile_name: str | None = None,
    ) -> "AipShim":
        """Create an AipShim with block/escalation rules from .aip/ if present."""
        block_rules = load_block_rules(workspace_root)
        escalate_rules = load_escalate_rules(workspace_root)
        imx_profile: ImxProfileConstraints | None = None
        if imx_profile_name is not None:
            imx_profile = load_imx_profile(imx_profile_name)
            logger.info("Loaded IMX profile %r (loaded=%s)", imx_profile_name, imx_profile.loaded)
        return cls(workspace_root, session_name, poll_interval=poll_interval,
                   auto_approve=auto_approve, block_rules=block_rules,
                   escalate_rules=escalate_rules, imx_profile=imx_profile)

    def add_agent(self, agent_name: str, profile: ShimProfile) -> None:
        """Register an agent to watch."""
        if profile.tier != "intercept":
            raise ValueError(f"Profile for {profile.cli_name} is tier '{profile.tier}', not 'intercept'. "
                             f"Use native hooks or MCP tools instead.")
        self._states[agent_name] = ShimState(agent_name=agent_name, profile=profile)

    def check_once(self, agent_name: str, pane_content: str | None = None) -> dict[str, Any] | None:
        """Check a single agent's pane for approval prompts.

        Returns event dict if a prompt was detected and handled, None otherwise.
        """
        state = self._states.get(agent_name)
        if state is None:
            raise ValueError(f"Agent not registered with shim: {agent_name}")

        # An open human gate takes precedence: resolve it (inject the decision)
        # or keep the agent blocked until a human writes approved to the file.
        gate_result = self._resolve_gate_if_open(agent_name, state)
        if gate_result is not None:
            return gate_result

        if pane_content is None:
            try:
                pane_content = self.tmux.capture_pane(agent_name, lines=10)
            except TmuxError:
                return None

        if state.profile.prompt_regex is None:
            return None

        # Only look at new content since last check
        new_content = pane_content
        if state.last_pane_content and pane_content.startswith(state.last_pane_content):
            new_content = pane_content[len(state.last_pane_content):]
        state.last_pane_content = pane_content

        match = state.profile.prompt_regex.search(new_content)
        if match is None:
            return None

        # Detected an approval prompt — emit PreToolUse event
        runtime = HookRuntime(self.workspace_root, agent_name)

        # Extract any command context from the pane
        matched_text = match.group(0)
        context_lines = new_content[:match.start()].strip().splitlines()
        tool_context = context_lines[-1].strip() if context_lines else ""
        command = tool_context or matched_text
        tool_name = _extract_tool_name(command)

        # Emit the PreToolUse event in the spec's pending_approval shape
        # (Tier 2 — Interactive Intercept): the orchestrator and .aip/block
        # rules read {tool, data.command} to evaluate the action.
        runtime.workspace.write_status(
            agent_name,
            status="working",
            current_tool=tool_name,
            last_tool_status="pending_approval",
            active=True,
            hook_event="pre_tool_use",
        )
        runtime.workspace.append_event(
            agent_name,
            "PreToolUse",
            status="pending_approval",
            tool=tool_name,
            data={"command": command},
            prompt_matched=matched_text,
            source="shim",
        )

        # Human-escalation gate: a command matching an escalation rule must not
        # be auto-decided. Write a gate record, emit a human_gate event, and
        # leave the agent blocked until a human resolves it (spec §"Human
        # escalation shims").
        if match_block(command, self.escalate_rules) is not None:
            gate_path = write_gate(self.workspace_root, agent_name, tool=tool_name, command=command)
            ref = gate_ref(self.workspace_root, gate_path)
            runtime.workspace.write_status(
                agent_name,
                status="needs-input",
                current_tool=tool_name,
                last_tool_status="human_gate",
                active=True,
                hook_event="pre_tool_use",
            )
            runtime.workspace.append_event(
                agent_name,
                "human_gate",
                tool=tool_name,
                command=command,
                requires_approval=True,
                gate_ref=ref,
                source="shim",
            )
            return {
                "agent": agent_name,
                "action": "gated",
                "gate_ref": ref,
                "prompt": matched_text,
                "ts": isoformat_z(utc_now()),
            }

        # Decide approve or deny
        approved = self._should_approve(agent_name, tool_context, matched_text)

        if approved:
            keys = state.profile.approve_keys
            action = "approved"
        else:
            keys = state.profile.deny_keys
            action = "denied"

        # Inject the response
        try:
            self.tmux.send_keys(agent_name, keys, press_enter=False)
        except TmuxError as exc:
            logger.warning("Failed to inject %s response for %s: %s", action, agent_name, exc)

        # Emit PostToolUse event reflecting the injected decision
        runtime.workspace.write_status(
            agent_name,
            remove_keys=("current_tool",),
            status="working",
            last_tool_status=action,
            active=True,
            hook_event="post_tool_use",
        )
        runtime.workspace.append_event(
            agent_name,
            "PostToolUse",
            status=action,
            tool=tool_name,
            data={"command": command},
            source="shim",
        )

        return {
            "agent": agent_name,
            "action": action,
            "prompt": matched_text,
            "context": tool_context,
            "ts": isoformat_z(utc_now()),
        }

    def _resolve_gate_if_open(self, agent_name: str, state: "ShimState") -> dict[str, Any] | None:
        """Resolve an open human gate, or signal the agent is still blocked.

        Returns None when there is no open gate (caller proceeds normally),
        a ``waiting`` result while the gate is unresolved, or an
        ``approved``/``denied`` result once a human sets ``approved`` — at which
        point the stored decision is injected into the agent's pane.
        """
        gate_path = open_gate_path(self.workspace_root, agent_name)
        if gate_path is None:
            return None

        record = read_gate(gate_path) or {}
        ref = gate_ref(self.workspace_root, gate_path)
        if record.get("approved") is None:
            return {
                "agent": agent_name,
                "action": "waiting",
                "gate_ref": ref,
                "ts": isoformat_z(utc_now()),
            }

        approved = bool(record.get("approved"))
        resolve_gate(gate_path, approved=approved)
        tool_name = record.get("tool", "interactive")
        command = record.get("command", "")
        keys = state.profile.approve_keys if approved else state.profile.deny_keys
        action = "approved" if approved else "denied"
        try:
            self.tmux.send_keys(agent_name, keys, press_enter=False)
        except TmuxError as exc:
            logger.warning("Failed to inject %s response for %s: %s", action, agent_name, exc)

        runtime = HookRuntime(self.workspace_root, agent_name)
        runtime.workspace.write_status(
            agent_name,
            remove_keys=("current_tool",),
            status="working",
            last_tool_status=action,
            active=True,
            hook_event="post_tool_use",
        )
        runtime.workspace.append_event(
            agent_name,
            "PostToolUse",
            status=action,
            tool=tool_name,
            data={"command": command},
            via="human_gate",
            source="shim",
        )
        return {
            "agent": agent_name,
            "action": action,
            "gate_ref": ref,
            "ts": isoformat_z(utc_now()),
        }

    def _should_approve(self, agent_name: str, context: str, prompt: str) -> bool:
        """Decide whether to approve or deny an action."""
        # Check block rules first
        combined = f"{context}\n{prompt}"
        for rule in self.block_rules:
            if rule.pattern.search(combined):
                logger.info("Blocked %s action for %s: %s", agent_name, rule.reason, context[:80])
                return False
        if self.imx_profile is not None and self.imx_profile.loaded:
            tool_name = context.split()[0] if context else ""
            allowed, reason = check_tool_allowed(tool_name, self.imx_profile)
            if not allowed:
                logger.info("IMX profile denied %s: %s", tool_name, reason)
                return False
        return self.auto_approve

    def poll_all(self) -> list[dict[str, Any]]:
        """Check all registered agents once. Returns list of handled events."""
        events: list[dict[str, Any]] = []
        for agent_name in list(self._states):
            result = self.check_once(agent_name)
            if result is not None:
                events.append(result)
        return events

    def run(self, *, max_iterations: int | None = None) -> None:
        """Run the shim poll loop.

        Args:
            max_iterations: Stop after this many iterations (None = run forever).
        """
        self._running = True
        iteration = 0
        while self._running:
            if max_iterations is not None and iteration >= max_iterations:
                break
            self.poll_all()
            time.sleep(self.poll_interval)
            iteration += 1

    def stop(self) -> None:
        """Stop the poll loop."""
        self._running = False
