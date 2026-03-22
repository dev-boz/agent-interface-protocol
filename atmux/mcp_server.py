from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from typing import Any

from . import __version__
from .tasks import TaskQueue
from .tmux import TmuxController
from .workspace import AtmuxWorkspace, VALID_STATUSES, sanitize_component

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("atmux-mcp")

INTERESTS_SCHEMA = {
    "type": "object",
    "description": "Agent interest map for targeted inter-agent coordination.",
    "properties": {
        "agents": {
            "type": "object",
            "description": "Map of agent names to priority (high/medium/low).",
            "additionalProperties": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
        },
        "events": {
            "type": "object",
            "description": "Map of event patterns (e.g. 'status:failed') to priority.",
            "additionalProperties": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
        },
        "summaries": {
            "type": "object",
            "description": "Map of summary glob patterns (e.g. 'coder-*') to priority.",
            "additionalProperties": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
        },
    },
    "additionalProperties": False,
}

TOOL_SPECS = (
    {
        "name": "report_status",
        "description": "Write the current agent status snapshot and append a status event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": sorted(VALID_STATUSES)},
                "message": {"type": "string"},
            },
            "required": ["status"],
            "additionalProperties": False,
        },
    },
    {
        "name": "export_summary",
        "description": "Persist a markdown summary for other agents and append an export event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "register_capabilities",
        "description": (
            "Merge capability labels and optional interest maps into the agent status file "
            "and append an event. Interest maps define what this agent cares about — "
            "which other agents, event types, and summary patterns to watch, with priority "
            "levels (high, medium, low)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "interests": INTERESTS_SCHEMA,
            },
            "required": ["capabilities"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_task",
        "description": "Create a queued task in workspace/tasks/pending and append an event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_role": {"type": "string"},
                "task_description": {"type": "string"},
                "context": {"type": "string"},
                "priority": {"type": "string"},
            },
            "required": ["task_description"],
            "additionalProperties": False,
        },
    },
    {
        "name": "report_progress",
        "description": "Merge lightweight progress details into the agent status file and append an event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "progress": {"type": "string"},
                "percentage": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": ["progress"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for",
        "description": "Block until a matching event appears in workspace/events.jsonl or timeout expires.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_filter": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    ]
                },
                "timeout": {"type": "number", "minimum": 0},
            },
            "required": ["event_filter"],
            "additionalProperties": False,
        },
    },
    {
        "name": "spawn_teammate",
        "description": "Spawn a new tmux-backed teammate, register it in the agent tree, and write initial status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "cli_type": {"type": "string"},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "interests": INTERESTS_SCHEMA,
                "parent_id": {"type": "string"},
                "depth": {"type": "integer", "minimum": 0},
            },
            "required": ["name", "cli_type", "capabilities"],
            "additionalProperties": False,
        },
    },
)


class ToolInputError(ValueError):
    """Raised when tool input does not satisfy ATMUX expectations."""


