import json
import io
import sys

from aip.cli import main
from aip.tmux import TmuxError
from aip.workspace import AipWorkspace
from aip.tasks import TaskQueue


def test_init_command_creates_workspace(tmp_path, capsys):
    exit_code = main(["--workspace-root", str(tmp_path / "workspace"), "init"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["workspace"] == str(tmp_path / "workspace")
    assert (tmp_path / "workspace" / "tasks" / "pending").exists()


def test_hook_emit_command_writes_status_event(tmp_path, capsys):
    exit_code = main(
        [
            "--workspace-root",
            str(tmp_path / "workspace"),
            "hook",
            "emit",
            "--agent-name",
            "coder",
            "--event",
            "SessionStart",
            "--payload-json",
            '{"message":"starting"}',
        ]
    )

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["normalized_event"] == "session_start"
    assert captured["status"]["status"] == "working"


def test_hook_proxy_reads_stdin_and_emits_empty_json_for_gemini(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name":"BeforeTool","tool_name":"read_file"}'))

    exit_code = main(
        [
            "--workspace-root",
            str(tmp_path / "workspace"),
            "hook",
            "proxy",
            "--agent-name",
            "coder",
            "--output-mode",
            "json-empty",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "{}\n"


def test_hook_print_config_outputs_profiled_snippet(tmp_path, capsys):
    exit_code = main(
        [
            "--workspace-root",
            str(tmp_path / "workspace"),
            "hook",
            "print-config",
            "--cli",
            "gemini",
            "--agent-name",
            "coder",
            "--tool-profile",
            "worker",
        ]
    )

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["cli"] == "gemini"
    assert '"--tool-profile"' in captured["snippet"]


def test_hook_install_writes_codex_config_files(tmp_path, capsys):
    config_root = tmp_path / "repo"

    exit_code = main(
        [
            "--workspace-root",
            str(tmp_path / "workspace"),
            "hook",
            "install",
            "--cli",
            "codex",
            "--agent-name",
            "architect",
            "--tool-profile",
            "architect",
            "--config-root",
            str(config_root),
        ]
    )

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["cli"] == "codex"
    assert (config_root / ".codex" / "hooks.json").exists()
    assert (config_root / ".codex" / "config.toml").exists()


class FakeTmuxController:
    def __init__(self, session_name="aip"):
        self.session_name = session_name
        self.killed = []
        self.kill_failures = set()

    def capture_pane(self, target, *, lines=None, include_escape=False):
        return f"{target} is mid-task"

    def kill_window(self, target):
        if target in self.kill_failures:
            raise TmuxError(f"kill failed for {target}")
        self.killed.append(target)


def test_agent_kill_requeues_claimed_tasks_and_removes_descendants(tmp_path, capsys, monkeypatch):
    fake_tmux = FakeTmuxController()
    monkeypatch.setattr("aip.cli.TmuxController", lambda session_name="aip": fake_tmux)

    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure_agent_node("orchestrator", depth=0, parent=None, tmux_window="aip:orchestrator")
    workspace.add_agent_child("orchestrator", "coder", depth=1, tmux_window="aip:coder")
    queue = TaskQueue(workspace)
    task = queue.create_task(description="finish feature")
    queue.claim_task(task.task_id, "coder")

    exit_code = main(["--workspace-root", str(tmp_path / "workspace"), "agent", "kill", "orchestrator"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [item["agent"] for item in captured["killed"]] == ["coder", "orchestrator"]
    assert fake_tmux.killed == ["coder", "orchestrator"]
    assert (workspace.pending_dir / "task-001.md").exists()
    assert workspace.read_agent_tree() == {}

    pending_text = (workspace.pending_dir / "task-001.md").read_text(encoding="utf-8")
    assert "handoff_summary:" in pending_text

    events = workspace.tail_events(limit=10)
    shutdown_events = [event for event in events if event["event"] == "shutdown"]
    assert len(shutdown_events) == 4


def test_task_create_command_writes_pending_file_and_returns_task(tmp_path, capsys):
    """aip task create creates a task in pending/ and returns the task JSON."""
    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "task", "create",
        "--description", "implement oauth login",
        "--task-type", "coding",
        "--priority", "high",
        "--target-role", "coder",
    ])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["task_id"] == "task-001"
    assert captured["description"] == "implement oauth login"
    assert (tmp_path / "workspace" / "tasks" / "pending" / "task-001.md").exists()


def test_task_create_with_blocked_by_and_explicit_id(tmp_path, capsys):
    """aip task create accepts --blocked-by and --task-id flags."""
    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "task", "create",
        "--description", "add tests",
        "--blocked-by", "task-001",
        "--task-id", "task-002",
    ])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["task_id"] == "task-002"
    assert "task-001" in captured["blocked_by"]
    task_text = (tmp_path / "workspace" / "tasks" / "pending" / "task-002.md").read_text(encoding="utf-8")
    assert "blocked_by: task-001" in task_text


def test_activity_derive_command_returns_5_state(tmp_path, capsys):
    """aip activity derive returns the computed state JSON."""
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_presence("coder")

    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "activity", "derive", "coder",
    ])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["agent"] == "coder"
    assert captured["state"] in {"running", "needs-input", "stalled", "idle", "done"}
    assert "derived_at" in captured


def test_activity_derive_with_write_creates_activity_file(tmp_path, capsys):
    """aip activity derive --write persists {agent}-activity.json."""
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()

    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "activity", "derive", "coder", "--write",
    ])

    assert exit_code == 0
    activity_path = tmp_path / "workspace" / "status" / "coder-activity.json"
    assert activity_path.exists()
    data = json.loads(activity_path.read_text(encoding="utf-8"))
    assert data["agent"] == "coder"
    assert data["state"] in {"running", "needs-input", "stalled", "idle", "done"}


