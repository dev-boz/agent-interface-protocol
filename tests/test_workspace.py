import json
import multiprocessing as mp
import queue
import sys
import threading
from pathlib import Path

from aip.workspace import AipWorkspace, atomic_write_text

# Make IMX importable without requiring it to be installed
_IMX_PATH = Path("/home/dev-boz/projects/IMX")
if _IMX_PATH.exists() and str(_IMX_PATH) not in sys.path:
    sys.path.insert(0, str(_IMX_PATH))


def _acquire_lock_in_subprocess(workspace_root: str, barrier, results, holder: str) -> None:
    workspace = AipWorkspace(workspace_root)
    try:
        barrier.wait(timeout=10)
        results.put((holder, workspace.acquire_lock("shared-resource", holder, ttl_seconds=30)))
    except BaseException as exc:  # pragma: no cover - subprocess boundary
        results.put((holder, f"{type(exc).__name__}: {exc}"))


def test_workspace_creates_layout_and_status_files(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()

    assert workspace.events_path.exists()
    assert workspace.agent_tree_path.exists()
    assert workspace.pending_dir.exists()
    assert workspace.claimed_dir.exists()
    assert workspace.done_dir.exists()
    assert workspace.failed_dir.exists()
    assert workspace.task_packets_dir.exists()

    snapshot = workspace.write_status("coder", status="working", message="starting")
    assert snapshot["status"] == "working"
    assert snapshot["message"] == "starting"

    updated = workspace.write_status("coder", remove_keys=("message",), progress="1 of 3")
    assert updated["status"] == "working"
    assert updated["progress"] == "1 of 3"
    assert "message" not in updated


def test_workspace_events_and_summary_exports(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
    summary_path = workspace.export_summary("reviewer", "## done")
    event = workspace.append_event("reviewer", "status", status="finished")

    assert summary_path.exists()
    assert summary_path.read_text(encoding="utf-8") == "## done\n"
    assert event["event"] == "status"

    tail = workspace.tail_events(limit=5)
    assert tail == [json.loads(workspace.events_path.read_text(encoding="utf-8").strip())]


def test_workspace_task_packet_roundtrip_and_route_request_reference(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
    packet_path = workspace.write_task_packet(
        "task-001",
        {
            "schema_version": "0.6",
            "task_id": "task-001",
            "task_class": "analysis",
            "risk_tier": "READ_ONLY",
            "relationship": "subtask",
            "provenance": {
                "written_by": "architect",
                "written_at": "2026-05-22T00:00:00Z",
                "chain_depth": 1,
            },
        },
    )
    route_path = workspace.write_route_request(
        "task-001",
        task_class="analysis",
        risk_tier="READ_ONLY",
        requester="architect",
        task_packet_ref=packet_path.relative_to(workspace.root).as_posix(),
        chain_depth=1,
    )

    packet = workspace.read_task_packet("task-001")
    route_request = json.loads(route_path.read_text(encoding="utf-8"))

    assert packet_path == workspace.task_packets_dir / "task-001.json"
    assert packet["task_class"] == "analysis"
    assert packet["provenance"]["chain_depth"] == 1
    assert route_request["task_packet_ref"] == "tasks/packets/task-001.json"
    assert route_request["chain_depth"] == 1


def test_next_task_id_scans_all_task_directories(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    for relative in ("pending/task-001.md", "done/task-009.md", "claimed/coder-task-010.md"):
        path = workspace.tasks_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# task\n", encoding="utf-8")

    assert workspace.next_task_id() == "task-011"


def test_lock_requires_matching_holder_for_release(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")

    assert workspace.acquire_lock("repo-state", "agent-a")
    assert not workspace.acquire_lock("repo-state", "agent-b")
    assert not workspace.release_lock("repo-state", "agent-b")
    assert workspace.release_lock("repo-state", "agent-a")
    assert workspace.acquire_lock("repo-state", "agent-b")


def test_acquire_lock_has_single_winner_across_processes(tmp_path):
    workspace_root = tmp_path / "workspace"
    contenders = 6
    ctx = mp.get_context("fork" if sys.platform != "win32" else "spawn")
    barrier = ctx.Barrier(contenders)
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_acquire_lock_in_subprocess,
            args=(str(workspace_root), barrier, results, f"agent-{index}"),
        )
        for index in range(contenders)
    ]

    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
        hung = [process.pid for process in processes if process.is_alive()]
        assert not hung, f"lock contenders hung: {hung}"
        assert all(process.exitcode == 0 for process in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.close()

    outcomes = {}
    for _ in range(contenders):
        holder, result = results.get(timeout=5)
        outcomes[holder] = result

    assert not [result for result in outcomes.values() if isinstance(result, str)]
    winners = [holder for holder, result in outcomes.items() if result is True]
    losers = [holder for holder, result in outcomes.items() if result is False]

    assert len(winners) == 1
    assert len(losers) == contenders - 1

    lock_path = workspace_root / "locks" / "shared-resource.lock"
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock_data["holder"] == winners[0]


def test_agent_tree_helpers_register_root_and_child(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")

    root = workspace.ensure_agent_node(
        "orchestrator",
        depth=0,
        parent=None,
        tmux_window="aip:orchestrator",
        cli_type="claude-code",
    )
    child = workspace.add_agent_child(
        "orchestrator",
        "coder",
        depth=1,
        tmux_window="aip:coder",
        cli_type="gemini",
    )

    assert root["depth"] == 0
    assert child["parent"] == "orchestrator"
    tree = workspace.read_agent_tree()
    assert tree["orchestrator"]["children"] == ["coder"]
    assert tree["coder"]["cli_type"] == "gemini"


def test_agent_tree_postorder_and_removal(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure_agent_node("orchestrator", depth=0, parent=None, tmux_window="aip:orchestrator")
    workspace.add_agent_child("orchestrator", "manager", depth=1, tmux_window="aip:manager")
    workspace.add_agent_child("manager", "worker", depth=2, tmux_window="aip:worker")

    assert workspace.agent_subtree_postorder("orchestrator") == ["worker", "manager", "orchestrator"]

    workspace.remove_agent_node("worker")
    workspace.remove_agent_node("manager")
    tree = workspace.read_agent_tree()
    assert tree["orchestrator"]["children"] == []


def test_read_json_returns_empty_on_corrupt_file(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    corrupt = workspace.root / "corrupt.json"
    corrupt.write_text("{invalid json", encoding="utf-8")
    assert workspace.read_json(corrupt) == {}


def test_tail_events_skips_corrupt_lines(tmp_path):
    workspace = AipWorkspace(tmp_path / "workspace")
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


# ---------------------------------------------------------------------------
# Task 1: Integration test — append_event → correlate_workspace → append_task_record
# ---------------------------------------------------------------------------

def test_full_flow_append_event_correlate_telemetry(tmp_path):
    """Exercise the full data pipeline: workspace.append_event →
    imx.correlation.correlate_workspace → imx.telemetry.append_task_record.

    Uses tmp_path only; no mocks.  Verifies that task data written via
    append_event flows through correlation and lands verbatim in tasks.jsonl.
    """
    from imx.correlation import correlate_workspace
    from imx.telemetry import append_task_record  # noqa: F401 — import-only verify

    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()

    task_id = "task-001"

    # Write a route decision so correlation_status == "full"
    workspace.write_route_decision(
        task_id,
        node_id="node-alpha",
        profile="standard",
        rationale="test route",
        decided_by="test-router",
    )

    # Two events tagged with the task — the correlator groups on `task` key
    workspace.append_event("coder", "status", task=task_id, status="working")
    workspace.append_event("coder", "status", task=task_id, status="finished")

    telemetry_dir = tmp_path / "telemetry"
    n_records = correlate_workspace(
        workspace_root=workspace.root,
        telemetry_dir=telemetry_dir,
    )

    # At least one record for our task_id
    assert n_records >= 1

    tasks_jsonl = telemetry_dir / "tasks.jsonl"
    assert tasks_jsonl.exists(), "tasks.jsonl was not created by append_task_record"

    records = [json.loads(line) for line in tasks_jsonl.read_text().splitlines() if line.strip()]
    assert records, "No records written to tasks.jsonl"

    task_record = next((r for r in records if r.get("task_id") == task_id), None)
    assert task_record is not None, f"No record for {task_id} in tasks.jsonl"

    # Verify key fields flowed through correctly
    assert task_record["agent"] == "coder"
    assert task_record["outcome"] == "succeeded"
    assert task_record["node_id"] == "node-alpha"
    assert task_record["correlation_status"] == "full"
    assert task_record["schema_version"] == "0.6"
    assert task_record["started_at"] is not None
    assert task_record["completed_at"] is not None


# ---------------------------------------------------------------------------
# Task 2: Interest map delivery — append_event tags interested_agents
# ---------------------------------------------------------------------------

def test_append_event_tags_interested_agents_when_registry_configured(tmp_path):
    """When an InterestRegistry is passed to AipWorkspace, append_event should
    consult it and tag the event dict with interested_agents."""
    from aip.interest_maps import InterestRegistry, InterestSubscription, InterestKind

    registry = InterestRegistry()
    registry.register(InterestSubscription(
        agent_name="monitor-agent",
        kinds=[InterestKind.TASK_STATUS],
    ))
    registry.register(InterestSubscription(
        agent_name="heartbeat-watcher",
        kinds=[InterestKind.HEARTBEAT],
    ))

    workspace = AipWorkspace(tmp_path / "ws", interest_registry=registry)

    # A status event — should match monitor-agent, not heartbeat-watcher
    event = workspace.append_event("coder", "status", status="working")
    assert "interested_agents" in event
    assert "monitor-agent" in event["interested_agents"]
    assert "heartbeat-watcher" not in event["interested_agents"]

    # Verify the tag was persisted in events.jsonl too
    lines = workspace.events_path.read_text().splitlines()
    persisted = json.loads(lines[-1])
    assert "interested_agents" in persisted
    assert "monitor-agent" in persisted["interested_agents"]


def test_append_event_without_registry_is_unchanged(tmp_path):
    """Without a registry, append_event should not add interested_agents."""
    workspace = AipWorkspace(tmp_path / "ws")
    event = workspace.append_event("coder", "status", status="finished")
    assert "interested_agents" not in event


def test_derive_activity_idle_when_no_claim_and_present(tmp_path):
    """Agent with presence sentinel but no claimed task → idle."""
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_presence("coder")

    record = workspace.derive_activity("coder")
    assert record["agent"] == "coder"
    assert record["state"] == "idle"
    assert "derived_at" in record


def test_derive_activity_running_with_fresh_heartbeat_and_claim(tmp_path):
    """Agent with a claimed task and a fresh heartbeat → running."""
    from aip.tasks import TaskQueue
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_heartbeat("coder", status="working")
    queue = TaskQueue(workspace)
    task = queue.create_task(description="code the feature")
    queue.claim_task(task.task_id, "coder")

    record = workspace.derive_activity("coder")
    assert record["state"] == "running"
    assert "tasks/claimed/" in record["derived_from"]["claim"]
    assert "heartbeat_age_seconds" in record


def test_derive_activity_stalled_with_old_heartbeat_and_claim(tmp_path):
    """Agent with a claimed task but a stale heartbeat → stalled."""
    import time
    from aip.tasks import TaskQueue
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    # Write a heartbeat with a past timestamp
    old_ts = "2000-01-01T00:00:00Z"
    heartbeat_path = workspace.root / "HEARTBEAT-coder.md"
    heartbeat_path.write_text(f"# HEARTBEAT — coder\nagent: coder\nstatus: working\nts: {old_ts}\n", encoding="utf-8")
    queue = TaskQueue(workspace)
    task = queue.create_task(description="long running task")
    queue.claim_task(task.task_id, "coder")

    record = workspace.derive_activity("coder", staleness_threshold_seconds=60)
    assert record["state"] == "stalled"


def test_derive_activity_needs_input_with_open_gate(tmp_path):
    """Agent with a claim AND an open gate → needs-input."""
    from aip.gates import write_gate
    from aip.tasks import TaskQueue
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    queue = TaskQueue(workspace)
    task = queue.create_task(description="push to prod")
    queue.claim_task(task.task_id, "coder")
    write_gate(str(workspace.root), "coder", tool="bash", command="git push origin main")

    record = workspace.derive_activity("coder")
    assert record["state"] == "needs-input"
    assert "gate" in record["derived_from"]


def test_derive_activity_write_persists_activity_json(tmp_path):
    """write=True persists {agent}-activity.json to the status directory."""
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()

    workspace.derive_activity("coder", write=True)

    activity_path = workspace.status_dir / "coder-activity.json"
    assert activity_path.exists()
    data = json.loads(activity_path.read_text(encoding="utf-8"))
    assert data["agent"] == "coder"
    assert data["state"] in {"running", "needs-input", "stalled", "idle", "done"}


def test_derive_activity_done_when_finished_and_not_present(tmp_path):
    """Agent with status=finished, no presence, no claim → done."""
    workspace = AipWorkspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_status("coder", status="finished")

    record = workspace.derive_activity("coder")
    assert record["state"] == "done"


def test_append_event_registry_all_kind_matches_any_event(tmp_path):
    """An InterestKind.ALL subscription should match events of any type."""
    from aip.interest_maps import InterestRegistry, InterestSubscription, InterestKind

    from aip.interest_maps import Priority
    registry = InterestRegistry()
    registry.register(InterestSubscription(
        agent_name="omniscient",
        kinds=[InterestKind.ALL],
        min_priority=Priority.LOW,  # must be LOW to receive heartbeat events
    ))

    workspace = AipWorkspace(tmp_path / "ws", interest_registry=registry)

    for event_type in ("status", "heartbeat", "gate", "tool", "export"):
        ev = workspace.append_event("coder", event_type)
        assert "omniscient" in ev.get("interested_agents", []), \
            f"omniscient should be interested in {event_type}"
