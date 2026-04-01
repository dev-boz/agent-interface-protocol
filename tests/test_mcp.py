import json
import threading
import time

import pytest

from aip.mcp_server import AipToolRuntime, TOOL_SPECS, ToolInputError, resolve_allowed_tools


class FakeTmuxController:
    def __init__(self, session_name="aip"):
        self.session_name = session_name
        self.spawn_calls = []
        self.killed = []
        self.sent_keys = []

    def spawn_window(self, window_name, command, start_directory=None):
        self.spawn_calls.append((window_name, command, start_directory))

    def kill_window(self, target):
        self.killed.append(target)

    def send_keys(self, target, text, press_enter=True):
        self.sent_keys.append((target, text, press_enter))


def test_tool_specs_has_eight_tools():
    assert len(TOOL_SPECS) == 8
    names = {s["name"] for s in TOOL_SPECS}
    assert names == {
        "report_status",
        "export_summary",
        "register_capabilities",
        "request_task",
        "report_progress",
        "wait_for",
        "spawn_teammate",
        "notify",
    }


def test_resolve_allowed_tools_for_worker_profile():
    assert resolve_allowed_tools(tool_profile="worker") == (
        "export_summary",
        "register_capabilities",
    )


def test_resolve_allowed_tools_rejects_unknown_tool():
    with pytest.raises(ToolInputError, match="Unknown tool"):
        resolve_allowed_tools(allowed_tools=["export_summary", "nope"])


