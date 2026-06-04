"""Agent interest maps per AIP spec §10 — selective notification delivery."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


class InterestKind:
    TASK_STATUS = "task_status"
    TOOL_USE = "tool_use"
    GATE = "gate"
    EXPORT = "export"
    HEARTBEAT = "heartbeat"
    ROUTE = "route"
    HUMAN_GATE = "human_gate"
    ALL = "all"


class Priority:
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class InterestSubscription:
    agent_name: str
    kinds: list[str] = field(default_factory=list)
    min_priority: int = Priority.NORMAL
    task_filter: str = ""
    registered_at: str = ""


class InterestRegistry:
    def __init__(self, registry_path: Path | None = None) -> None:
        self._path = registry_path
        self._subscriptions: dict[str, InterestSubscription] = {}
        self._load()

    def register(self, subscription: InterestSubscription) -> None:
        self._subscriptions[subscription.agent_name] = subscription
        self.save()

    def unregister(self, agent_name: str) -> None:
        self._subscriptions.pop(agent_name, None)
        self.save()

    def interested_agents(
        self,
        kind: str,
        priority: int = Priority.NORMAL,
        task_id: str = "",
    ) -> list[str]:
        results = []
        for agent_name, sub in self._subscriptions.items():
            kind_match = kind in sub.kinds or InterestKind.ALL in sub.kinds
            priority_match = priority >= sub.min_priority
            task_match = not sub.task_filter or task_id.startswith(sub.task_filter)
            if kind_match and priority_match and task_match:
                results.append(agent_name)
        return sorted(results)

    def save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        payload = {agent: asdict(sub) for agent, sub in self._subscriptions.items()}
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            return
        for agent_name, data in raw.items():
            try:
                sub = InterestSubscription(
                    agent_name=data["agent_name"],
                    kinds=data.get("kinds", []),
                    min_priority=data.get("min_priority", Priority.NORMAL),
                    task_filter=data.get("task_filter", ""),
                    registered_at=data.get("registered_at", ""),
                )
                self._subscriptions[agent_name] = sub
            except (KeyError, TypeError):
                pass  # skip malformed entries silently


def event_kind_from_aip_event(event_type: str) -> str:
    """Map an AIP event type string to an InterestKind constant."""
    if event_type in ("status", "task"):
        return InterestKind.TASK_STATUS
    if event_type == "tool":
        return InterestKind.TOOL_USE
    if event_type == "human_gate":
        return InterestKind.HUMAN_GATE
    if event_type == "export":
        return InterestKind.EXPORT
    if event_type == "heartbeat":
        return InterestKind.HEARTBEAT
    if event_type in ("route_request", "route_decision"):
        return InterestKind.ROUTE
    if event_type == "gate":
        return InterestKind.GATE
    return InterestKind.ALL


def priority_from_aip_event(event: dict) -> int:
    """Infer a Priority integer from an AIP event dict."""
    if event.get("event") == "human_gate":
        return Priority.CRITICAL
    if event.get("status") in ("failed", "blocked"):
        return Priority.HIGH
    if event.get("event") in ("export", "gate"):
        return Priority.HIGH
    if event.get("event") == "heartbeat":
        return Priority.LOW
    return Priority.NORMAL
