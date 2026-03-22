import json
import threading
import time

import pytest

from atmux.mcp_server import AtmuxToolRuntime, ToolInputError, TOOL_SPECS


class FakeTmuxController:
    def __init__(self, session_name="atmux"):
        self.session_name = session_name
        self.spawn_calls = []
        self.killed = []

    def spawn_window(self, window_name, command, start_directory=None):
        self.spawn_calls.append((window_name, command, start_directory))

    def kill_window(self, target):
        self.killed.append(target)


def test_tool_specs_has_seven_tools():
    assert len(TOOL_SPECS) == 7
    names = {s["name"] for s in TOOL_SPECS}
    assert names == {
        "report_status",
        "export_summary",
        "register_capabilities",
        "request_task",
        "report_progress",
        "wait_for",
        "spawn_teammate",
    }


def test_report_status_and_request_task(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder")

    status_result = json.loads(runtime.execute("report_status", {"status": "working", "message": "starting auth"}))
    task_result = json.loads(runtime.execute("request_task", {"task_description": "review auth", "target_role": "reviewer", "priority": "high"}))

    assert status_result["status"] == "working"
    assert task_result["task_id"] == "task-001"
    assert (tmp_path / "workspace" / "tasks" / "pending" / "task-001.md").exists()


def test_export_summary(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder")
    result = json.loads(runtime.execute("export_summary", {"content": "# Review\nAll good."}))
    assert result["file"].startswith("summaries/")
    assert (tmp_path / "workspace" / result["file"]).exists()


def test_report_progress(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder")
    result = json.loads(runtime.execute("report_progress", {"progress": "halfway done", "percentage": 50}))
    assert result["progress"] == "halfway done"
    assert result["percentage"] == 50


def test_wait_for_matches_existing_event(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder", poll_interval=0.01)
    runtime.execute("report_status", {"status": "finished"})

    result = json.loads(runtime.execute("wait_for", {"event_filter": "agent:coder,status:finished", "timeout": 0.1}))

    assert result["timeout"] is False
    assert result["matched_filter"] == "agent:coder,status:finished"
    assert result["event"]["status"] == "finished"


def test_wait_for_returns_timeout_when_no_match(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder", poll_interval=0.01)

    result = json.loads(runtime.execute("wait_for", {"event_filter": "agent:reviewer,status:finished", "timeout": 0.05}))

    assert result == {"matched_filter": None, "event": None, "timeout": True}


def test_wait_for_detects_future_event(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "orchestrator", poll_interval=0.01)

    def emit_event():
        time.sleep(0.02)
        runtime.workspace.append_event("coder", "status", status="finished")

    thread = threading.Thread(target=emit_event)
    thread.start()
    try:
        result = json.loads(
            runtime.execute("wait_for", {"event_filter": ["agent:coder,status:finished", "agent:reviewer,status:finished"], "timeout": 0.2})
        )
    finally:
        thread.join()

    assert result["timeout"] is False
    assert result["matched_filter"] == "agent:coder,status:finished"
    assert result["event"]["agent"] == "coder"


def test_spawn_teammate_registers_tree_status_and_event(tmp_path):
    tmux = FakeTmuxController()
    runtime = AtmuxToolRuntime(
        str(tmp_path / "workspace"),
        "orchestrator",
        tmux_controller=tmux,
    )

    result = json.loads(
        runtime.execute(
            "spawn_teammate",
            {
                "name": "coder",
                "cli_type": "gemini",
                "capabilities": ["python", "testing"],
                "interests": {"agents": {"architect": "high"}},
            },
        )
    )

    assert result["agent_id"] == "coder"
    assert result["parent_id"] == "orchestrator"
    assert tmux.spawn_calls == [("coder", "gemini", None)]

    tree = runtime.workspace.read_agent_tree()
    assert tree["orchestrator"]["children"] == ["coder"]
    assert tree["coder"]["depth"] == 1

    status_file = tmp_path / "workspace" / "status" / "coder.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["capabilities"] == ["python", "testing"]
    assert status["interests"]["agents"]["architect"] == "high"

    events = [json.loads(line) for line in (tmp_path / "workspace" / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "spawn"
    assert events[-1]["agent_id"] == "coder"


def test_spawn_teammate_enforces_depth_and_breadth_limits(tmp_path):
    tmux = FakeTmuxController()
    runtime = AtmuxToolRuntime(
        str(tmp_path / "workspace"),
        "orchestrator",
        tmux_controller=tmux,
        max_depth=1,
        max_breadth=1,
    )

    runtime.execute("spawn_teammate", {"name": "coder", "cli_type": "gemini", "capabilities": ["python"]})

    try:
        runtime.execute("spawn_teammate", {"name": "reviewer", "cli_type": "claude-code", "capabilities": ["review"]})
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "max_breadth" in str(exc)

    child_runtime = AtmuxToolRuntime(
        str(tmp_path / "workspace"),
        "coder",
        tmux_controller=tmux,
        max_depth=1,
        max_breadth=1,
    )
    try:
        child_runtime.execute("spawn_teammate", {"name": "worker", "cli_type": "aider", "capabilities": ["fixes"]})
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "max_depth" in str(exc)


def test_register_capabilities_with_interests(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "architect")

    result = json.loads(runtime.execute("register_capabilities", {
        "capabilities": ["system-design", "api-design"],
        "interests": {
            "agents": {"coder": "high", "reviewer": "high", "researcher": "medium"},
            "events": {"status:failed": "high", "status:finished": "medium"},
            "summaries": {"architect-*": "high", "coder-*": "medium"},
        },
    }))

    assert result["capabilities"] == ["system-design", "api-design"]
    assert result["interests"]["agents"]["coder"] == "high"
    assert result["interests"]["events"]["status:failed"] == "high"

    # Verify persisted
    status_file = tmp_path / "workspace" / "status" / "architect.json"
    persisted = json.loads(status_file.read_text(encoding="utf-8"))
    assert persisted["interests"]["agents"]["researcher"] == "medium"

    # Verify event
    events_file = tmp_path / "workspace" / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
    cap_event = [e for e in events if e["event"] == "capabilities"][0]
    assert cap_event["interests"]["agents"]["coder"] == "high"


def test_register_capabilities_without_interests(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "worker")
    result = json.loads(runtime.execute("register_capabilities", {"capabilities": ["python", "testing"]}))
    assert result["capabilities"] == ["python", "testing"]
    assert "interests" not in result


def test_register_capabilities_invalid_interest_priority(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder")
    try:
        runtime.execute("register_capabilities", {
            "capabilities": ["python"],
            "interests": {"agents": {"reviewer": "urgent"}},
        })
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "Invalid priority" in str(exc)


def test_invalid_status_returns_error(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder")
    try:
        runtime.execute("report_status", {"status": "nope"})
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "Invalid status" in str(exc)


def test_unknown_tool_returns_error(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder")
    try:
        runtime.execute("nonexistent_tool", {})
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "Unknown tool" in str(exc)


def test_spawn_teammate_rolls_back_tmux_on_post_spawn_failure(tmp_path, monkeypatch):
    tmux = FakeTmuxController()
    runtime = AtmuxToolRuntime(
        str(tmp_path / "workspace"),
        "orchestrator",
        tmux_controller=tmux,
    )
    # Make add_agent_child fail after tmux window is spawned
    original = runtime.workspace.add_agent_child
    def failing_add(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(runtime.workspace, "add_agent_child", failing_add)

    with pytest.raises(OSError, match="disk full"):
        runtime.execute("spawn_teammate", {"name": "coder", "cli_type": "gemini", "capabilities": ["python"]})

    # Window was spawned then rolled back
    assert tmux.spawn_calls == [("coder", "gemini", None)]
    assert tmux.killed == ["coder"]


def test_read_events_from_offset_skips_corrupt_lines(tmp_path):
    runtime = AtmuxToolRuntime(str(tmp_path / "workspace"), "coder", poll_interval=0.01)
    runtime.execute("report_status", {"status": "working"})
    # Inject corrupt line
    with runtime.workspace.events_path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt\n")
    runtime.execute("report_status", {"status": "finished"})

    result = json.loads(runtime.execute("wait_for", {"event_filter": "status:finished", "timeout": 0.1}))
    assert result["timeout"] is False
    assert result["event"]["status"] == "finished"
