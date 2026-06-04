from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

VALID_STATUSES = frozenset({"working", "blocked", "failed", "finished", "idle"})
_TASK_ID_PATTERN = re.compile(r"task-(\d+)", re.IGNORECASE)


logger = logging.getLogger("aip.workspace")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_isoformat(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sanitize_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-_.")
    return cleaned or "unnamed"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class AipWorkspace:
    def __init__(self, root: str | Path, *, interest_registry: Any | None = None) -> None:
        self.root = Path(root)
        self.interest_registry = interest_registry

    @property
    def summaries_dir(self) -> Path:
        return self.root / "summaries"

    @property
    def status_dir(self) -> Path:
        return self.root / "status"

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def pending_dir(self) -> Path:
        return self.tasks_dir / "pending"

    @property
    def claimed_dir(self) -> Path:
        return self.tasks_dir / "claimed"

    @property
    def done_dir(self) -> Path:
        return self.tasks_dir / "done"

    @property
    def failed_dir(self) -> Path:
        return self.tasks_dir / "failed"

    @property
    def task_packets_dir(self) -> Path:
        return self.tasks_dir / "packets"

    @property
    def transcripts_dir(self) -> Path:
        return self.root / "transcripts"

    @property
    def gates_dir(self) -> Path:
        return self.root / "gates"

    @property
    def locks_dir(self) -> Path:
        return self.root / "locks"

    @property
    def route_requests_dir(self) -> Path:
        return self.root / "route-requests"

    @property
    def route_decisions_dir(self) -> Path:
        return self.root / "route-decisions"

    @property
    def dream_candidates_dir(self) -> Path:
        return self.root / "dream-candidates"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def agent_tree_path(self) -> Path:
        return self.root / "agent_tree.json"

    @property
    def interests_path(self) -> Path:
        return self.root / "interests.json"

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.summaries_dir,
            self.status_dir,
            self.pending_dir,
            self.claimed_dir,
            self.done_dir,
            self.failed_dir,
            self.task_packets_dir,
            self.transcripts_dir,
            self.gates_dir,
            self.locks_dir,
            self.route_requests_dir,
            self.route_decisions_dir,
            self.dream_candidates_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        if not self.agent_tree_path.exists():
            atomic_write_json(self.agent_tree_path, {})

    def read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Corrupt JSON in %s: %s", path, exc)
            return {}

    def read_status(self, agent_name: str) -> dict[str, Any]:
        self.ensure()
        return self.read_json(self.status_dir / f"{sanitize_component(agent_name)}.json")

    def write_status(
        self,
        agent_name: str,
        *,
        remove_keys: tuple[str, ...] = (),
        **updates: Any,
    ) -> dict[str, Any]:
        self.ensure()
        status_path = self.status_dir / f"{sanitize_component(agent_name)}.json"
        snapshot = self.read_json(status_path)
        for key in remove_keys:
            snapshot.pop(key, None)
        snapshot.update({key: value for key, value in updates.items() if value is not None})
        snapshot["agent"] = agent_name
        snapshot["updated_at"] = isoformat_z(utc_now())
        atomic_write_json(status_path, snapshot)
        return snapshot

    def append_event(self, agent_name: str, event: str, **fields: Any) -> dict[str, Any]:
        self.ensure()
        payload = {
            "ts": isoformat_z(utc_now()),
            "agent": agent_name,
            "event": event,
        }
        payload.update({key: value for key, value in fields.items() if value is not None})
        if self.interest_registry is not None:
            from .interest_maps import event_kind_from_aip_event, priority_from_aip_event
            kind = event_kind_from_aip_event(event)
            priority = priority_from_aip_event(payload)
            task_id = payload.get("task", "")
            payload["interested_agents"] = self.interest_registry.interested_agents(
                kind, priority=priority, task_id=task_id
            )
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return payload

    def tail_events(self, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure()
        with self.events_path.open("r", encoding="utf-8") as handle:
            lines = deque(handle, maxlen=limit)
        events: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Skipping corrupt event line: %s", exc)
        return events

    def export_summary(
        self,
        agent_name: str,
        content: str,
        *,
        timestamp: datetime | None = None,
    ) -> Path:
        self.ensure()
        stamp = (timestamp or utc_now()).astimezone(timezone.utc)
        filename = f"{sanitize_component(agent_name)}-{stamp.strftime('%m%d-%H%M%S')}.md"
        summary_path = self.summaries_dir / filename
        final_content = content if content.endswith("\n") else f"{content}\n"
        atomic_write_text(summary_path, final_content)
        return summary_path

    def write_heartbeat(self, agent_name: str, *, status: str = "working", extra: dict | None = None) -> Path:
        """Write a heartbeat file for an agent. Updated frequently during long tasks."""
        self.ensure()
        content_lines = [
            f"# HEARTBEAT — {agent_name}",
            f"agent: {agent_name}",
            f"status: {status}",
            f"ts: {isoformat_z(utc_now())}",
        ]
        if extra:
            for key, value in extra.items():
                content_lines.append(f"{key}: {value}")
        content = "\n".join(content_lines) + "\n"
        path = self.root / f"HEARTBEAT-{sanitize_component(agent_name)}.md"
        atomic_write_text(path, content)
        return path

    def write_presence(self, agent_name: str) -> Path:
        """Write a presence sentinel file. Exists while agent is active."""
        self.ensure()
        safe_name = sanitize_component(agent_name)
        path = self.root / f"{safe_name}.present"
        path.touch()
        return path

    def clear_presence(self, agent_name: str) -> None:
        """Remove presence sentinel when agent exits."""
        safe_name = sanitize_component(agent_name)
        path = self.root / f"{safe_name}.present"
        path.unlink(missing_ok=True)

    def _heartbeat_age_seconds(self, safe_name: str) -> float | None:
        """Return how old the heartbeat file is in seconds, or None if absent."""
        heartbeat_path = self.root / f"HEARTBEAT-{safe_name}.md"
        if not heartbeat_path.exists():
            return None
        for line in heartbeat_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ts: "):
                ts_str = line[4:].strip()
                try:
                    ts = parse_isoformat(ts_str)
                    return (utc_now() - ts).total_seconds()
                except (ValueError, TypeError):
                    return None
        return None

    def _last_heartbeat_ts(self, safe_name: str) -> str | None:
        """Return the raw heartbeat timestamp string, or None if absent."""
        heartbeat_path = self.root / f"HEARTBEAT-{safe_name}.md"
        if not heartbeat_path.exists():
            return None
        for line in heartbeat_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ts: "):
                return line[4:].strip()
        return None

    def derive_activity(
        self,
        agent_name: str,
        *,
        staleness_threshold_seconds: float = 120,
        write: bool = False,
    ) -> dict:
        """Derive a 5-state activity record for an agent from filesystem evidence.

        States (spec §"Derived Activity State"):
          running      — heartbeat fresh + at least one claimed task
          needs-input  — claim active + open gate record present
          stalled      — claim present but heartbeat older than staleness threshold
          idle         — alive (present sentinel) with no active claim
          done         — terminal: no active claim, not present, status == finished

        When ``write=True``, persists the record to
        ``workspace/status/{agent}-activity.json``.
        """
        self.ensure()
        safe_name = sanitize_component(agent_name)

        status = self.read_json(self.status_dir / f"{safe_name}.json")
        present = (self.root / f"{safe_name}.present").exists()
        last_heartbeat = self._last_heartbeat_ts(safe_name)
        heartbeat_age = self._heartbeat_age_seconds(safe_name)

        # Determine whether there is at least one claimed task for this agent.
        claimed_task: str | None = None
        for path in sorted(self.claimed_dir.glob(f"{safe_name}-*.md")):
            claimed_task = path.name
            break

        # Check for an open (unresolved) gate.
        open_gate: str | None = None
        if self.gates_dir.is_dir():
            for path in sorted(self.gates_dir.glob(f"gate-{safe_name}-*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not data.get("resolved", False):
                        open_gate = path.name
                        break
                except (json.JSONDecodeError, OSError):
                    pass

        # Compute 5-state.
        has_claim = claimed_task is not None
        heartbeat_stale = (
            heartbeat_age is not None
            and heartbeat_age > staleness_threshold_seconds
        )

        if has_claim and open_gate:
            state = "needs-input"
        elif has_claim and heartbeat_stale:
            state = "stalled"
        elif has_claim:
            state = "running"
        elif present or status.get("status") == "working":
            state = "idle"
        elif status.get("status") == "finished":
            state = "done"
        else:
            state = "idle"

        derived_from: dict[str, Any] = {}
        if last_heartbeat:
            derived_from["heartbeat"] = f"HEARTBEAT-{safe_name}.md"
        if claimed_task:
            derived_from["claim"] = f"tasks/claimed/{claimed_task}"
        if open_gate:
            derived_from["gate"] = f"gates/{open_gate}"

        record: dict[str, Any] = {
            "agent": agent_name,
            "state": state,
            "derived_from": derived_from,
            "derived_at": isoformat_z(utc_now()),
        }
        # Include auxiliary fields for diagnostics (non-authoritative).
        if last_heartbeat:
            record["last_heartbeat"] = last_heartbeat
        if heartbeat_age is not None:
            record["heartbeat_age_seconds"] = round(heartbeat_age, 1)

        if write:
            activity_path = self.status_dir / f"{safe_name}-activity.json"
            atomic_write_json(activity_path, record)

        return record

    @contextmanager
    def _lock_guard(self, safe_resource: str):
        guard_path = self.locks_dir / f".{safe_resource}.guard"
        guard_path.parent.mkdir(parents=True, exist_ok=True)
        with guard_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def acquire_lock(self, resource: str, holder: str, *, ttl_seconds: int = 300) -> bool:
        """Try to acquire a resource lock. Returns True on success, False if already held."""
        self.ensure()
        safe_resource = sanitize_component(resource)
        lock_path = self.locks_dir / f"{safe_resource}.lock"
        with self._lock_guard(safe_resource):
            if lock_path.exists():
                try:
                    data = json.loads(lock_path.read_text(encoding="utf-8"))
                    expires = parse_isoformat(data.get("expires_at", "1970-01-01T00:00:00Z"))
                    if utc_now() < expires:
                        return False
                except (json.JSONDecodeError, ValueError):
                    pass
            acquired = utc_now()
            payload = {
                "resource": resource,
                "holder": holder,
                "acquired_at": isoformat_z(acquired),
                "expires_at": isoformat_z(acquired + timedelta(seconds=ttl_seconds)),
                "ttl_seconds": ttl_seconds,
            }
            atomic_write_json(lock_path, payload)
            return True

    def release_lock(self, resource: str, holder: str) -> bool:
        """Release a resource lock. Returns True if lock was held by this holder."""
        safe_resource = sanitize_component(resource)
        lock_path = self.locks_dir / f"{safe_resource}.lock"
        with self._lock_guard(safe_resource):
            if not lock_path.exists():
                return False
            try:
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                if data.get("holder") != holder:
                    return False
            except (json.JSONDecodeError, ValueError):
                return False
            lock_path.unlink(missing_ok=True)
            return True

    def append_dream_candidate(self, *, source: str, content: str, task_id: str | None = None, task_class: str | None = None, trigger_type: str = "large_task_completion") -> Path:
        """Append a dream candidate record for gitmem Dream pipeline ingestion."""
        self.ensure()
        import hashlib
        slug = hashlib.sha1(content.encode()).hexdigest()[:8]
        filename = f"{isoformat_z(utc_now()).replace(':', '-').replace('T', '-')[:16]}-{slug}.md"
        path = self.dream_candidates_dir / filename
        lines = [
            f"# Dream Candidate",
            f"trigger_type: {trigger_type}",
            f"source: {source}",
            f"ts: {isoformat_z(utc_now())}",
        ]
        if task_id:
            lines.append(f"task_id: {task_id}")
        if task_class:
            lines.append(f"task_class: {task_class}")
        lines.extend(["", content.strip()])
        atomic_write_text(path, "\n".join(lines) + "\n")
        return path

    def append_transcript_event(
        self,
        session_id: str,
        agent_name: str,
        event_type: str,
        **fields,
    ) -> dict:
        """Append a normalized event to workspace/transcripts/{session_id}.jsonl."""
        self.ensure()
        safe_session = sanitize_component(session_id)
        path = self.transcripts_dir / f"{safe_session}.jsonl"
        payload = {
            "ts": isoformat_z(utc_now()),
            "session_id": session_id,
            "agent": agent_name,
            "event": event_type,
            "schema_version": "0.6",
        }
        payload.update({k: v for k, v in fields.items() if v is not None})
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return payload

    def read_transcript(self, session_id: str, limit: int = 100) -> list[dict]:
        """Read the last N events from a session transcript."""
        self.ensure()
        safe_session = sanitize_component(session_id)
        path = self.transcripts_dir / f"{safe_session}.jsonl"
        if not path.exists():
            return []
        from collections import deque
        with path.open("r", encoding="utf-8") as handle:
            lines = deque(handle, maxlen=limit)
        events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                pass
        return events

    def task_packet_path(self, task_id: str) -> Path:
        return self.task_packets_dir / f"{sanitize_component(task_id)}.json"

    def write_task_packet(self, task_id: str, packet: dict[str, Any]) -> Path:
        """Persist an IMX task packet under workspace/tasks/packets/."""
        self.ensure()
        payload = dict(packet)
        payload["task_id"] = task_id
        path = self.task_packet_path(task_id)
        atomic_write_json(path, payload)
        return path

    def read_task_packet(self, task_id: str) -> dict[str, Any]:
        self.ensure()
        return self.read_json(self.task_packet_path(task_id))

    def write_route_request(
        self,
        task_id: str,
        *,
        task_class: str,
        risk_tier: str,
        requester: str,
        budget: dict | None = None,
        task_packet_ref: str | None = None,
        capability_profile: str | None = None,
        chain_depth: int | None = None,
    ) -> Path:
        """Write a route request for IMX to resolve."""
        self.ensure()
        payload = {
            "schema_version": "0.6",
            "task_id": task_id,
            "task_class": task_class,
            "risk_tier": risk_tier,
            "requester": requester,
            "requested_at": isoformat_z(utc_now()),
            "status": "pending",
        }
        if budget:
            payload["budget"] = budget
        if task_packet_ref:
            payload["task_packet_ref"] = task_packet_ref
        if capability_profile:
            payload["capability_profile"] = capability_profile
        if chain_depth is not None:
            payload["chain_depth"] = chain_depth
        path = self.route_requests_dir / f"{task_id}.json"
        atomic_write_json(path, payload)
        return path

    def write_route_decision(self, task_id: str, *, node_id: str, profile: str, rationale: str, decided_by: str = "imx-router") -> Path:
        """Write an IMX route decision record."""
        self.ensure()
        payload = {
            "schema_version": "0.6",
            "route_decision_id": f"rd-{task_id}",
            "task_id": task_id,
            "node_id": node_id,
            "profile": profile,
            "rationale": rationale,
            "decided_by": decided_by,
            "decided_at": isoformat_z(utc_now()),
        }
        path = self.route_decisions_dir / f"{task_id}.json"
        atomic_write_json(path, payload)
        return path

    def next_task_id(self) -> str:
        self.ensure()
        max_id = 0
        for directory in (self.pending_dir, self.claimed_dir, self.done_dir, self.failed_dir):
            for path in directory.glob("*.md"):
                match = _TASK_ID_PATTERN.search(path.name)
                if match:
                    max_id = max(max_id, int(match.group(1)))
        return f"task-{max_id + 1:03d}"

    def read_agent_tree(self) -> dict[str, Any]:
        self.ensure()
        data = self.read_json(self.agent_tree_path)
        if not isinstance(data, dict):
            raise ValueError("agent_tree.json must contain a JSON object")
        return data

    def write_agent_tree(self, tree: dict[str, Any]) -> dict[str, Any]:
        self.ensure()
        atomic_write_json(self.agent_tree_path, tree)
        return tree

    def ensure_agent_node(
        self,
        agent_name: str,
        *,
        depth: int,
        parent: str | None,
        tmux_window: str,
        **extra: Any,
    ) -> dict[str, Any]:
        tree = self.read_agent_tree()
        node = tree.get(agent_name)
        if node is None:
            node = {
                "depth": depth,
                "parent": parent,
                "children": [],
                "tmux_window": tmux_window,
            }
            tree[agent_name] = node
        else:
            node.setdefault("children", [])
            node["depth"] = depth
            node["parent"] = parent
            node["tmux_window"] = tmux_window
        for key, value in extra.items():
            if value is not None:
                node[key] = value
        self.write_agent_tree(tree)
        return node

    def add_agent_child(
        self,
        parent_name: str,
        child_name: str,
        *,
        depth: int,
        tmux_window: str,
        **extra: Any,
    ) -> dict[str, Any]:
        tree = self.read_agent_tree()
        if parent_name not in tree:
            raise ValueError(f"Unknown parent agent: {parent_name}")
        if child_name in tree:
            raise ValueError(f"Agent already exists in tree: {child_name}")

        parent = tree[parent_name]
        parent.setdefault("children", [])
        parent["children"].append(child_name)

        child = {
            "depth": depth,
            "parent": parent_name,
            "children": [],
            "tmux_window": tmux_window,
        }
        for key, value in extra.items():
            if value is not None:
                child[key] = value
        tree[child_name] = child
        self.write_agent_tree(tree)
        return child

    def agent_subtree_postorder(self, agent_name: str) -> list[str]:
        tree = self.read_agent_tree()
        if agent_name not in tree:
            return [agent_name]

        ordered: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited or name not in tree:
                return
            visited.add(name)
            node = tree[name]
            for child in node.get("children", []):
                visit(child)
            ordered.append(name)

        visit(agent_name)
        return ordered

    def remove_agent_node(self, agent_name: str) -> None:
        tree = self.read_agent_tree()
        node = tree.get(agent_name)
        if node is None:
            return
        parent_name = node.get("parent")
        if parent_name in tree:
            parent = tree[parent_name]
            parent["children"] = [child for child in parent.get("children", []) if child != agent_name]
        del tree[agent_name]
        self.write_agent_tree(tree)
