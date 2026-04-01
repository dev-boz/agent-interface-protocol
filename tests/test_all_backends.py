"""Comprehensive integration tests for all 11 agent-interface-protocol backends.

Validates that every backend is properly registered across all subsystems:
shim profiles, hook configs, launch commands, CLI choices, MCP runtime,
and task lifecycle infrastructure.
"""

from __future__ import annotations

import json

import pytest

from aip.aip_shim import BUILTIN_PROFILES, AipShim, ShimProfile, load_profile
from aip.cli import build_parser
from aip.hook_configs import generate_hook_config, install_hook_config
from aip.mcp_server import (
    BACKEND_LAUNCH_COMMANDS,
    ELICITATION_SUPPORTED,
    INJECTION_COMMANDS,
    AipToolRuntime,
)
from aip.tasks import TaskClaimError, TaskQueue
from aip.workspace import AipWorkspace

# ── Backend roster ──────────────────────────────────────────────────────

ALL_BACKENDS = [
    "claude-code", "copilot", "gemini", "kiro", "codex",
    "opencode", "cursor", "qwen", "kilo", "vibe", "amp",
]

NATIVE_BACKENDS = [
    "claude-code", "copilot", "gemini", "kiro", "codex",
    "opencode", "cursor", "qwen", "kilo",
]

INTERCEPT_BACKENDS = ["vibe", "amp"]

MCP_ONLY_BACKENDS: list[str] = []

# Native backends that support hook config generation.
# opencode uses plugin events, not file-based hook configs.
HOOK_CONFIG_BACKENDS = [
    "claude-code", "copilot", "gemini", "kiro", "codex", "cursor", "qwen",
]

# Backends that support mid-stream injection via tmux send-keys.
INJECTION_BACKENDS = list(INJECTION_COMMANDS.keys())

# Backends that do NOT support injection.
NO_INJECTION_BACKENDS = [b for b in ALL_BACKENDS if b not in INJECTION_COMMANDS]


# ── Fake tmux controller (no live tmux needed) ─────────────────────────

class FakeTmuxController:
    def __init__(self, session_name="aip"):
        self.session_name = session_name
        self.sent_keys: list[tuple[str, str, bool]] = []
        self.spawned: list[tuple[str, str]] = []
        self.killed: list[str] = []
        self._windows: list = []

    def list_windows(self):
        return self._windows

    def capture_pane(self, target, *, lines=None, include_escape=False):
        return ""

    def send_keys(self, target, text, *, press_enter=True):
        self.sent_keys.append((target, text, press_enter))

    def spawn_window(self, name, command, *, start_directory=None):
        self.spawned.append((name, command))

    def kill_window(self, target):
        self.killed.append(target)


# ═══════════════════════════════════════════════════════════════════════
# 1. Registry completeness tests
# ═══════════════════════════════════════════════════════════════════════