class AtmuxToolRuntime:
    def __init__(
        self,
        workspace_root: str,
        agent_name: str,
        *,
        session_name: str = "atmux",
        tmux_controller: TmuxController | None = None,
        poll_interval: float = 0.1,
        max_depth: int = 3,
        max_breadth: int = 4,
    ) -> None:
        self.workspace = AtmuxWorkspace(workspace_root)
        self.workspace.ensure()
        self.queue = TaskQueue(self.workspace)
        self.agent_name = agent_name
        self.session_name = session_name
        self.tmux = tmux_controller or TmuxController(session_name=session_name)
        self.poll_interval = poll_interval
        self.max_depth = max_depth
        self.max_breadth = max_breadth

    def execute(self, name: str, arguments: dict[str, Any] | None) -> str:
        args = arguments or {}
        if name == "report_status":
            return self.report_status(args["status"], message=args.get("message"))
        if name == "export_summary":
            return self.export_summary(args["content"], task_id=args.get("task_id"))
        if name == "register_capabilities":
            return self.register_capabilities(args["capabilities"], interests=args.get("interests"))
        if name == "request_task":
            return self.request_task(
                task_description=args["task_description"],
                target_role=args.get("target_role"),
                context=args.get("context"),
                priority=args.get("priority"),
            )
        if name == "report_progress":
            return self.report_progress(args["progress"], percentage=args.get("percentage"))
        if name == "wait_for":
            return self.wait_for(args["event_filter"], timeout=args.get("timeout"))
        if name == "spawn_teammate":
            return self.spawn_teammate(
                name=args["name"],
                cli_type=args["cli_type"],
                capabilities=args["capabilities"],
                interests=args.get("interests"),
                parent_id=args.get("parent_id"),
                depth=args.get("depth"),
            )
        raise ToolInputError(f"Unknown tool: {name}")

    def report_status(self, status: str, *, message: str | None = None) -> str:
        if status not in VALID_STATUSES:
            raise ToolInputError(f"Invalid status: {status}")
        remove_keys = ("message",) if message is None else ()
        snapshot = self.workspace.write_status(
            self.agent_name,
            remove_keys=remove_keys,
            status=status,
            message=message,
        )
        self.workspace.append_event(self.agent_name, "status", status=status, message=message)
        return json.dumps(snapshot, indent=2)

    def export_summary(self, content: str, *, task_id: str | None = None) -> str:
        summary_path = self.workspace.export_summary(self.agent_name, content)
        relative_path = summary_path.relative_to(self.workspace.root).as_posix()
        self.workspace.append_event(self.agent_name, "export", file=relative_path, task_id=task_id)
        return json.dumps({"file": relative_path, "task_id": task_id}, indent=2)

    def register_capabilities(
        self,
        capabilities: list[str],
        *,
        interests: dict[str, Any] | None = None,
    ) -> str:
        cleaned = list(dict.fromkeys(capability.strip() for capability in capabilities if capability.strip()))
        if not cleaned:
            raise ToolInputError("Capabilities must contain at least one non-empty entry")
        validated_interests = self._validate_interests(interests) if interests else None
        updates: dict[str, Any] = {"capabilities": cleaned}
        if validated_interests is not None:
            updates["interests"] = validated_interests
        snapshot = self.workspace.write_status(self.agent_name, **updates)
        self.workspace.append_event(self.agent_name, "capabilities", capabilities=cleaned, interests=validated_interests)
        return json.dumps(snapshot, indent=2)

    @staticmethod
    def _validate_interests(interests: dict[str, Any]) -> dict[str, Any]:
        valid_priorities = {"high", "medium", "low"}
        valid_sections = {"agents", "events", "summaries"}
        result: dict[str, Any] = {}
        for section, mapping in interests.items():
            if section not in valid_sections:
                raise ToolInputError(f"Unknown interest section: {section}. Must be one of {valid_sections}")
            if not isinstance(mapping, dict):
                raise ToolInputError(f"Interest section '{section}' must be an object")
            cleaned: dict[str, str] = {}
            for key, priority in mapping.items():
                if priority not in valid_priorities:
                    raise ToolInputError(
                        f"Invalid priority '{priority}' for {section}.{key}. Must be one of {valid_priorities}"
                    )
                cleaned[key] = priority
            if cleaned:
                result[section] = cleaned
        return result

    def request_task(
        self,
        *,
        task_description: str,
        target_role: str | None = None,
        context: str | None = None,
        priority: str | None = None,
    ) -> str:
        task = self.queue.create_task(
            description=task_description,
            task_type="delegated",
            priority=priority or "normal",
            target_role=target_role,
            context=context,
            requested_by=self.agent_name,
        )
        return json.dumps(
            {
                "task_id": task.task_id,
                "path": f"tasks/pending/{task.task_id}.md",
            },
            indent=2,
        )

    def report_progress(self, progress: str, *, percentage: float | None = None) -> str:
        if percentage is not None and not 0 <= percentage <= 100:
            raise ToolInputError("percentage must be between 0 and 100")
        snapshot = self.workspace.write_status(
            self.agent_name,
            progress=progress,
            percentage=percentage,
        )
        self.workspace.append_event(
            self.agent_name,
            "progress",
            progress=progress,
            percentage=percentage,
        )
        return json.dumps(snapshot, indent=2)

    def wait_for(self, event_filter: str | list[str], *, timeout: float | None = None) -> str:
        filters = self._normalize_event_filters(event_filter)
        deadline = None if timeout is None else time.monotonic() + timeout
        offset = 0

        while True:
            offset, events = self._read_events_from_offset(offset)
            for event in events:
                matched_filter = self._match_event(event, filters)
                if matched_filter is None:
                    continue
                return json.dumps(
                    {
                        "matched_filter": matched_filter,
                        "event": event,
                        "timeout": False,
                    },
                    indent=2,
                )

            if deadline is not None and time.monotonic() >= deadline:
                return json.dumps(
                    {
                        "matched_filter": None,
                        "event": None,
                        "timeout": True,
                    },
                    indent=2,
                )

            time.sleep(self.poll_interval)

    def spawn_teammate(
        self,
        *,
        name: str,
        cli_type: str,
        capabilities: list[str],
        interests: dict[str, Any] | None = None,
        parent_id: str | None = None,
        depth: int | None = None,
    ) -> str:
        agent_id = sanitize_component(name)
        if not agent_id:
            raise ToolInputError("name must contain at least one valid character")
        command = cli_type.strip()
        if not command:
            raise ToolInputError("cli_type must contain a command")

        cleaned_capabilities = list(dict.fromkeys(capability.strip() for capability in capabilities if capability.strip()))
        if not cleaned_capabilities:
            raise ToolInputError("Capabilities must contain at least one non-empty entry")
        validated_interests = self._validate_interests(interests) if interests else None

        parent_agent = sanitize_component(parent_id) if parent_id else sanitize_component(self.agent_name)
        if parent_agent == agent_id:
            raise ToolInputError("spawn_teammate cannot use the new agent as its own parent")

        tree = self.workspace.read_agent_tree()
        if parent_agent not in tree:
            if parent_agent != sanitize_component(self.agent_name):
                raise ToolInputError(f"Unknown parent agent: {parent_agent}")
            self.workspace.ensure_agent_node(
                parent_agent,
                depth=0,
                parent=None,
                tmux_window=f"{self.session_name}:{parent_agent}",
            )
            tree = self.workspace.read_agent_tree()

        parent_node = tree[parent_agent]
        children = parent_node.setdefault("children", [])
        if len(children) >= self.max_breadth:
            raise ToolInputError(f"Parent agent already has max_breadth={self.max_breadth} children")

        computed_depth = int(parent_node["depth"]) + 1
        if depth is not None and depth != computed_depth:
            raise ToolInputError(f"depth {depth} does not match computed depth {computed_depth}")
        if computed_depth > self.max_depth:
            raise ToolInputError(f"Cannot spawn beyond max_depth={self.max_depth}")
        if agent_id in tree:
            raise ToolInputError(f"Agent already exists in tree: {agent_id}")

        self.tmux.spawn_window(agent_id, command)
        tmux_window = f"{self.session_name}:{agent_id}"
        try:
            self.workspace.add_agent_child(
                parent_agent,
                agent_id,
                depth=computed_depth,
                tmux_window=tmux_window,
                cli_type=command,
            )
            snapshot = self.workspace.write_status(
                agent_id,
                status="idle",
                capabilities=cleaned_capabilities,
                interests=validated_interests,
                parent_id=parent_agent,
                depth=computed_depth,
                cli_type=command,
                tmux_window=tmux_window,
            )
        except Exception:
            try:
                self.tmux.kill_window(agent_id)
            except Exception:
                logger.warning("Failed to kill orphaned window %s during rollback", agent_id)
            raise
        self.workspace.append_event(
            self.agent_name,
            "spawn",
            agent_id=agent_id,
            parent_id=parent_agent,
            depth=computed_depth,
            tmux_window=tmux_window,
            cli_type=command,
        )
        return json.dumps(
            {
                "agent_id": agent_id,
                "tmux_window": tmux_window,
                "depth": computed_depth,
                "parent_id": parent_agent,
                "status": snapshot["status"],
            },
            indent=2,
        )

    def _read_events_from_offset(self, offset: int) -> tuple[int, list[dict[str, Any]]]:
        with self.workspace.events_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            payload = handle.read()
            new_offset = handle.tell()
        events: list[dict[str, Any]] = []
        for line in payload.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                logger.warning("Skipping corrupt event line at offset %d", offset)
        return new_offset, events

    def _normalize_event_filters(self, event_filter: str | list[str]) -> list[tuple[str, dict[str, str]]]:
        raw_filters = [event_filter] if isinstance(event_filter, str) else list(event_filter)
        if not raw_filters:
            raise ToolInputError("event_filter must not be empty")

        normalized: list[tuple[str, dict[str, str]]] = []
        for raw_filter in raw_filters:
            if not isinstance(raw_filter, str) or not raw_filter.strip():
                raise ToolInputError("event_filter entries must be non-empty strings")
            criteria: dict[str, str] = {}
            for fragment in raw_filter.split(","):
                fragment = fragment.strip()
                if not fragment:
                    continue
                if ":" not in fragment:
                    raise ToolInputError(f"Invalid event filter fragment: {fragment}")
                key, value = fragment.split(":", 1)
                key = key.strip()
                value = value.strip()
                if not key or not value:
                    raise ToolInputError(f"Invalid event filter fragment: {fragment}")
                criteria[key] = value
            if not criteria:
                raise ToolInputError("event_filter must contain at least one key:value pair")
            normalized.append((raw_filter, criteria))
        return normalized

    @staticmethod
    def _match_event(event: dict[str, Any], filters: list[tuple[str, dict[str, str]]]) -> str | None:
        for raw_filter, criteria in filters:
            if all(str(event.get(key)) == value for key, value in criteria.items()):
                return raw_filter
        return None


