from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALID_STATUSES = frozenset({"working", "blocked", "failed", "finished", "idle"})
_TASK_ID_PATTERN = re.compile(r"task-(\d+)", re.IGNORECASE)


logger = logging.getLogger("atmux.workspace")


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


class AtmuxWorkspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

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
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def agent_tree_path(self) -> Path:
        return self.root / "agent_tree.json"

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.summaries_dir,
            self.status_dir,
            self.pending_dir,
            self.claimed_dir,
            self.done_dir,
            self.failed_dir,
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
        stamp = (timestamp or utc_now()).astimezone(UTC)
        filename = f"{sanitize_component(agent_name)}-{stamp.strftime('%m%d-%H%M%S')}.md"
        summary_path = self.summaries_dir / filename
        final_content = content if content.endswith("\n") else f"{content}\n"
        atomic_write_text(summary_path, final_content)
        return summary_path

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
