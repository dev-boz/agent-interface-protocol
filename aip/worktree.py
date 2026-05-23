"""Worktree helper for AIP mutation tasks.

Provisions `.worktrees/{task_id}/` via git worktree add and registers
the mapping in workspace/agent-map.json.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .workspace import AipWorkspace, atomic_write_json, isoformat_z, utc_now


@dataclass
class WorktreeInfo:
    task_id: str
    agent_id: str
    worktree_path: str
    branch: str
    created_at: str


def _agent_map_path(workspace: AipWorkspace) -> Path:
    return workspace.root / "agent-map.json"


def _load_agent_map(workspace: AipWorkspace) -> dict:
    path = _agent_map_path(workspace)
    if not path.exists():
        return {"schema_version": "0.6", "agents": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"schema_version": "0.6", "agents": []}


def provision_worktree(
    workspace: AipWorkspace,
    task_id: str,
    agent_id: str,
    *,
    repo_root: Path | None = None,
    branch_prefix: str = "task",
) -> WorktreeInfo:
    """Create a git worktree for a mutation task and register it in agent-map.json.

    Returns WorktreeInfo with the worktree path and branch name.
    Raises RuntimeError if git worktree add fails.
    """
    repo = repo_root or Path.cwd()
    safe_task = task_id.replace("/", "-").replace("\\", "-")
    branch = f"{branch_prefix}/{safe_task}"
    worktree_path = repo / ".worktrees" / safe_task

    if not worktree_path.exists():
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path)],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

    info = WorktreeInfo(
        task_id=task_id,
        agent_id=agent_id,
        worktree_path=str(worktree_path),
        branch=branch,
        created_at=isoformat_z(utc_now()),
    )

    # Register in agent-map.json
    agent_map = _load_agent_map(workspace)
    agents = agent_map.setdefault("agents", [])
    # Remove stale entry for same task
    agents[:] = [a for a in agents if a.get("task_id") != task_id]
    agents.append({
        "agent_id": agent_id,
        "task_id": task_id,
        "worktree": str(worktree_path.relative_to(repo)),
        "branch": branch,
        "created_at": info.created_at,
    })
    atomic_write_json(_agent_map_path(workspace), agent_map)

    return info


def remove_worktree(
    workspace: AipWorkspace,
    task_id: str,
    *,
    repo_root: Path | None = None,
    force: bool = False,
) -> bool:
    """Remove a git worktree and its agent-map entry. Returns True if removed."""
    repo = repo_root or Path.cwd()
    safe_task = task_id.replace("/", "-").replace("\\", "-")
    worktree_path = repo / ".worktrees" / safe_task

    removed = False
    if worktree_path.exists():
        cmd = ["git", "worktree", "remove", str(worktree_path)]
        if force:
            cmd.append("--force")
        result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        removed = result.returncode == 0

    # Update agent-map regardless
    agent_map = _load_agent_map(workspace)
    before = len(agent_map.get("agents", []))
    agent_map["agents"] = [a for a in agent_map.get("agents", []) if a.get("task_id") != task_id]
    if len(agent_map["agents"]) < before:
        atomic_write_json(_agent_map_path(workspace), agent_map)

    return removed


def list_worktrees(workspace: AipWorkspace) -> list[dict]:
    """Return all registered worktree entries from agent-map.json."""
    return _load_agent_map(workspace).get("agents", [])