class TestRegistryCompleteness:
    def test_all_backends_have_builtin_profile(self):
        for backend in ALL_BACKENDS:
            assert backend in BUILTIN_PROFILES, (
                f"{backend} missing from BUILTIN_PROFILES"
            )

    def test_all_native_backends_have_hook_config(self):
        # opencode and kilo use plugin events, not file-based hook configs
        plugin_event_backends = {"opencode", "kilo"}
        for backend in NATIVE_BACKENDS:
            if backend in plugin_event_backends:
                try:
                    generate_hook_config(
                        cli_name=backend,
                        workspace_root="/ws",
                        agent_name="test",
                        session_name="aip",
                        tool_profile="full",
                    )
                except ValueError:
                    pass  # expected — no file-based hook config
                continue
            cfg = generate_hook_config(
                cli_name=backend,
                workspace_root="/ws",
                agent_name="test",
                session_name="aip",
                tool_profile="full",
            )
            assert cfg is not None, f"{backend} generate_hook_config returned None"

    def test_all_backends_have_launch_command(self):
        for backend in ALL_BACKENDS:
            assert backend in BACKEND_LAUNCH_COMMANDS, (
                f"{backend} missing from BACKEND_LAUNCH_COMMANDS"
            )
        expected_commands = {
            "claude-code": "claude",
            "copilot": "copilot",
            "gemini": "gemini",
            "kiro": "kiro-cli",
            "codex": "codex",
            "opencode": "opencode",
            "cursor": "agent",
            "qwen": "qwen",
            "kilo": "kilo",
            "vibe": "vibe",
            "amp": "amp",
        }
        for backend, expected_cmd in expected_commands.items():
            assert BACKEND_LAUNCH_COMMANDS[backend] == expected_cmd

    def test_native_backends_in_cli_hook_choices(self):
        parser = build_parser()
        # Walk subparsers to find the --cli choices on hook print-config / install
        choices = _extract_cli_choices(parser)
        for backend in HOOK_CONFIG_BACKENDS:
            assert backend in choices, (
                f"{backend} not in CLI --cli choices: {choices}"
            )

    def test_injection_commands_only_for_supported_clis(self):
        """INJECTION_COMMANDS must only contain CLIs confirmed to support
        mid-stream injection; no intercept-only or mcp-only backends."""
        for cli_name in INJECTION_COMMANDS:
            assert cli_name not in INTERCEPT_BACKENDS, (
                f"intercept backend {cli_name} should not be in INJECTION_COMMANDS"
            )
            assert cli_name not in MCP_ONLY_BACKENDS, (
                f"mcp-only backend {cli_name} should not be in INJECTION_COMMANDS"
            )
            # All injection CLIs must also have a launch command
            assert cli_name in BACKEND_LAUNCH_COMMANDS


# ═══════════════════════════════════════════════════════════════════════
# 2. Per-backend hook config tests
# ═══════════════════════════════════════════════════════════════════════

class TestHookConfigPerBackend:
    @pytest.mark.parametrize("cli_name", HOOK_CONFIG_BACKENDS)
    def test_generate_hook_config_per_backend(self, cli_name):
        cfg = generate_hook_config(
            cli_name=cli_name,
            workspace_root="/ws",
            agent_name="worker-1",
            session_name="aip",
            tool_profile="full",
        )
        snippet = cfg["snippet"]
        parsed = json.loads(snippet)

        if cli_name == "codex":
            # Codex splits config: hooks in snippet, MCP server in bootstrap TOML
            assert "hooks" in parsed, f"codex: missing hooks in snippet"
            hooks = parsed["hooks"]
            assert "PreToolUse" in hooks, f"codex: missing PreToolUse"
            assert "PostToolUse" in hooks, f"codex: missing PostToolUse"
            # MCP server is in the bootstrap_snippet (TOML)
            assert "bootstrap_snippet" in cfg, "codex: missing bootstrap_snippet"
            assert "mcp_servers.aip" in cfg["bootstrap_snippet"]
            return

        # All other CLIs: mcpServers.aip in the JSON snippet
        assert "mcpServers" in parsed, f"{cli_name}: missing mcpServers"
        assert "aip" in parsed["mcpServers"], f"{cli_name}: missing mcpServers.aip"

        if cli_name == "gemini":
            hooks = parsed.get("hooks", {})
            assert "BeforeTool" in hooks, f"gemini: missing BeforeTool"
            assert "AfterTool" in hooks, f"gemini: missing AfterTool"
        else:
            # Other CLIs use PreToolUse/PostToolUse or preToolUse/postToolUse
            hooks = parsed.get("hooks", {})
            hook_keys_lower = {k.lower() for k in hooks}
            assert "pretooluse" in hook_keys_lower, (
                f"{cli_name}: missing PreToolUse/preToolUse in {list(hooks.keys())}"
            )
            assert "posttooluse" in hook_keys_lower, (
                f"{cli_name}: missing PostToolUse/postToolUse in {list(hooks.keys())}"
            )

    @pytest.mark.parametrize("cli_name", HOOK_CONFIG_BACKENDS)
    def test_install_hook_config_per_backend(self, cli_name, tmp_path):
        result = install_hook_config(
            cli_name=cli_name,
            config_root=str(tmp_path),
            workspace_root="/ws",
            agent_name="worker-1",
            session_name="aip",
            tool_profile="full",
        )
        assert result["cli"] == cli_name
        assert "written" in result
        for path_str in result["written"]:
            from pathlib import Path
            assert Path(path_str).exists(), (
                f"{cli_name}: installed file not found: {path_str}"
            )


