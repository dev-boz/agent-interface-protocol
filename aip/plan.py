"""Plan files and DAG decomposition (spec §"Plan Files and DAG Decomposition").

When a task has internal structure it is written down as
``workspace/tasks/{task_id}/plan.yaml`` — the plan file is the truth. The queue
is merely the *execution projection* of that truth: ready nodes (all
dependencies satisfied) are projected into ``pending/`` as ordinary task files;
counts across ``pending/claimed/done/failed`` become the read-only kanban view.

Node completion is derived from directory state (spec: "compare the plan file
with directory state"): a node is done when all its declared ``outputs`` exist
under the task subtree, or when a ``nodes/{id}.done`` marker is present. The
packing/projection logic is dependency-free; only ``load_plan`` (reading the
YAML file) uses PyYAML, the optional ``context`` extra.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .workspace import AipWorkspace, isoformat_z, utc_now


@dataclass
class PlanNode:
    id: str
    depends_on: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    leaf_tools: list[str] = field(default_factory=list)


@dataclass
class Plan:
    task_id: str
    goal: str = ""
    nodes: list[PlanNode] = field(default_factory=list)


def parse_plan(data: dict) -> Plan:
    """Build a Plan from an already-parsed dict (no YAML needed)."""
    nodes = []
    for raw in data.get("nodes", []) or []:
        nodes.append(
            PlanNode(
                id=str(raw["id"]),
                depends_on=list(raw.get("depends_on", []) or []),
                outputs=list(raw.get("outputs", []) or []),
                leaf_tools=list(raw.get("leaf_tools", []) or []),
            )
        )
    return Plan(task_id=str(data.get("task_id", "")), goal=str(data.get("goal", "")), nodes=nodes)


def plan_path(workspace: AipWorkspace, task_id: str) -> Path:
    return workspace.tasks_dir / task_id / "plan.yaml"


def load_plan(path: Path | str) -> Plan:
    """Load a plan.yaml file (requires the ``context`` extra for PyYAML)."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
        raise RuntimeError(
            "Plan files need PyYAML. Install with: pip install 'aip[context]'"
        ) from exc
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"plan file not found: {p}")
    return parse_plan(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


def task_tree(workspace: AipWorkspace, task_id: str) -> Path:
    return workspace.tasks_dir / task_id


def node_done(node: PlanNode, task_dir: Path) -> bool:
    """A node is done when a completion marker exists or all outputs are present."""
    marker = task_dir / "nodes" / f"{node.id}.done"
    if marker.exists():
        return True
    if node.outputs:
        return all((task_dir / out).exists() for out in node.outputs)
    return False


def node_states(plan: Plan, task_dir: Path) -> dict[str, str]:
    """Return {node_id: 'done'|'ready'|'blocked'} from plan + directory state."""
    done = {n.id for n in plan.nodes if node_done(n, task_dir)}
    states: dict[str, str] = {}
    for node in plan.nodes:
        if node.id in done:
            states[node.id] = "done"
        elif all(dep in done for dep in node.depends_on):
            states[node.id] = "ready"
        else:
            states[node.id] = "blocked"
    return states


def mark_node_done(task_dir: Path, node_id: str) -> Path:
    """Write a completion marker for a node (idempotent)."""
    nodes_dir = task_dir / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    marker = nodes_dir / f"{node_id}.done"
    marker.write_text(isoformat_z(utc_now()) + "\n", encoding="utf-8")
    return marker


def provision_task_tree(workspace: AipWorkspace, task_id: str) -> Path:
    """Create the task-local sub-workspace (artifacts/, scratch/, nodes/, audit.jsonl)."""
    base = task_tree(workspace, task_id)
    for sub in ("artifacts", "scratch", "nodes"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    audit = base / "audit.jsonl"
    if not audit.exists():
        audit.touch()
    return base


def append_audit(workspace: AipWorkspace, task_id: str, event: str, **fields) -> dict:
    """Append a record to the task-local audit.jsonl (high-resolution history)."""
    base = provision_task_tree(workspace, task_id)
    record = {"ts": isoformat_z(utc_now()), "task_id": task_id, "event": event}
    record.update({k: v for k, v in fields.items() if v is not None})
    with (base / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    return record


def _existing_queue_task_ids(queue) -> set[str]:
    ids: set[str] = set()
    for stage in ("pending", "claimed", "done", "failed"):
        for task in queue.list_tasks(stage):
            ids.add(task.task_id)
    return ids


def node_task_id(plan_task_id: str, node_id: str) -> str:
    return f"{plan_task_id}.{node_id}"


def project_plan(plan: Plan, queue, *, requested_by: str = "orchestrator") -> list[str]:
    """Project ready nodes into the pending queue. Returns the projected node ids.

    A node is projected once: nodes already represented in any queue stage are
    skipped, so re-running projection only adds newly-ready nodes.
    """
    task_dir = task_tree(queue.workspace, plan.task_id)
    provision_task_tree(queue.workspace, plan.task_id)
    states = node_states(plan, task_dir)
    existing = _existing_queue_task_ids(queue)
    projected: list[str] = []
    for node in plan.nodes:
        if states[node.id] != "ready":
            continue
        ntid = node_task_id(plan.task_id, node.id)
        if ntid in existing:
            continue
        body_lines = []
        if node.outputs:
            body_lines.append("outputs: " + ", ".join(node.outputs))
        if node.leaf_tools:
            body_lines.append("leaf_tools: " + ", ".join(node.leaf_tools))
        queue.create_task(
            description=f"{plan.goal}: {node.id}" if plan.goal else node.id,
            task_id=ntid,
            requested_by=requested_by,
            body="\n".join(body_lines),
            metadata={
                "plan_task_id": plan.task_id,
                "node_id": node.id,
                "leaf_tools": ",".join(node.leaf_tools),
            },
        )
        append_audit(queue.workspace, plan.task_id, "projected", node=node.id, queue_task=ntid)
        projected.append(node.id)
    return projected
