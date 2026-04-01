"""Integration test: multi-backend collaboration via the agent-interface-protocol framework.

Exercises workspace, task queue, hook config generation, MCP tool runtime,
shim profiles, and cross-agent notification for all 11 supported backends
without launching real AI agents.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from aip.aip_shim import BUILTIN_PROFILES, load_profile
from aip.hook_configs import generate_hook_config, install_hook_config
from aip.mcp_server import INJECTION_COMMANDS, AipToolRuntime
from aip.tasks import TaskQueue
from aip.workspace import AipWorkspace


# ---------------------------------------------------------------------------
# Backend catalogue
# ---------------------------------------------------------------------------

BACKENDS = [
    {"name": "claude-code", "cli": "claude-code", "launch": "claude --model haiku", "tier": "native"},
    {"name": "copilot", "cli": "copilot", "launch": "copilot --model 'GPT-5 mini'", "tier": "native"},
    {"name": "gemini", "cli": "gemini", "launch": "gemini --model gemini-3-flash-preview", "tier": "native"},
    {"name": "kiro", "cli": "kiro", "launch": "kiro-cli", "tier": "native"},
    {"name": "codex", "cli": "codex", "launch": "codex --model gpt-5.1-codex-mini", "tier": "native"},
    {"name": "opencode", "cli": "opencode", "launch": "opencode --model minimax/MiniMax-M1-80k", "tier": "native"},
    {"name": "cursor", "cli": "cursor", "launch": "agent --model auto", "tier": "native"},
    {"name": "qwen", "cli": "qwen", "launch": "qwen", "tier": "native"},
    {"name": "kilo", "cli": "kilo", "launch": "kilo --model zen/MiniMax-M1-80k", "tier": "native"},
    {"name": "vibe", "cli": "vibe", "launch": "vibe --agent auto-approve", "tier": "intercept"},
    {"name": "amp", "cli": "amp", "launch": "amp --mode smart", "tier": "intercept"},
]

HOOK_SUPPORTED_CLIS = ["claude-code", "copilot", "gemini", "kiro", "codex", "cursor", "qwen"]


# ---------------------------------------------------------------------------
# FakeTmuxController
# ---------------------------------------------------------------------------

@dataclass
class FakeWindow:
    name: str
    index: int = 0
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "index": self.index, "active": self.active}


class FakeTmuxController:
    def __init__(self, session_name: str = "aip-test") -> None:
        self.session_name = session_name
        self.sent_keys: list[tuple[str, str, bool]] = []
        self.spawned: list[tuple[str, str]] = []
        self.killed: list[str] = []
        self._windows: list[FakeWindow] = []
        self._pane_content: dict[str, str] = {}

    def list_windows(self) -> list[FakeWindow]:
        return self._windows

    def ensure_session(self, **kwargs: Any) -> bool:
        return True

    def capture_pane(self, target: str, *, lines: int | None = None, include_escape: bool = False) -> str:
        return self._pane_content.get(target, "")

    def send_keys(self, target: str, text: str, *, press_enter: bool = True) -> None:
        self.sent_keys.append((target, text, press_enter))

    def spawn_window(self, name: str, command: str, *, start_directory: str | None = None) -> None:
        self.spawned.append((name, command))
        self._windows.append(FakeWindow(name=name, index=len(self._windows)))

    def kill_window(self, target: str) -> None:
        self.killed.append(target)
        self._windows = [w for w in self._windows if w.name != target]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def collab_env(tmp_path: Path):
    session_name = f"aip-test-{uuid.uuid4().hex[:8]}"
    ws_root = tmp_path / "workspace"
    workspace = AipWorkspace(ws_root)
    workspace.ensure()
    queue = TaskQueue(workspace)
    tmux = FakeTmuxController(session_name=session_name)
    tmux.ensure_session()

    yield workspace, queue, tmux, session_name

    # No real tmux session to tear down — FakeTmuxController is in-memory only.


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkspaceInitAllBackends:
    def test_workspace_init_all_backends(self, collab_env: tuple) -> None:
        workspace: AipWorkspace
        workspace, _queue, _tmux, session_name = collab_env

        for backend in BACKENDS:
            workspace.ensure_agent_node(
                backend["name"],
                depth=1,
                parent=None,
                tmux_window=f"{session_name}:{backend['name']}",
                cli_type=backend["launch"],
            )
            workspace.write_status(
                backend["name"],
                status="idle",
                cli_type=backend["launch"],
            )

        tree = workspace.read_agent_tree()
        assert len(tree) == 11, f"Expected 11 agents, got {len(tree)}: {list(tree)}"
        for backend in BACKENDS:
            assert backend["name"] in tree


class TestHookConfigGenerationAllTier1:
    def test_hook_config_generation_all_tier1(self, collab_env: tuple) -> None:
        workspace: AipWorkspace
        workspace, _queue, _tmux, session_name = collab_env

        for cli_name in HOOK_SUPPORTED_CLIS:
            result = generate_hook_config(
                cli_name=cli_name,
                workspace_root=str(workspace.root),
                agent_name=f"test-{cli_name}",
                session_name=session_name,
                tool_profile="worker",
            )

            assert result["cli"] == cli_name
            snippet = json.loads(result["snippet"])
            assert "mcpServers" in snippet or "hooks" in snippet, (
                f"{cli_name}: snippet must have mcpServers or hooks"
            )

            install_result = install_hook_config(
                cli_name=cli_name,
                config_root=str(workspace.root / cli_name),
                workspace_root=str(workspace.root),
                agent_name=f"test-{cli_name}",
                session_name=session_name,
                tool_profile="worker",
            )

            assert "written" in install_result
            for written_path in install_result["written"]:
                assert Path(written_path).exists(), f"{cli_name}: {written_path} not created"


class TestMcpRuntimePerBackend:
    def test_mcp_runtime_per_backend(self, collab_env: tuple) -> None:
        workspace: AipWorkspace
        workspace, _queue, _tmux, session_name = collab_env

        for backend in BACKENDS:
            fake_tmux = FakeTmuxController(session_name=session_name)
            runtime = AipToolRuntime(
                str(workspace.root),
                backend["name"],
                session_name=session_name,
                tmux_controller=fake_tmux,
                tool_profile="full",
            )

            result_json = runtime.execute(
                "report_status",
                {"status": "idle", "message": "Backend initialized"},
            )
            result = json.loads(result_json)
            assert result["agent"] == backend["name"]
            assert result["status"] == "idle"

            status = workspace.read_status(backend["name"])
            assert status["status"] == "idle"
            assert status["message"] == "Backend initialized"


class TestTaskDistributionAcrossBackends:
    def test_task_distribution_across_backends(self, collab_env: tuple) -> None:
        workspace: AipWorkspace
        workspace, queue, _tmux, _session_name = collab_env

        for backend in BACKENDS:
            task_id = f"task-{backend['name']}"
            queue.create_task(
                description=f"Test task for {backend['name']}",
                task_id=task_id,
                requested_by="orchestrator",
            )

        for backend in BACKENDS:
            task_id = f"task-{backend['name']}"
            queue.claim_task(task_id, backend["name"])

        claimed = queue.list_tasks(stage="claimed")
        assert len(claimed) == 11, f"Expected 11 claimed tasks, got {len(claimed)}"

        for backend in BACKENDS:
            task_id = f"task-{backend['name']}"
            queue.complete_task(task_id, agent_name=backend["name"])

        done = queue.list_tasks(stage="done")
        assert len(done) == 11, f"Expected 11 done tasks, got {len(done)}"

        for task in done:
            assert task.claimed_by is not None


class TestTaskDependencyChain:
    def test_task_dependency_chain(self, collab_env: tuple) -> None:
        workspace: AipWorkspace
        workspace, queue, _tmux, _session_name = collab_env

        queue.create_task(
            description="Base task — no dependencies",
            task_id="task-base",
            requested_by="orchestrator",
        )
        queue.create_task(
            description="Mid task — depends on base",
            task_id="task-mid",
            requested_by="orchestrator",
            blocked_by=["task-base"],
        )
        queue.create_task(
            description="Final task — depends on mid",
            task_id="task-final",
            requested_by="orchestrator",
            blocked_by=["task-mid"],
        )

        claimable = queue.list_claimable_tasks()
        claimable_ids = [t.task_id for t in claimable]
        assert "task-base" in claimable_ids
        assert "task-mid" not in claimable_ids
        assert "task-final" not in claimable_ids

        queue.claim_task("task-base", "worker-claude")
        queue.complete_task("task-base", agent_name="worker-claude")

        claimable = queue.list_claimable_tasks()
        claimable_ids = [t.task_id for t in claimable]
        assert "task-mid" in claimable_ids
        assert "task-final" not in claimable_ids

        queue.claim_task("task-mid", "worker-gemini")
        queue.complete_task("task-mid", agent_name="worker-gemini")

        claimable = queue.list_claimable_tasks()
        claimable_ids = [t.task_id for t in claimable]
        assert "task-final" in claimable_ids

        queue.claim_task("task-final", "worker-codex")
        queue.complete_task("task-final", agent_name="worker-codex")

        done = queue.list_tasks(stage="done")
        done_ids = {t.task_id for t in done}
        assert done_ids == {"task-base", "task-mid", "task-final"}


class TestCrossAgentNotify:
    def test_cross_agent_notify(self, collab_env: tuple) -> None:
        workspace: AipWorkspace
        workspace, _queue, _tmux, session_name = collab_env

        fake_tmux = FakeTmuxController(session_name=session_name)

        workers = [
            ("worker-gemini", "gemini"),
            ("worker-codex", "codex"),
            ("worker-cursor", "cursor"),
        ]

        # Register orchestrator in agent tree
        workspace.ensure_agent_node(
            "orchestrator",
            depth=0,
            parent=None,
            tmux_window=f"{session_name}:orchestrator",
            cli_type="claude",
        )

        for agent_name, cli_type in workers:
            workspace.add_agent_child(
                "orchestrator",
                agent_name,
                depth=1,
                tmux_window=f"{session_name}:{agent_name}",
                cli_type=cli_type,
            )
            fake_tmux._windows.append(
                FakeWindow(name=agent_name, index=len(fake_tmux._windows)),
            )

        runtime = AipToolRuntime(
            str(workspace.root),
            "orchestrator",
            session_name=session_name,
            tmux_controller=fake_tmux,
            tool_profile="full",
        )

        for agent_name, cli_type in workers:
            result_json = runtime.notify(
                target_agent=agent_name,
                message="Please start your assigned work",
                priority="high",
            )
            result = json.loads(result_json)
            assert result["sent"] is True

            if cli_type in INJECTION_COMMANDS:
                assert agent_name in result["injected_to"]

        # Verify send_keys was called for each injectable backend
        sent_targets = [target for target, _text, _enter in fake_tmux.sent_keys]
        for agent_name, cli_type in workers:
            if cli_type in INJECTION_COMMANDS:
                assert agent_name in sent_targets, (
                    f"Expected send_keys for {agent_name} (cli={cli_type})"
                )

        # Verify injection text includes proper template
        for target, text, _enter in fake_tmux.sent_keys:
            assert "orchestrator says:" in text
            assert "Please start your assigned work" in text


class TestShimProfileCoverage:
    def test_shim_profile_coverage(self) -> None:
        # Tier 2 intercept: amp and vibe
        for cli_name in ("amp", "vibe"):
            profile = load_profile(cli_name)
            assert profile.tier == "intercept", f"{cli_name} should be intercept"
            assert profile.prompt_regex is not None, f"{cli_name} must have prompt_regex"

        # Kilo is a fork of opencode — same native tier
        profile = load_profile("kilo")
        assert profile.tier == "native", "kilo should be native (opencode fork)"

        # All native backends
        native_clis = [b["cli"] for b in BACKENDS if b["tier"] == "native"]
        for cli_name in native_clis:
            profile = load_profile(cli_name)
            assert profile.tier == "native", f"{cli_name} should be native, got {profile.tier}"

    def test_all_11_backends_have_builtin_profiles(self) -> None:
        for backend in BACKENDS:
            assert backend["cli"] in BUILTIN_PROFILES, (
                f"{backend['cli']} missing from BUILTIN_PROFILES"
            )