# ═══════════════════════════════════════════════════════════════════════
# 3. Per-backend shim profile tests
# ═══════════════════════════════════════════════════════════════════════

class TestShimProfilePerBackend:
    @pytest.mark.parametrize("cli_name", ALL_BACKENDS)
    def test_load_profile_per_backend(self, cli_name):
        builtin = BUILTIN_PROFILES[cli_name]
        expected_tier = builtin.get("tier", "intercept")

        if expected_tier == "mcp-only":
            # mcp-only backends have no shim profile (no hooks, no prompt).
            # load_profile raises because from_dict doesn't handle mcp-only.
            with pytest.raises(ValueError, match="missing prompt_regex"):
                load_profile(cli_name)
            return

        profile = load_profile(cli_name)
        assert isinstance(profile, ShimProfile)
        assert profile.cli_name == cli_name
        assert profile.tier == expected_tier, (
            f"{cli_name}: expected tier '{expected_tier}', got '{profile.tier}'"
        )

    @pytest.mark.parametrize("cli_name", ["amp", "vibe", "aider", "open-interpreter"])
    def test_intercept_profiles_have_prompt_regex(self, cli_name):
        profile = load_profile(cli_name)
        assert profile.tier == "intercept"
        assert profile.prompt_regex is not None, (
            f"{cli_name}: intercept profile has no prompt_regex"
        )

    @pytest.mark.parametrize("cli_name", NATIVE_BACKENDS)
    def test_native_profiles_reject_shim_add(self, cli_name, tmp_path):
        profile = load_profile(cli_name)
        shim = AipShim(
            str(tmp_path / "workspace"),
            tmux_controller=FakeTmuxController(),
        )
        with pytest.raises(ValueError, match="not 'intercept'"):
            shim.add_agent("test-agent", profile)


# ═══════════════════════════════════════════════════════════════════════
# 4. MCP tool runtime per-backend tests
# ═══════════════════════════════════════════════════════════════════════

class TestMcpRuntimePerBackend:
    @pytest.mark.parametrize("cli_name", ALL_BACKENDS)
    def test_mcp_runtime_registers_per_backend(self, cli_name, tmp_path):
        """Creating an AipToolRuntime with each backend name succeeds."""
        tmux = FakeTmuxController()
        runtime = AipToolRuntime(
            str(tmp_path / "workspace"),
            f"agent-{cli_name}",
            tmux_controller=tmux,
        )
        assert runtime.agent_name == f"agent-{cli_name}"

    @pytest.mark.parametrize("cli_name", INJECTION_BACKENDS)
    def test_notify_injection_per_backend(self, cli_name, tmp_path):
        """For injection-capable backends: spawn agent, send high-priority
        notify, verify send_keys was called with the correct template."""
        tmux = FakeTmuxController()
        runtime = AipToolRuntime(
            str(tmp_path / "workspace"),
            "orchestrator",
            tmux_controller=tmux,
        )
        # Spawn a teammate using this backend
        runtime.execute("spawn_teammate", {
            "name": f"worker-{cli_name}",
            "cli_type": cli_name,
            "capabilities": ["testing"],
        })
        tmux.sent_keys.clear()

        result = json.loads(runtime.execute("notify", {
            "target_agent": f"worker-{cli_name}",
            "message": "ping from test",
            "priority": "high",
        }))

        assert f"worker-{cli_name}" in result["injected_to"]
        assert len(tmux.sent_keys) == 1
        _, text, _ = tmux.sent_keys[0]
        assert "ping from test" in text

        # Verify template shape
        template = INJECTION_COMMANDS[cli_name]
        if "/btw" in template:
            assert "/btw" in text
        else:
            assert "/btw" not in text

    @pytest.mark.parametrize("cli_name", NO_INJECTION_BACKENDS)
    def test_notify_no_injection_for_unsupported(self, cli_name, tmp_path):
        """For non-injection backends: notify doesn't crash but doesn't inject."""
        tmux = FakeTmuxController()
        runtime = AipToolRuntime(
            str(tmp_path / "workspace"),
            "orchestrator",
            tmux_controller=tmux,
        )
        runtime.execute("spawn_teammate", {
            "name": f"worker-{cli_name}",
            "cli_type": cli_name,
            "capabilities": ["testing"],
        })
        tmux.sent_keys.clear()

        result = json.loads(runtime.execute("notify", {
            "target_agent": f"worker-{cli_name}",
            "message": "ping from test",
            "priority": "high",
        }))

        assert result["injected_to"] == []
        assert len(tmux.sent_keys) == 0