def test_runtime_filters_tools_by_profile(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder", tool_profile="worker")

    assert [spec["name"] for spec in runtime.list_tool_specs()] == [
        "export_summary",
        "register_capabilities",
    ]


def test_runtime_rejects_tool_not_enabled_for_profile(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder", tool_profile="worker")

    with pytest.raises(ToolInputError, match="Tool not enabled"):
        runtime.execute("notify", {"target_agent": "reviewer", "message": "hello", "priority": "low"})


def test_report_status_and_request_task(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")

    status_result = json.loads(runtime.execute("report_status", {"status": "working", "message": "starting auth"}))
    task_result = json.loads(runtime.execute("request_task", {"task_description": "review auth", "target_role": "reviewer", "priority": "high"}))

    assert status_result["status"] == "working"
    assert task_result["task_id"] == "task-001"
    assert (tmp_path / "workspace" / "tasks" / "pending" / "task-001.md").exists()


def test_export_summary(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    result = json.loads(runtime.execute("export_summary", {"content": "# Review\nAll good."}))
    assert result["file"].startswith("summaries/")
    assert (tmp_path / "workspace" / result["file"]).exists()


def test_report_progress(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    result = json.loads(runtime.execute("report_progress", {"progress": "halfway done", "percentage": 50}))
    assert result["progress"] == "halfway done"
    assert result["percentage"] == 50


def test_wait_for_matches_existing_event(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder", poll_interval=0.01)
    runtime.execute("report_status", {"status": "finished"})

    result = json.loads(runtime.execute("wait_for", {"event_filter": "agent:coder,status:finished", "timeout": 0.1}))

    assert result["timeout"] is False
    assert result["matched_filter"] == "agent:coder,status:finished"
    assert result["event"]["status"] == "finished"


def test_wait_for_returns_timeout_when_no_match(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder", poll_interval=0.01)

    result = json.loads(runtime.execute("wait_for", {"event_filter": "agent:reviewer,status:finished", "timeout": 0.05}))

    assert result == {"matched_filter": None, "event": None, "timeout": True}


def test_wait_for_detects_future_event(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "orchestrator", poll_interval=0.01)

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
    runtime = AipToolRuntime(
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
    runtime = AipToolRuntime(
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

    child_runtime = AipToolRuntime(
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
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect")

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
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "worker")
    result = json.loads(runtime.execute("register_capabilities", {"capabilities": ["python", "testing"]}))
    assert result["capabilities"] == ["python", "testing"]
    assert "interests" not in result


def test_register_capabilities_invalid_interest_priority(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    try:
        runtime.execute("register_capabilities", {
            "capabilities": ["python"],
            "interests": {"agents": {"reviewer": "urgent"}},
        })
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "Invalid priority" in str(exc)


def test_invalid_status_returns_error(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    try:
        runtime.execute("report_status", {"status": "nope"})
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "Invalid status" in str(exc)


def test_unknown_tool_returns_error(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    try:
        runtime.execute("nonexistent_tool", {})
        assert False, "Should have raised ToolInputError"
    except ToolInputError as exc:
        assert "Unknown tool" in str(exc)


def test_spawn_teammate_rolls_back_tmux_on_post_spawn_failure(tmp_path, monkeypatch):
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(
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
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder", poll_interval=0.01)
    runtime.execute("report_status", {"status": "working"})
    # Inject corrupt line
    with runtime.workspace.events_path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt\n")
    runtime.execute("report_status", {"status": "finished"})

    result = json.loads(runtime.execute("wait_for", {"event_filter": "status:finished", "timeout": 0.1}))
    assert result["timeout"] is False
    assert result["event"]["status"] == "finished"


def test_notify_appends_event_and_returns_confirmation(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "api-architect")

    result = json.loads(runtime.execute("notify", {
        "target_agent": "db-architect",
        "message": "auth flow needs a session table",
        "priority": "high",
    }))

    assert result["sent"] is True
    assert result["from"] == "api-architect"
    assert result["target"] == "db-architect"
    assert result["priority"] == "high"

    events = [json.loads(line) for line in runtime.workspace.events_path.read_text().strip().split("\n")]
    notify_events = [e for e in events if e["event"] == "notify"]
    assert len(notify_events) == 1
    assert notify_events[0]["agent"] == "api-architect"
    assert notify_events[0]["target"] == "db-architect"
    assert notify_events[0]["message"] == "auth flow needs a session table"
    assert notify_events[0]["priority"] == "high"


def test_notify_broadcast_to_all(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "orchestrator")

    result = json.loads(runtime.execute("notify", {
        "target_agent": "all",
        "message": "shutting down in 5 minutes",
        "priority": "high",
    }))

    assert result["target"] == "all"


def test_notify_invalid_priority(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    with pytest.raises(ToolInputError, match="Invalid priority"):
        runtime.execute("notify", {
            "target_agent": "reviewer",
            "message": "hello",
            "priority": "urgent",
        })


def test_notify_empty_message(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    with pytest.raises(ToolInputError, match="message must not be empty"):
        runtime.execute("notify", {
            "target_agent": "reviewer",
            "message": "   ",
            "priority": "high",
        })


def test_notify_empty_target(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "coder")
    with pytest.raises(ToolInputError, match="target_agent must not be empty"):
        runtime.execute("notify", {
            "target_agent": "",
            "message": "hello",
            "priority": "high",
        })


def test_notify_visible_to_wait_for(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "db-architect", poll_interval=0.01)

    # Send a notify, then wait_for it
    runtime.execute("notify", {
        "target_agent": "api-architect",
        "message": "schema ready",
        "priority": "high",
    })

    result = json.loads(runtime.execute("wait_for", {
        "event_filter": "event:notify,target:api-architect",
        "timeout": 0.1,
    }))

    assert result["timeout"] is False
    assert result["event"]["event"] == "notify"
    assert result["event"]["message"] == "schema ready"


def test_notify_high_priority_injects_into_supported_cli(tmp_path):
    """High priority + injection-capable CLI → sends mid-stream via tmux."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    # Spawn a claude-code agent so it's in the tree with cli_type
    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "claude-code",
        "capabilities": ["python"],
    })

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "don't edit auth.py",
        "priority": "high",
    }))

    assert result["injected_to"] == ["coder"]
    # Verify send_keys was called with the /btw command
    assert len(tmux.sent_keys) == 1
    target, text, _ = tmux.sent_keys[0]
    assert target == "coder"
    assert "/btw" in text
    assert "don't edit auth.py" in text
    assert "architect says:" in text


def test_notify_high_priority_skips_injection_for_unsupported_cli(tmp_path):
    """High priority + no injection support → event log only."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "aider",
        "capabilities": ["python"],
    })

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "heads up",
        "priority": "high",
    }))

    assert result["injected_to"] == []
    # No send_keys called (spawn_teammate doesn't use send_keys)
    assert len(tmux.sent_keys) == 0


def test_notify_high_priority_injects_codex_without_slash(tmp_path):
    """Codex injection is plain text (Enter to inject), no /btw prefix."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "codex",
        "capabilities": ["python"],
    })

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "use auth_v2.py instead",
        "priority": "high",
    }))

    assert result["injected_to"] == ["coder"]
    _, text, _ = tmux.sent_keys[0]
    # Codex template is plain "{message}" — no /btw prefix
    assert "/btw" not in text
    assert "use auth_v2.py instead" in text


def test_notify_high_priority_injects_gemini_steering(tmp_path):
    """Gemini model steering: plain text injection while spinner is visible."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "gemini",
        "capabilities": ["python"],
    })

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "skip the migration step",
        "priority": "high",
    }))

    assert result["injected_to"] == ["coder"]
    _, text, _ = tmux.sent_keys[0]
    assert "/btw" not in text
    assert "skip the migration step" in text


def test_notify_medium_priority_never_injects(tmp_path):
    """Medium/low priority → event log only, even for supported CLIs."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "claude-code",
        "capabilities": ["python"],
    })

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "fyi",
        "priority": "medium",
    }))

    assert result["injected_to"] == []
    assert len(tmux.sent_keys) == 0


def test_notify_broadcast_injects_into_all_supported(tmp_path):
    """Broadcast to 'all' injects into every supported CLI, skips others."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "orchestrator", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder1",
        "cli_type": "claude-code",
        "capabilities": ["python"],
    })
    runtime.execute("spawn_teammate", {
        "name": "coder2",
        "cli_type": "aider",
        "capabilities": ["python"],
    })
    runtime.execute("spawn_teammate", {
        "name": "coder3",
        "cli_type": "copilot",
        "capabilities": ["python"],
    })

    result = json.loads(runtime.execute("notify", {
        "target_agent": "all",
        "message": "shutting down",
        "priority": "high",
    }))

    # claude-code and copilot support injection, aider does not
    assert sorted(result["injected_to"]) == ["coder1", "coder3"]
    assert len(tmux.sent_keys) == 2


def test_request_task_with_blocked_by(tmp_path):
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "orchestrator")

    result = json.loads(runtime.execute("request_task", {
        "task_description": "implement feature B",
        "target_role": "coder",
        "priority": "high",
        "blocked_by": ["dep-001", "dep-002"],
    }))

    assert result["task_id"] == "task-001"
    assert result["blocked_by"] == ["dep-001", "dep-002"]

    task_file = tmp_path / "workspace" / "tasks" / "pending" / "task-001.md"
    content = task_file.read_text(encoding="utf-8")
    assert "blocked_by: dep-001, dep-002" in content


# --- elicitation parameter tests ---


def test_notify_elicit_supported_cli_skips_injection(tmp_path):
    """elicit=True + supported CLI → no send_keys, agent in elicitation_targets."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "claude-code",
        "capabilities": ["python"],
    })
    tmux.sent_keys.clear()  # reset after spawn

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "please review auth module",
        "priority": "high",
        "elicit": True,
    }))

    assert result["sent"] is True
    assert result["elicit"] is True
    assert "coder" in result["elicitation_targets"]
    assert result["injected_to"] == []
    assert len(tmux.sent_keys) == 0


def test_notify_elicit_unsupported_cli_injects_normally(tmp_path):
    """elicit=True + unsupported CLI → falls back to normal injection."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "gemini",
        "capabilities": ["python"],
    })
    tmux.sent_keys.clear()

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "please review auth module",
        "priority": "high",
        "elicit": True,
    }))

    assert result["sent"] is True
    assert result["elicit"] is True
    assert result["elicitation_targets"] == []
    assert result["injected_to"] == ["coder"]
    assert len(tmux.sent_keys) == 1
    _, text, _ = tmux.sent_keys[0]
    assert "please review auth module" in text


def test_notify_without_elicit_injects_normally(tmp_path):
    """No elicit parameter → normal injection for supported CLI."""
    tmux = FakeTmuxController()
    runtime = AipToolRuntime(str(tmp_path / "workspace"), "architect", tmux_controller=tmux)

    runtime.execute("spawn_teammate", {
        "name": "coder",
        "cli_type": "claude-code",
        "capabilities": ["python"],
    })
    tmux.sent_keys.clear()

    result = json.loads(runtime.execute("notify", {
        "target_agent": "coder",
        "message": "check the logs",
        "priority": "high",
    }))

    assert result["sent"] is True
    assert "elicit" not in result
    assert result["injected_to"] == ["coder"]
    assert len(tmux.sent_keys) == 1
    _, text, _ = tmux.sent_keys[0]
    assert "/btw" in text
    assert "check the logs" in text
