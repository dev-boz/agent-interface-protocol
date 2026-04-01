from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .workspace import (
    AipWorkspace,
    atomic_write_text,
    isoformat_z,
    parse_isoformat,
    sanitize_component,
    utc_now,
)


class TaskError(RuntimeError):
    """Base task queue error."""


class TaskClaimError(TaskError):
    """Raised when a pending task can no longer be claimed."""


class TaskTransitionError(TaskError):
    """Raised when a claimed task cannot move to the target state."""


_VALID_TASK_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_task_id(task_id: str) -> str:
    """Reject task IDs that could escape the workspace via path traversal."""
    if not task_id or not _VALID_TASK_ID.match(task_id):
        raise TaskError(
            f"Invalid task_id: {task_id!r}. "
            "Must be non-empty and contain only alphanumerics, dots, hyphens, or underscores."
        )
    if task_id in (".", ".."):
        raise TaskError(f"Invalid task_id: {task_id!r}")
    return task_id


def _single_line(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


@dataclass
class Task:
    task_id: str
    description: str
    task_type: str = "general"
    priority: str = "normal"
    context: str = ""
    target_role: str | None = None
    created_at: str | None = None
    claimed_by: str | None = None
    lease_expires: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    body: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def to_markdown(self) -> str:
        lines = [f"# {self.task_id}"]
        ordered_fields = (
            ("type", self.task_type),
            ("priority", self.priority),
            ("target_role", self.target_role or ""),
            ("description", self.description),
            ("context", self.context),
            ("created_at", self.created_at or ""),
            ("claimed_by", self.claimed_by or ""),
            ("lease_expires", self.lease_expires or ""),
            ("blocked_by", ", ".join(self.blocked_by) if self.blocked_by else ""),
        )
        for key, value in ordered_fields:
            if value:
                lines.append(f"{key}: {value}")
        for key in sorted(self.metadata):
            value = self.metadata[key]
            if value:
                lines.append(f"{key}: {value}")
        if self.body:
            lines.extend(("", self.body.rstrip()))
        return "\n".join(lines).rstrip() + "\n"


def parse_task(text: str) -> Task:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise TaskError("Task file must start with '# task-id'")
    task_id = lines[0][2:].strip()

    metadata: dict[str, str] = {}
    body_start = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if not line.strip():
            body_start = index + 1
            break
        if ":" not in line:
            body_start = index
            break
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()

    body = "\n".join(lines[body_start:]).strip()
    known_fields = {
        "type",
        "priority",
        "target_role",
        "description",
        "context",
        "created_at",
        "claimed_by",
        "lease_expires",
        "blocked_by",
    }
    extra = {key: value for key, value in metadata.items() if key not in known_fields}

    blocked_by_raw = metadata.get("blocked_by", "")
    blocked_by = [tid.strip() for tid in blocked_by_raw.split(",") if tid.strip()] if blocked_by_raw else []

    return Task(
        task_id=task_id,
        task_type=metadata.get("type", "general"),
        priority=metadata.get("priority", "normal"),
        target_role=metadata.get("target_role") or None,
        description=metadata.get("description", ""),
        context=metadata.get("context", ""),
        created_at=metadata.get("created_at") or None,
        claimed_by=metadata.get("claimed_by") or None,
        lease_expires=metadata.get("lease_expires") or None,
        blocked_by=blocked_by,
        body=body,
        metadata=extra,
    )


class TaskQueue:
    def __init__(self, workspace: AipWorkspace) -> None:
        self.workspace = workspace
        self.workspace.ensure()

    def create_task(
        self,
        *,
        description: str,
        task_type: str = "general",
        priority: str = "normal",
        target_role: str | None = None,
        context: str | None = None,
        body: str = "",
        task_id: str | None = None,
        requested_by: str = "system",
        blocked_by: list[str] | None = None,
    ) -> Task:
        cleaned_blocked_by = []
        if blocked_by:
            for tid in blocked_by:
                stripped = tid.strip()
                if stripped:
                    _validate_task_id(stripped)
                    cleaned_blocked_by.append(stripped)
        task = Task(
            task_id=_validate_task_id(task_id) if task_id else self.workspace.next_task_id(),
            task_type=_single_line(task_type) or "general",
            priority=_single_line(priority) or "normal",
            target_role=_single_line(target_role) or None,
            description=_single_line(description),
            context=_single_line(context),
            created_at=isoformat_z(utc_now()),
            blocked_by=cleaned_blocked_by,
            body=body.strip(),
        )
        task_path = self.workspace.pending_dir / f"{task.task_id}.md"
        if task_path.exists():
            raise TaskError(f"Task already exists: {task.task_id}")
        atomic_write_text(task_path, task.to_markdown())
        self._append_task_event(
            requested_by,
            action="create",
            task=task,
            stage="pending",
        )
        return task

    def read_task(self, path: Path) -> Task:
        return parse_task(path.read_text(encoding="utf-8"))

    def list_tasks(self, stage: str = "pending") -> list[Task]:
        directory = self._stage_dir(stage)
        return [self.read_task(path) for path in sorted(directory.glob("*.md"))]

    def claim_task(
        self,
        task_id: str,
        agent_name: str,
        *,
        lease_seconds: int = 1800,
        now=None,
        actor_name: str | None = None,
    ) -> Task:
        _validate_task_id(task_id)
        if lease_seconds <= 0:
            raise TaskError(f"lease_seconds must be positive, got {lease_seconds}")

        # Check blocked_by before claiming
        source = self.workspace.pending_dir / f"{task_id}.md"
        if source.exists():
            candidate = self.read_task(source)
            if candidate.blocked_by:
                unresolved = self._unresolved_blockers(candidate.blocked_by)
                if unresolved:
                    raise TaskClaimError(
                        f"Task {task_id} is blocked by unresolved dependencies: {', '.join(unresolved)}"
                    )

        claimed_name = sanitize_component(agent_name)
        target = self.workspace.claimed_dir / f"{claimed_name}-{task_id}.md"
        try:
            os.rename(source, target)
        except FileNotFoundError as exc:
            raise TaskClaimError(f"Task is no longer pending: {task_id}") from exc

        claimed_task = self.read_task(target)
        claimed_at = now or utc_now()
        claimed_task.claimed_by = agent_name
        claimed_task.lease_expires = isoformat_z(claimed_at + timedelta(seconds=lease_seconds))
        atomic_write_text(target, claimed_task.to_markdown())
        self._append_task_event(
            actor_name or agent_name,
            action="claim",
            task=claimed_task,
            stage="claimed",
            lease_expires=claimed_task.lease_expires,
            claimed_by=agent_name,
        )
        return claimed_task

    def complete_task(self, task_id: str, *, agent_name: str | None = None, actor_name: str | None = None) -> Task:
        _validate_task_id(task_id)
        return self._transition_claimed_task(task_id, stage="done", agent_name=agent_name, actor_name=actor_name)

    def fail_task(self, task_id: str, *, agent_name: str | None = None, actor_name: str | None = None) -> Task:
        _validate_task_id(task_id)
        return self._transition_claimed_task(task_id, stage="failed", agent_name=agent_name, actor_name=actor_name)

    def reclaim_expired(self, *, now=None, actor_name: str = "system") -> list[str]:
        current_time = now or utc_now()
        reclaimed: list[str] = []
        for path in sorted(self.workspace.claimed_dir.glob("*.md")):
            task = self.read_task(path)
            if not task.lease_expires:
                continue
            if parse_isoformat(task.lease_expires) > current_time:
                continue
            task.claimed_by = None
            task.lease_expires = None
            task.metadata["reclaimed_at"] = isoformat_z(current_time)
            atomic_write_text(path, task.to_markdown())
            target = self.workspace.pending_dir / f"{task.task_id}.md"
            if target.exists():
                raise TaskTransitionError(f"Pending task already exists: {task.task_id}")
            os.rename(path, target)
            self._append_task_event(
                actor_name,
                action="reclaim",
                task=task,
                stage="pending",
                previous_stage="claimed",
            )
            reclaimed.append(task.task_id)
        return reclaimed

    def list_claimed_tasks(self, agent_name: str) -> list[Task]:
        claimed_name = sanitize_component(agent_name)
        return [self.read_task(path) for path in sorted(self.workspace.claimed_dir.glob(f"{claimed_name}-*.md"))]

    def list_claimable_tasks(self) -> list[Task]:
        """Return pending tasks that have no unresolved blocked_by dependencies."""
        tasks: list[Task] = []
        for path in sorted(self.workspace.pending_dir.glob("*.md")):
            task = self.read_task(path)
            if not task.blocked_by or not self._unresolved_blockers(task.blocked_by):
                tasks.append(task)
        return tasks

    def _unresolved_blockers(self, blocked_by: list[str]) -> list[str]:
        """Return task IDs from blocked_by that are NOT in done/."""
        unresolved: list[str] = []
        for blocker_id in blocked_by:
            done_path = self.workspace.done_dir / f"{blocker_id}.md"
            if not done_path.exists():
                unresolved.append(blocker_id)
        return unresolved

    def requeue_tasks_for_agent(
        self,
        agent_name: str,
        *,
        reason: str,
        handoff_summary: str | None = None,
        now=None,
        actor_name: str = "system",
    ) -> list[Task]:
        current_time = now or utc_now()
        interrupted: list[Task] = []
        claimed_name = sanitize_component(agent_name)
        for path in sorted(self.workspace.claimed_dir.glob(f"{claimed_name}-*.md")):
            task = self.read_task(path)
            task.claimed_by = None
            task.lease_expires = None
            task.metadata["interrupted_at"] = isoformat_z(current_time)
            task.metadata["interrupted_reason"] = reason
            if handoff_summary:
                task.metadata["handoff_summary"] = handoff_summary
            atomic_write_text(path, task.to_markdown())
            target = self.workspace.pending_dir / f"{task.task_id}.md"
            if target.exists():
                raise TaskTransitionError(f"Pending task already exists: {task.task_id}")
            os.rename(path, target)
            self._append_task_event(
                actor_name,
                action="requeue",
                task=task,
                stage="pending",
                previous_stage="claimed",
                previous_agent=agent_name,
                reason=reason,
                handoff_summary=handoff_summary,
            )
            interrupted.append(task)
        return interrupted

    def _transition_claimed_task(
        self,
        task_id: str,
        *,
        stage: str,
        agent_name: str | None,
        actor_name: str | None,
    ) -> Task:
        source = self._find_claimed_task(task_id, agent_name=agent_name)
        task = self.read_task(source)
        target = self._stage_dir(stage) / f"{task_id}.md"
        if target.exists():
            raise TaskTransitionError(f"Target task already exists: {target.name}")
        task.metadata[f"{stage}_at"] = isoformat_z(utc_now())
        atomic_write_text(source, task.to_markdown())
        os.rename(source, target)
        self._append_task_event(
            actor_name or agent_name or task.claimed_by or "system",
            action=stage,
            task=task,
            stage=stage,
            previous_stage="claimed",
        )
        return task

    def _find_claimed_task(self, task_id: str, *, agent_name: str | None) -> Path:
        if agent_name:
            path = self.workspace.claimed_dir / f"{sanitize_component(agent_name)}-{task_id}.md"
            if path.exists():
                return path
            raise TaskTransitionError(f"No claimed task for {agent_name}: {task_id}")

        matches = sorted(self.workspace.claimed_dir.glob(f"*-{task_id}.md"))
        if not matches:
            raise TaskTransitionError(f"No claimed task found: {task_id}")
        if len(matches) > 1:
            raise TaskTransitionError(f"Multiple claimed tasks found for {task_id}")
        return matches[0]

    def _stage_dir(self, stage: str) -> Path:
        mapping = {
            "pending": self.workspace.pending_dir,
            "claimed": self.workspace.claimed_dir,
            "done": self.workspace.done_dir,
            "failed": self.workspace.failed_dir,
        }
        try:
            return mapping[stage]
        except KeyError as exc:
            raise TaskError(f"Unknown task stage: {stage}") from exc

    def _append_task_event(
        self,
        agent_name: str,
        *,
        action: str,
        task: Task,
        stage: str,
        previous_stage: str | None = None,
        lease_expires: str | None = None,
        claimed_by: str | None = None,
        previous_agent: str | None = None,
        reason: str | None = None,
        handoff_summary: str | None = None,
    ) -> None:
        self.workspace.append_event(
            agent_name,
            "task",
            action=action,
            task_id=task.task_id,
            stage=stage,
            previous_stage=previous_stage,
            claimed_by=claimed_by or task.claimed_by,
            previous_agent=previous_agent,
            target_role=task.target_role,
            task_type=task.task_type,
            priority=task.priority,
            description=task.description,
            lease_expires=lease_expires or task.lease_expires,
            reason=reason,
            handoff_summary=handoff_summary,
        )