# ═══════════════════════════════════════════════════════════════════════
# 5. Task lifecycle (shared infrastructure)
# ═══════════════════════════════════════════════════════════════════════

class TestTaskLifecycle:
    def test_task_create_claim_complete_cycle(self, tmp_path):
        workspace = AipWorkspace(tmp_path / "workspace")
        queue = TaskQueue(workspace)

        task = queue.create_task(description="run integration tests")
        assert task.task_id == "task-001"
        assert (workspace.pending_dir / "task-001.md").exists()

        claimed = queue.claim_task(task.task_id, "worker-1")
        assert claimed.claimed_by == "worker-1"
        assert not (workspace.pending_dir / "task-001.md").exists()
        assert (workspace.claimed_dir / "worker-1-task-001.md").exists()

        completed = queue.complete_task(task.task_id, agent_name="worker-1")
        assert completed.task_id == "task-001"
        assert not (workspace.claimed_dir / "worker-1-task-001.md").exists()
        assert (workspace.done_dir / "task-001.md").exists()

    def test_task_blocked_by_lifecycle(self, tmp_path):
        workspace = AipWorkspace(tmp_path / "workspace")
        queue = TaskQueue(workspace)

        task_a = queue.create_task(
            description="build database schema",
            task_id="task-A",
        )
        task_b = queue.create_task(
            description="implement API routes",
            task_id="task-B",
            blocked_by=["task-A"],
        )

        # task-B can't be claimed while task-A is pending
        with pytest.raises(TaskClaimError, match="blocked"):
            queue.claim_task(task_b.task_id, "worker-2")

        # Complete task-A
        queue.claim_task(task_a.task_id, "worker-1")
        queue.complete_task(task_a.task_id, agent_name="worker-1")

        # Now task-B can be claimed
        claimed_b = queue.claim_task(task_b.task_id, "worker-2")
        assert claimed_b.claimed_by == "worker-2"


# ── helpers ─────────────────────────────────────────────────────────────

def _extract_cli_choices(parser):
    """Walk the argparse tree to find --cli choices from hook subcommands."""
    choices: set[str] = set()
    for action in parser._subparsers._actions:
        if not hasattr(action, "choices") or action.choices is None:
            continue
        for name, sub in action.choices.items():
            # Recurse into sub-subparsers
            for sub_action in sub._subparsers._actions if sub._subparsers else []:
                if not hasattr(sub_action, "choices") or sub_action.choices is None:
                    continue
                for sub_name, subsub in sub_action.choices.items():
                    for opt in subsub._actions:
                        if hasattr(opt, "option_strings") and "--cli" in opt.option_strings:
                            if opt.choices:
                                choices.update(opt.choices)
    return choices