def test_agent_kill_preserves_tree_node_on_kill_failure(tmp_path, capsys, monkeypatch):
    fake_tmux = FakeTmuxController()
    fake_tmux.kill_failures.add("coder")
    monkeypatch.setattr("aip.cli.TmuxController", lambda session_name="aip": fake_tmux)

    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure_agent_node("orchestrator", depth=0, parent=None, tmux_window="aip:orchestrator")
    workspace.add_agent_child("orchestrator", "coder", depth=1, tmux_window="aip:coder")

    exit_code = main(["--workspace-root", str(tmp_path / "workspace"), "agent", "kill", "orchestrator"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0

    # coder kill failed → node should remain in tree
    coder_result = next(r for r in captured["killed"] if r["agent"] == "coder")
    assert coder_result["killed"] is False

    tree = workspace.read_agent_tree()
    assert "coder" in tree

    # orchestrator kill succeeded → node should be removed
    assert "orchestrator" not in tree


def _write_imx_profile(catalog_dir, name, risk_tiers):
    profiles_dir = catalog_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    lines = ["risk_tiers:"] + [f"  - {tier}" for tier in risk_tiers]
    (profiles_dir / f"{name}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_imx_profile_list_returns_sorted_profiles(tmp_path, capsys):
    catalog = tmp_path / "catalog"
    _write_imx_profile(catalog, "worker", ["READ_ONLY", "LOCAL"])
    _write_imx_profile(catalog, "auditor", ["READ_ONLY"])

    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "imx-profile", "list", "--catalog-dir", str(catalog),
    ])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["profiles"] == ["auditor", "worker"]


def test_imx_profile_check_risk_allowed(tmp_path, capsys):
    catalog = tmp_path / "catalog"
    _write_imx_profile(catalog, "worker", ["READ_ONLY", "LOCAL"])

    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "imx-profile", "check-risk", "worker", "LOCAL", "--catalog-dir", str(catalog),
    ])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["allowed"] is True
    assert captured["profile_loaded"] is True


def test_imx_profile_check_risk_denied_exits_nonzero(tmp_path, capsys):
    catalog = tmp_path / "catalog"
    _write_imx_profile(catalog, "worker", ["READ_ONLY", "LOCAL"])

    exit_code = main([
        "--workspace-root", str(tmp_path / "workspace"),
        "imx-profile", "check-risk", "worker", "EXTERNAL", "--catalog-dir", str(catalog),
    ])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert captured["allowed"] is False
    assert "EXTERNAL" in captured["reason"]
