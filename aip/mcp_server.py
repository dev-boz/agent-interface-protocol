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
from .workspace import AipWorkspace, VALID_STATUSES, sanitize_component

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("aip-mcp")

# CLI backends that support mid-stream message injection and their command
# templates.  The placeholder ``{message}`` is replaced with the actual
# notification text.  CLIs not listed here fall back to event-log-only
# delivery.
#
# Confirmed working via tmux send-keys:
#   claude-code:  /btw slash command injects into active thinking
#   codex:        typed text + Enter injects into current turn ("steer")
#   gemini:       model steering (experimental) — typed text + Enter while
#                 spinner is visible becomes a hint for the next reasoning step
#   copilot:      typed text + Enter sends at next gap in thinking/tool use;
#                 Ctrl+Enter enqueues for after current task finishes
#
# Known support but unreliable via tmux send-keys:
#   cursor:       Ctrl+Enter interrupts / Alt+Enter queues — special key
#                 sequences don't translate reliably through tmux
#
# Not yet shipped:
#   opencode:     PR #17233 adds steer mode setting (not released)
#   kilo:         fork of opencode — will inherit when upstream ships
#
# Queue only (next turn, not mid-stream):
#   amp:          command palette → queue — delivers after current turn
#
# No support:
#   kiro, mistral/vibe, qodo
INJECTION_COMMANDS: dict[str, str] = {
    "claude-code": "/btw {message}",
    "codex": "{message}",
    "copilot": "{message}",
    "gemini": "{message}",
    "cursor": "{message}",
    "qwen": "/btw {message}",
}

# CLIs that support MCP elicitation (structured dialog that forces a response).
# When elicit=true on a notify call, the MCP server returns an elicitation
# request for these CLIs instead of (or in addition to) injection.
ELICITATION_SUPPORTED: frozenset[str] = frozenset({
    "claude-code",
    "codex",
    "cursor",
    "qwen",
})

# Maps logical CLI names to the shell command used to launch the agent in tmux.
# Used by spawn_teammate when the caller provides a CLI name rather than a raw
# command string.
BACKEND_LAUNCH_COMMANDS: dict[str, str] = {
    "claude-code": "claude",
    "copilot": "copilot",
    "gemini": "gemini",
    "kiro": "kiro-cli",
    "codex": "codex",
    "opencode": "opencode",
    "cursor": "agent",
    "qwen": "qwen",
    "kilo": "kilo",
    "vibe": "vibe",
    "amp": "amp",
}

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
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this task can be claimed.",
                },
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
    {
        "name": "notify",
        "description": "Send a direct message to another agent (or all agents) via the event log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_agent": {"type": "string", "description": "Agent name or \"all\" for broadcast."},
                "message": {"type": "string"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "elicit": {
                    "type": "boolean",
                    "description": "If true and CLI supports MCP elicitation, pop a structured dialog forcing the agent to respond.",
                },
            },
            "required": ["target_agent", "message", "priority"],
            "additionalProperties": False,
        },
    },
)

TOOL_NAMES = tuple(spec["name"] for spec in TOOL_SPECS)

TOOL_PROFILES: dict[str, tuple[str, ...]] = {
    "full": TOOL_NAMES,
    "orchestrator": TOOL_NAMES,
    "worker": (
        "export_summary",
        "register_capabilities",
    ),
    "worker-hookless": (
        "report_status",
        "report_progress",
        "export_summary",
        "register_capabilities",
    ),
    "reviewer": (
        "export_summary",
        "notify",
        "register_capabilities",
    ),
    "architect": (
        "export_summary",
        "notify",
        "register_capabilities",
    ),
    "manager": (
        "export_summary",
        "register_capabilities",
        "request_task",
        "wait_for",
        "spawn_teammate",
    ),
}


class ToolInputError(ValueError):
    """Raised when tool input does not satisfy AIP expectations."""


