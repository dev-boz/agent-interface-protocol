"""Tests for plan files and DAG decomposition (aip.plan)."""

from __future__ import annotations

import json

from aip.cli import main
from aip.plan import (
    Plan,
    PlanNode,
    append_audit,
    mark_node_done,
    node_states,
    parse_plan,
    project_plan,
    provision_task_tree,
    task_tree,
)
from aip.tasks import TaskQueue
from aip.workspace import AipWorkspace


def _oauth_plan():
    return Plan(
        task_id="task-042",
        goal="ship oauth login",
        nodes=[
            PlanNode(id="design", outputs=["artifacts/design-notes.md"]),
            PlanNode(id="backend", depends_on=["design"], outputs=["artifacts/backend.patch"]),
            PlanNode(id="frontend", depends_on=["design"], outputs=["artifacts/frontend.patch"]),
            PlanNode(id="integration", depends_on=["backend", "frontend"]),
        ],
    )


def test_parse_plan():
    data = {
        "task_id": "task-042",
        "goal": "ship oauth login",
        "nodes": [
            {"id": "design", "outputs": ["artifacts/design-notes.md"]},
            {"id": "backend", "depends_on": ["design"], "leaf_tools": ["pytest"]},
        ],
    }
    plan = parse_plan(data)
    assert plan.task_id == "task-042"
    assert plan.nodes[1].depends_on == ["design"]
    assert plan.nodes[1].leaf_tools == ["pytest"]


def test_initial_states_only_root_ready(tmp_path):
    ws = AipWorkspace(str(tmp_path / "workspace"))
    plan = _oauth_plan()
    states = node_states(plan, task_tree(ws, plan.task_id))
    assert states == {
        "design": "ready",
        "backend": "blocked",
        "frontend": "blocked",
        "integration": "blocked",
    }


def test_states_advance_as_outputs_appear(tmp_path):
    ws = AipWorkspace(str(tmp_path / "workspace"))
    plan = _oauth_plan()
    base = provision_task_tree(ws, plan.task_id)
    # design completes → backend and frontend become ready.
    (base / "artifacts" / "design-notes.md").write_text("done")
    states = node_states(plan, base)
    assert states["design"] == "done"
    assert states["backend"] == "ready"
    assert states["frontend"] == "ready"
    assert states["integration"] == "blocked"


def test_marker_completes_node_without_outputs(tmp_path):
    ws = AipWorkspace(str(tmp_path / "workspace"))
    plan = _oauth_plan()
    base = provision_task_tree(ws, plan.task_id)
    (base / "artifacts" / "design-notes.md").write_text("d")
    (base / "artifacts" / "backend.patch").write_text("b")
    (base / "artifacts" / "frontend.patch").write_text("f")
    # integration has no outputs → completes via marker.
    assert node_states(plan, base)["integration"] == "ready"
    mark_node_done(base, "integration")
    assert node_states(plan, base)["integration"] == "done"


def test_project_plan_projects_only_ready_nodes(tmp_path):
    ws = AipWorkspace(str(tmp_path / "workspace"))
    ws.ensure()
    queue = TaskQueue(ws)
    plan = _oauth_plan()

    projected = project_plan(plan, queue)
    assert projected == ["design"]
    pending_ids = {t.task_id for t in queue.list_tasks("pending")}
    assert "task-042.design" in pending_ids
    # Blocked nodes are not projected.
    assert "task-042.backend" not in pending_ids


def test_project_plan_is_incremental(tmp_path):
    ws = AipWorkspace(str(tmp_path / "workspace"))
    ws.ensure()
    queue = TaskQueue(ws)
    plan = _oauth_plan()
    project_plan(plan, queue)

    # Re-projecting without progress adds nothing.
    assert project_plan(plan, queue) == []

    # Completing design makes backend + frontend ready on the next projection.
    base = task_tree(ws, plan.task_id)
    (base / "artifacts" / "design-notes.md").write_text("done")
    newly = set(project_plan(plan, queue))
    assert newly == {"backend", "frontend"}


def test_append_audit_writes_jsonl(tmp_path):
    ws = AipWorkspace(str(tmp_path / "workspace"))
    append_audit(ws, "task-042", "claimed", agent="coder")
    audit = task_tree(ws, "task-042") / "audit.jsonl"
    record = json.loads(audit.read_text().splitlines()[0])
    assert record["event"] == "claimed"
    assert record["agent"] == "coder"
    assert record["task_id"] == "task-042"


def test_cli_plan_project_and_status(tmp_path, capsys):
    ws_root = tmp_path / "workspace"
    ws = AipWorkspace(str(ws_root))
    base = provision_task_tree(ws, "task-042")
    (base / "plan.yaml").write_text(
        "task_id: task-042\n"
        "goal: ship oauth login\n"
        "nodes:\n"
        "  - id: design\n"
        "    outputs: [artifacts/design-notes.md]\n"
        "  - id: backend\n"
        "    depends_on: [design]\n"
    )

    rc = main(["plan", "project", "--task-id", "task-042", "--workspace-root", str(ws_root)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["projected"] == ["design"]
    assert out["states"]["backend"] == "blocked"

    rc = main(["plan", "status", "--task-id", "task-042", "--workspace-root", str(ws_root)])
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["states"]["design"] == "ready"
