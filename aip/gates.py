"""Human-escalation gate records (spec §"Human escalation shims").

When a PreToolUse command requires human approval (EXTERNAL risk, production
writes), the shim writes a gate record to ``workspace/gates/`` and blocks the
agent until a human (or the orchestrator) writes ``approved: true`` to the gate
file. Gate resolution is file-polled, not a blocking dialog, so it fits the
shim's non-blocking poll loop and the workspace's file-truth model.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .workspace import isoformat_z, utc_now

logger = logging.getLogger("aip.gates")


def gates_dir(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / "gates"


def read_gate(path: str | Path) -> dict | None:
    """Read a gate record, or None if missing/unreadable."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read gate record %s: %s", p, exc)
        return None


def open_gate_path(workspace_root: str | Path, agent: str) -> Path | None:
    """Return the path of the agent's open (unresolved) gate, if any."""
    d = gates_dir(workspace_root)
    if not d.is_dir():
        return None
    for path in sorted(d.glob(f"gate-{agent}-*.json")):
        record = read_gate(path)
        if record is not None and not record.get("resolved", False):
            return path
    return None


def write_gate(workspace_root: str | Path, agent: str, *, tool: str, command: str) -> Path:
    """Create a pending human-gate record and return its path.

    The record starts with ``approved: null`` and ``resolved: false``; a human
    or the orchestrator flips ``approved`` to true/false to release the agent.
    """
    d = gates_dir(workspace_root)
    d.mkdir(parents=True, exist_ok=True)
    idx = len(list(d.glob(f"gate-{agent}-*.json"))) + 1
    path = d / f"gate-{agent}-{idx:03d}.json"
    record = {
        "agent": agent,
        "tool": tool,
        "command": command,
        "requires_approval": True,
        "approved": None,
        "resolved": False,
        "created_at": isoformat_z(utc_now()),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_gate(path: str | Path, *, approved: bool) -> dict | None:
    """Mark a gate resolved with the given decision. Returns the updated record."""
    record = read_gate(path)
    if record is None:
        return None
    record["approved"] = bool(approved)
    record["resolved"] = True
    record["resolved_at"] = isoformat_z(utc_now())
    Path(path).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def gate_ref(workspace_root: str | Path, path: str | Path) -> str:
    """Best-effort workspace-relative ref for a gate file (for event logs)."""
    p = Path(path)
    try:
        return str(p.relative_to(Path(workspace_root).parent))
    except ValueError:
        return str(p)
