import json

from atmux.cli import main
from atmux.tmux import TmuxError
from atmux.workspace import AtmuxWorkspace
from atmux.tasks import TaskQueue


def test_init_command_creates_workspace(tmp_path, capsys):
    exit_code = main(["--workspace-root", str(tmp_path / "workspace"), "init"])

    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["workspace"] == str(tmp_path / "workspace")
    assert (tmp_path / "workspace" / "tasks" / "pending").exists()


class FakeTmuxController:
    def __init__(self, session_name="atmux"):
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
    monkeypatch.setattr("atmux.cli.TmuxController", lambda session_name="atmux": fake_tmux)

    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure_agent_node("orchestrator", depth=0, parent=None, tmux_window="atmux:orchestrator")
    workspace.add_agent_child("orchestrator", "coder", depth=1, tmux_window="atmux:coder")
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


def test_agent_kill_preserves_tree_node_on_kill_failure(tmp_path, capsys, monkeypatch):
    fake_tmux = FakeTmuxController()
    fake_tmux.kill_failures.add("coder")
    monkeypatch.setattr("atmux.cli.TmuxController", lambda session_name="atmux": fake_tmux)

    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure_agent_node("orchestrator", depth=0, parent=None, tmux_window="atmux:orchestrator")
    workspace.add_agent_child("orchestrator", "coder", depth=1, tmux_window="atmux:coder")

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
