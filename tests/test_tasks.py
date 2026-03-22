from datetime import UTC, datetime, timedelta

import pytest

from atmux.tasks import TaskClaimError, TaskError, TaskQueue
from atmux.workspace import AtmuxWorkspace


def test_task_queue_lifecycle_moves_files_between_stages(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    queue = TaskQueue(workspace)

    task = queue.create_task(
        description="implement auth",
        task_type="coding",
        priority="high",
        target_role="coder",
        context="see summary",
    )
    assert (workspace.pending_dir / "task-001.md").exists()

    claimed = queue.claim_task(
        task.task_id,
        "coder",
        lease_seconds=120,
        now=datetime(2026, 3, 17, 12, 0, tzinfo=UTC),
    )
    assert claimed.claimed_by == "coder"
    assert (workspace.claimed_dir / "coder-task-001.md").exists()

    completed = queue.complete_task(task.task_id, agent_name="coder")
    assert completed.task_id == "task-001"
    assert (workspace.done_dir / "task-001.md").exists()
    events = workspace.tail_events(limit=10)
    assert [(event["event"], event.get("action")) for event in events] == [
        ("task", "create"),
        ("task", "claim"),
        ("task", "done"),
    ]


def test_claiming_missing_task_raises(tmp_path):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))

    with pytest.raises(TaskClaimError):
        queue.claim_task("task-999", "coder")


def test_reclaim_expired_returns_task_to_pending(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    queue = TaskQueue(workspace)
    task = queue.create_task(description="fix tests")

    queue.claim_task(
        task.task_id,
        "reviewer",
        lease_seconds=30,
        now=datetime(2026, 3, 17, 12, 0, tzinfo=UTC),
    )
    reclaimed = queue.reclaim_expired(now=datetime(2026, 3, 17, 12, 1, tzinfo=UTC))

    assert reclaimed == [task.task_id]
    pending_text = (workspace.pending_dir / f"{task.task_id}.md").read_text(encoding="utf-8")
    assert "claimed_by:" not in pending_text
    assert "lease_expires:" not in pending_text
    assert "reclaimed_at:" in pending_text
    assert workspace.tail_events(limit=1)[0]["action"] == "reclaim"


def test_list_tasks_reads_requested_stage(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    queue = TaskQueue(workspace)
    queue.create_task(description="task one")
    queue.create_task(description="task two")

    tasks = queue.list_tasks("pending")
    assert [task.description for task in tasks] == ["task one", "task two"]


def test_requeue_tasks_for_agent_generates_handoff_metadata_and_event(tmp_path):
    workspace = AtmuxWorkspace(tmp_path / "workspace")
    queue = TaskQueue(workspace)
    task = queue.create_task(description="finish refactor")
    queue.claim_task(task.task_id, "coder")

    requeued = queue.requeue_tasks_for_agent(
        "coder",
        reason="graceful shutdown",
        handoff_summary="summaries/coder-0319-2330.md",
    )

    assert [task.task_id for task in requeued] == ["task-001"]
    pending_text = (workspace.pending_dir / "task-001.md").read_text(encoding="utf-8")
    assert "handoff_summary: summaries/coder-0319-2330.md" in pending_text
    assert "interrupted_reason: graceful shutdown" in pending_text
    event = workspace.tail_events(limit=1)[0]
    assert event["event"] == "task"
    assert event["action"] == "requeue"
    assert event["handoff_summary"] == "summaries/coder-0319-2330.md"


@pytest.mark.parametrize("bad_id", [
    "../../escape",
    "../sibling",
    "foo/bar",
    "task-001/../../etc",
    "..",
    ".",
    "hello world",
    "task\x00null",
])
def test_create_task_rejects_path_traversal_ids(tmp_path, bad_id):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))
    with pytest.raises(TaskError, match="Invalid task_id"):
        queue.create_task(description="test", task_id=bad_id)


def test_claim_task_rejects_path_traversal_ids(tmp_path):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))
    queue.create_task(description="test")
    with pytest.raises(TaskError, match="Invalid task_id"):
        queue.claim_task("../../escape", "coder")


def test_complete_task_rejects_path_traversal_ids(tmp_path):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))
    with pytest.raises(TaskError, match="Invalid task_id"):
        queue.complete_task("../../escape", agent_name="coder")


def test_fail_task_rejects_path_traversal_ids(tmp_path):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))
    with pytest.raises(TaskError, match="Invalid task_id"):
        queue.fail_task("../escape", agent_name="coder")


def test_create_task_accepts_valid_custom_ids(tmp_path):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))
    task = queue.create_task(description="test", task_id="my-custom-task.001")
    assert task.task_id == "my-custom-task.001"


def test_claim_task_rejects_non_positive_lease(tmp_path):
    queue = TaskQueue(AtmuxWorkspace(tmp_path / "workspace"))
    task = queue.create_task(description="test")
    with pytest.raises(TaskError, match="lease_seconds must be positive"):
        queue.claim_task(task.task_id, "coder", lease_seconds=0)
    with pytest.raises(TaskError, match="lease_seconds must be positive"):
        queue.claim_task(task.task_id, "coder", lease_seconds=-10)
