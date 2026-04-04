from __future__ import annotations

import json
import threading
import time

import pytest

from aip.mcp_server import AipToolRuntime


@pytest.mark.live_tmux
def test_live_agent_lifecycle(live_tmux_env) -> None:
    live_tmux_env.run_aip("agent", "spawn", "shell-live", "bash")

    agents = live_tmux_env.run_aip("agent", "list")
    assert "shell-live" in [agent["name"] for agent in agents]

    live_tmux_env.run_aip("agent", "send", "shell-live", "printf 'LIVE_AGENT_OK\\n'")
    output = live_tmux_env.wait_for_pane_text("shell-live", "LIVE_AGENT_OK")
    assert "LIVE_AGENT_OK" in output

    live_tmux_env.run_aip("agent", "kill", "shell-live")
    remaining = live_tmux_env.run_aip("agent", "list")
    assert "shell-live" not in [agent["name"] for agent in remaining]


@pytest.mark.live_tmux
def test_live_incremental_pane_reads(live_tmux_env) -> None:
    live_tmux_env.run_aip("agent", "spawn", "reader-live", "bash")
    runtime = AipToolRuntime(
        str(live_tmux_env.workspace_root),
        "architect-live",
        session_name=live_tmux_env.session_name,
        tool_profile="architect",
    )

    live_tmux_env.run_aip("agent", "send", "reader-live", "printf 'ALPHA\\n'")
    live_tmux_env.wait_for_pane_text("reader-live", "ALPHA")

    first = json.loads(runtime.execute("read_pane", {"target_agent": "reader-live", "incremental": True}))
    assert "ALPHA" in first["content"]
    assert first["incremental"] is True
    assert first["cursor_before"] is None

    live_tmux_env.run_aip("agent", "send", "reader-live", "printf 'BETA\\n'")

    deadline = time.monotonic() + 10
    while True:
        second = json.loads(runtime.execute("read_pane", {"target_agent": "reader-live", "incremental": True}))
        if "BETA" in second["content"]:
            break
        if time.monotonic() >= deadline:
            raise AssertionError(second)
        time.sleep(0.2)

    empty = json.loads(runtime.execute("read_pane", {"target_agent": "reader-live", "incremental": True}))
    assert empty["content"] == ""


@pytest.mark.live_tmux
def test_live_mcp_tool_surface(live_tmux_env) -> None:
    with live_tmux_env.mcp_client("manager-live") as client:
        tool_names = {tool["name"] for tool in client.list_tools()}
        assert tool_names == {
            "report_status",
            "export_summary",
            "register_capabilities",
            "request_task",
            "report_progress",
            "read_pane",
            "wait_for",
            "spawn_teammate",
            "notify",
        }

        status = client.call_tool("report_status", {"status": "working", "message": "live test"})
        assert status["status"] == "working"

        capabilities = client.call_tool(
            "register_capabilities",
            {"capabilities": ["python", "testing"]},
        )
        assert capabilities["capabilities"] == ["python", "testing"]

        progress = client.call_tool("report_progress", {"progress": "1 of 1", "percentage": 100})
        assert progress["percentage"] == 100

        task = client.call_tool(
            "request_task",
            {"task_description": "live delegated task", "target_role": "reviewer"},
        )
        assert task["task_id"].startswith("task-")

        summary = client.call_tool(
            "export_summary",
            {"content": "# Live Summary\n\nEverything works."},
        )
        assert summary["file"].startswith("summaries/")

        spawned = client.call_tool(
            "spawn_teammate",
            {"name": "worker-live", "cli_type": "bash", "capabilities": ["shell"]},
        )
        assert spawned["agent_id"] == "worker-live"

        live_tmux_env.run_aip("agent", "send", "worker-live", "printf 'FROM_WORKER\\n'")
        live_tmux_env.wait_for_pane_text("worker-live", "FROM_WORKER")

        pane = client.call_tool("read_pane", {"target_agent": "worker-live"})
        assert "FROM_WORKER" in pane["content"]

        notify = client.call_tool(
            "notify",
            {"target_agent": "worker-live", "message": "heads up", "priority": "low"},
        )
        assert notify["sent"] is True

        wait_result = client.call_tool(
            "wait_for",
            {"event_filter": "agent:manager-live,status:working", "timeout": 0.1},
        )
        assert wait_result["timeout"] is False
        assert wait_result["event"]["status"] == "working"


@pytest.mark.live_tmux
def test_live_task_lease_expiry_and_reclaim(live_tmux_env) -> None:
    with live_tmux_env.mcp_client("lease-live") as client:
        created = client.call_tool(
            "request_task",
            {"task_description": "lease expiry live task", "priority": "high"},
        )

    claimed = live_tmux_env.run_aip(
        "task",
        "claim",
        created["task_id"],
        "lease-agent",
        "--lease-seconds",
        "2",
    )
    assert claimed["claimed_by"] == "lease-agent"

    time.sleep(3)
    reclaimed = live_tmux_env.run_aip("task", "reclaim-expired")
    assert created["task_id"] in reclaimed["reclaimed"]

    pending = live_tmux_env.run_aip("task", "list", "--stage", "pending")
    assert created["task_id"] in [task["task_id"] for task in pending]


@pytest.mark.live_tmux
def test_live_concurrent_claiming(live_tmux_env) -> None:
    with live_tmux_env.mcp_client("orchestrator-live") as client:
        created = client.call_tool(
            "request_task",
            {"task_description": "race me", "priority": "high"},
        )

    results: dict[str, dict[str, object]] = {}

    def claim(agent_name: str) -> None:
        try:
            results[agent_name] = {
                "success": True,
                "result": live_tmux_env.run_aip("task", "claim", created["task_id"], agent_name),
            }
        except AssertionError as exc:
            results[agent_name] = {
                "success": False,
                "error": str(exc),
            }

    threads = [threading.Thread(target=claim, args=(f"racer-{idx}",)) for idx in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [agent for agent, result in results.items() if result["success"]]
    assert len(winners) == 1, results