def resolve_allowed_tools(
    *,
    tool_profile: str = "full",
    allowed_tools: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if allowed_tools:
        resolved = tuple(dict.fromkeys(tool_name.strip() for tool_name in allowed_tools if tool_name.strip()))
        unknown = [tool_name for tool_name in resolved if tool_name not in TOOL_NAMES]
        if unknown:
            raise ToolInputError(f"Unknown tool(s): {', '.join(unknown)}")
        return resolved

    try:
        return TOOL_PROFILES[tool_profile]
    except KeyError as exc:
        valid = ", ".join(sorted(TOOL_PROFILES))
        raise ToolInputError(f"Unknown tool_profile: {tool_profile}. Valid profiles: {valid}") from exc


class AipToolRuntime:
    def __init__(
        self,
        workspace_root: str,
        agent_name: str,
        *,
        session_name: str = "aip",
        tmux_controller: TmuxController | None = None,
        poll_interval: float = 0.1,
        max_depth: int = 3,
        max_breadth: int = 4,
        tool_profile: str = "full",
        allowed_tools: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.workspace = AipWorkspace(workspace_root)
        self.workspace.ensure()
        self.queue = TaskQueue(self.workspace)
        self.agent_name = agent_name
        self.session_name = session_name
        self.tmux = tmux_controller or TmuxController(session_name=session_name)
        self.poll_interval = poll_interval
        self.max_depth = max_depth
        self.max_breadth = max_breadth
        self.tool_profile = tool_profile
        self.allowed_tools = frozenset(
            resolve_allowed_tools(tool_profile=tool_profile, allowed_tools=allowed_tools)
        )

    def execute(self, name: str, arguments: dict[str, Any] | None) -> str:
        if name not in TOOL_NAMES:
            raise ToolInputError(f"Unknown tool: {name}")
        if name not in self.allowed_tools:
            raise ToolInputError(f"Tool not enabled for profile '{self.tool_profile}': {name}")
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
                blocked_by=args.get("blocked_by"),
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
        if name == "notify":
            return self.notify(
                target_agent=args["target_agent"],
                message=args["message"],
                priority=args["priority"],
                elicit=args.get("elicit", False),
            )
        raise ToolInputError(f"Unknown tool: {name}")

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [spec for spec in TOOL_SPECS if spec["name"] in self.allowed_tools]

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
        blocked_by: list[str] | None = None,
    ) -> str:
        task = self.queue.create_task(
            description=task_description,
            task_type="delegated",
            priority=priority or "normal",
            target_role=target_role,
            context=context,
            requested_by=self.agent_name,
            blocked_by=blocked_by,
        )
        return json.dumps(
            {
                "task_id": task.task_id,
                "path": f"tasks/pending/{task.task_id}.md",
                "blocked_by": task.blocked_by or None,
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

    def notify(self, *, target_agent: str, message: str, priority: str, elicit: bool = False) -> str:
        valid_priorities = {"high", "medium", "low"}
        if priority not in valid_priorities:
            raise ToolInputError(f"Invalid priority: {priority}. Must be one of {valid_priorities}")
        target = target_agent.strip()
        if not target:
            raise ToolInputError("target_agent must not be empty")
        msg = message.strip()
        if not msg:
            raise ToolInputError("message must not be empty")

        # Always append to event log (permanent record).
        self.workspace.append_event(
            self.agent_name,
            "notify",
            target=target,
            priority=priority,
            message=msg,
            elicit=elicit if elicit else None,
        )

        # Dual-mode: high priority + injection-capable CLI → send mid-stream.
        injected_to: list[str] = []
        elicitation_targets: list[str] = []
        if priority == "high":
            targets = self._resolve_notify_targets(target)
            for agent_id, cli_type in targets:
                if elicit and cli_type in ELICITATION_SUPPORTED:
                    elicitation_targets.append(agent_id)
                    continue
                template = INJECTION_COMMANDS.get(cli_type)
                if template is not None:
                    inject_text = template.replace("{message}", f"{self.agent_name} says: {msg}")
                    try:
                        self.tmux.send_keys(agent_id, inject_text)
                        injected_to.append(agent_id)
                    except Exception:
                        logger.warning("Failed to inject notify into pane %s", agent_id)

        result: dict[str, Any] = {
            "sent": True,
            "from": self.agent_name,
            "target": target,
            "priority": priority,
            "injected_to": injected_to,
        }
        if elicit:
            result["elicit"] = True
            result["elicitation_targets"] = elicitation_targets
        return json.dumps(result, indent=2)

    def _resolve_notify_targets(self, target: str) -> list[tuple[str, str]]:
        """Return (agent_id, cli_type) pairs for injection candidates."""
        if target == "all":
            tree = self.workspace.read_agent_tree()
            return [
                (agent_id, node.get("cli_type", ""))
                for agent_id, node in tree.items()
                if agent_id != self.agent_name
            ]
        # Single target — check agent tree first, then status file.
        tree = self.workspace.read_agent_tree()
        if target in tree:
            return [(target, tree[target].get("cli_type", ""))]
        status = self.workspace.read_status(target)
        return [(target, status.get("cli_type", ""))]

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


def create_mcp_server(runtime: AipToolRuntime):
    """Build an MCP SDK server wired to our tool runtime."""
    Server, stdio_server, TextContent, Tool = _import_mcp_sdk()

    server = Server(
        "aip-mcp",
        version=__version__,
        instructions=(
            "Use the agent-interface-protocol tools to publish status, progress, summaries, and delegated tasks "
            "into the shared workspace."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list:
        return [
            Tool(name=spec["name"], description=spec["description"], inputSchema=spec["inputSchema"])
            for spec in runtime.list_tool_specs()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list:
        try:
            text = runtime.execute(name, arguments)
        except (KeyError, ToolInputError) as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]
        return [TextContent(type="text", text=text)]

    return server, stdio_server


async def _run(
    workspace: str,
    agent_name: str,
    session_name: str = "aip",
    *,
    tool_profile: str = "full",
    allowed_tools: list[str] | None = None,
) -> None:
    runtime = AipToolRuntime(
        workspace,
        agent_name,
        session_name=session_name,
        tool_profile=tool_profile,
        allowed_tools=allowed_tools,
    )
    server, stdio_server = create_mcp_server(runtime)
    logger.info("aip-mcp starting (workspace=%s, agent=%s)", workspace, agent_name)

    async with stdio_server() as (read_stream, write_stream):
        logger.info("aip-mcp stdio connected, serving tools")
        await server.run(read_stream, write_stream, server.create_initialization_options())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aip-mcp")
    parser.add_argument("--workspace", default="workspace")
    parser.add_argument("--agent-name", default="agent")
    parser.add_argument("--session-name", default="aip")
    parser.add_argument("--tool-profile", default="full")
    parser.add_argument("--allowed-tool", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(
            _run(
                args.workspace,
                args.agent_name,
                args.session_name,
                tool_profile=args.tool_profile,
                allowed_tools=args.allowed_tool,
            )
        )
    except KeyboardInterrupt:
        logger.info("Server stopped")
        raise SystemExit(0)
    except ToolInputError as exc:
        logger.error("%s", exc)
        raise SystemExit(2)
    except Exception:
        logger.exception("Server crashed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