def _import_mcp_sdk():
    """Lazy-import the MCP SDK so tests can still import the module without it."""
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
    return Server, stdio_server, TextContent, Tool


def create_mcp_server(runtime: AtmuxToolRuntime):
    """Build an MCP SDK server wired to our tool runtime."""
    Server, stdio_server, TextContent, Tool = _import_mcp_sdk()

    server = Server(
        "atmux-mcp",
        version=__version__,
        instructions=(
            "Use the ATMUX tools to publish status, progress, summaries, and delegated tasks "
            "into the shared workspace."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list:
        return [
            Tool(name=spec["name"], description=spec["description"], inputSchema=spec["inputSchema"])
            for spec in TOOL_SPECS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list:
        try:
            text = runtime.execute(name, arguments)
        except (KeyError, ToolInputError) as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]
        return [TextContent(type="text", text=text)]

    return server, stdio_server


async def _run(workspace: str, agent_name: str, session_name: str = "atmux") -> None:
    runtime = AtmuxToolRuntime(workspace, agent_name, session_name=session_name)
    server, stdio_server = create_mcp_server(runtime)
    logger.info("atmux-mcp starting (workspace=%s, agent=%s)", workspace, agent_name)

    async with stdio_server() as (read_stream, write_stream):
        logger.info("atmux-mcp stdio connected, serving tools")
        await server.run(read_stream, write_stream, server.create_initialization_options())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atmux-mcp")
    parser.add_argument("--workspace", default="workspace")
    parser.add_argument("--agent-name", default="agent")
    parser.add_argument("--session-name", default="atmux")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(_run(args.workspace, args.agent_name, args.session_name))
    except KeyboardInterrupt:
        logger.info("Server stopped")
        raise SystemExit(0)
    except Exception:
        logger.exception("Server crashed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
