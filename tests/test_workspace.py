import json
import threading

from atmux.workspace import AtmuxWorkspace, atomic_write_text


def test_workspace_creates_layout_and_status_files(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure()

    assert workspace.events_path.exists()
    assert workspace.agent_tree_path.exists()
    assert workspace.pending_dir.exists()
    assert workspace.claimed_dir.exists()
    assert workspace.done_dir.exists()
    assert workspace.failed_dir.exists()

    snapshot = workspace.write_status("coder", status="working", message="starting")
    assert snapshot["status"] == "working"
    assert snapshot["message"] == "starting"

    updated = workspace.write_status("coder", remove_keys=("message",), progress="1 of 3")
    assert updated["status"] == "working"
    assert updated["progress"] == "1 of 3"
    assert "message" not in updated


def test_workspace_events_and_summary_exports(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    summary_path = workspace.export_summary("reviewer", "## done")
    event = workspace.append_event("reviewer", "status", status="finished")

    assert summary_path.exists()
    assert summary_path.read_text(encoding="utf-8") == "## done\n"
    assert event["event"] == "status"

    tail = workspace.tail_events(limit=5)
    assert tail == [json.loads(workspace.events_path.read_text(encoding="utf-8").strip())]


def test_next_task_id_scans_all_task_directories(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure()
    for relative in ("pending/task-001.md", "done/task-009.md", "claimed/coder-task-010.md"):
        path = workspace.tasks_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# task\n", encoding="utf-8")

    assert workspace.next_task_id() == "task-011"


def test_agent_tree_helpers_register_root_and_child(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")

    root = workspace.ensure_agent_node(
        "orchestrator",
        depth=0,
        parent=None,
        tmux_window="atmux:orchestrator",
        cli_type="claude-code",
    )
    child = workspace.add_agent_child(
        "orchestrator",
        "coder",
        depth=1,
        tmux_window="atmux:coder",
        cli_type="gemini",
    )

    assert root["depth"] == 0
    assert child["parent"] == "orchestrator"
    tree = workspace.read_agent_tree()
    assert tree["orchestrator"]["children"] == ["coder"]
    assert tree["coder"]["cli_type"] == "gemini"


def test_agent_tree_postorder_and_removal(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure_agent_node("orchestrator", depth=0, parent=None, tmux_window="atmux:orchestrator")
    workspace.add_agent_child("orchestrator", "manager", depth=1, tmux_window="atmux:manager")
    workspace.add_agent_child("manager", "worker", depth=2, tmux_window="atmux:worker")

    assert workspace.agent_subtree_postorder("orchestrator") == ["worker", "manager", "orchestrator"]

    workspace.remove_agent_node("worker")
    workspace.remove_agent_node("manager")
    tree = workspace.read_agent_tree()
    assert tree["orchestrator"]["children"] == []


def test_read_json_returns_empty_on_corrupt_file(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure()
    corrupt = workspace.root / "corrupt.json"
    corrupt.write_text("{invalid json", encoding="utf-8")
    assert workspace.read_json(corrupt) == {}


def test_tail_events_skips_corrupt_lines(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.append_event("coder", "status", status="working")
    with workspace.events_path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt line\n")
    workspace.append_event("coder", "status", status="finished")
    events = workspace.tail_events(limit=10)
    assert len(events) == 2
    assert events[0]["status"] == "working"
    assert events[1]["status"] == "finished"


def test_atomic_write_text_thread_safety(tmp_path):
    target = tmp_path / "shared.txt"
    errors: list[Exception] = []

    def writer(thread_id: int):
        try:
            for i in range(20):
                atomic_write_text(target, f"thread-{thread_id}-iter-{i}\n")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent writes failed: {errors}"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("thread-")
