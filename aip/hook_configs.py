from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any


def _shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def build_mcp_server_config(
    *,
    workspace_root: str,
    agent_name: str,
    session_name: str,
    tool_profile: str,
) -> dict[str, Any]:
    return {
        "command": "aip-mcp",
        "args": [
            "--workspace",
            workspace_root,
            "--agent-name",
            agent_name,
            "--session-name",
            session_name,
            "--tool-profile",
            tool_profile,
        ],
    }


def build_hook_proxy_command(*, workspace_root: str, agent_name: str, output_mode: str) -> str:
    return _shell_join(
        [
            "aip",
            "--workspace-root",
            workspace_root,
            "hook",
            "proxy",
            "--agent-name",
            agent_name,
            "--output-mode",
            output_mode,
        ]
    )


def build_codex_notify_command(*, workspace_root: str, agent_name: str) -> list[str]:
    return [
        "aip",
        "--workspace-root",
        workspace_root,
        "hook",
        "notify-proxy",
        "--agent-name",
        agent_name,
    ]


def build_codex_hooks_config(*, workspace_root: str, agent_name: str) -> dict[str, Any]:
    command = build_hook_proxy_command(
        workspace_root=workspace_root,
        agent_name=agent_name,
        output_mode="silent",
    )
    return {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "statusMessage": "aip session start",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "statusMessage": "aip pre-tool",
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "statusMessage": "aip post-tool",
                        }
                    ],
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "statusMessage": "aip prompt submit",
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "statusMessage": "aip stop",
                        }
                    ],
                }
            ],
        }
    }


def _resolve_path(config_root: str | Path, relative_path: str) -> Path:
    return Path(config_root).expanduser().resolve() / relative_path


def _merge_hook_groups(existing_groups: list[Any], new_groups: list[Any]) -> list[Any]:
    merged = list(existing_groups)
    seen = {json.dumps(group, sort_keys=True) for group in existing_groups}
    for group in new_groups:
        signature = json.dumps(group, sort_keys=True)
        if signature not in seen:
            merged.append(group)
            seen.add(signature)
    return merged


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _merge_named_server(existing: dict[str, Any], server_name: str, server_config: dict[str, Any]) -> None:
    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")
    servers[server_name] = server_config


def _merge_hook_map(existing: dict[str, Any], incoming_hooks: dict[str, Any]) -> None:
    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be a JSON object")
    for event_name, groups in incoming_hooks.items():
        current_groups = hooks.get(event_name, [])
        if not isinstance(current_groups, list):
            raise ValueError(f"hooks.{event_name} must be a JSON array")
        hooks[event_name] = _merge_hook_groups(current_groups, groups)


def _ensure_codex_hooks_enabled(text: str) -> str:
    if "[features]" not in text:
        prefix = text.rstrip()
        if prefix:
            prefix += "\n\n"
        return prefix + "[features]\ncodex_hooks = true\n"

    lines = text.splitlines()
    in_features = False
    inserted = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_features and not inserted:
                result.append("codex_hooks = true")
                inserted = True
            in_features = stripped == "[features]"
            result.append(line)
            continue
        if in_features and stripped.startswith("codex_hooks"):
            result.append("codex_hooks = true")
            inserted = True
            continue
        result.append(line)
    if in_features and not inserted:
        result.append("codex_hooks = true")
    return "\n".join(result).rstrip() + "\n"


def _upsert_toml_table(text: str, table_name: str, body_lines: list[str]) -> str:
    lines = text.splitlines()
    header = f"[{table_name}]"
    new_section = [header, *body_lines]
    start = None
    end = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == header:
            start = index
            end = len(lines)
            for next_index in range(index + 1, len(lines)):
                next_stripped = lines[next_index].strip()
                if next_stripped.startswith("[") and next_stripped.endswith("]"):
                    end = next_index
                    break
            break
    if start is None:
        merged_lines = lines[:]
        if merged_lines and merged_lines[-1].strip():
            merged_lines.append("")
        merged_lines.extend(new_section)
    else:
        merged_lines = lines[:start] + new_section + lines[end:]
    return "\n".join(merged_lines).rstrip() + "\n"


def install_hook_config(
    *,
    cli_name: str,
    config_root: str | Path,
    workspace_root: str,
    agent_name: str,
    session_name: str,
    tool_profile: str,
) -> dict[str, Any]:
    cli_id = cli_name.strip().lower()
    root = Path(config_root).expanduser().resolve()

    if cli_id == "gemini":
        config_path = _resolve_path(root, ".gemini/settings.json")
        generated = json.loads(
            generate_hook_config(
                cli_name="gemini",
                workspace_root=workspace_root,
                agent_name=agent_name,
                session_name=session_name,
                tool_profile=tool_profile,
            )["snippet"]
        )
        existing = _load_json_object(config_path)
        _merge_named_server(existing, "aip", generated["mcpServers"]["aip"])
        hooks_config = existing.setdefault("hooksConfig", {})
        if not isinstance(hooks_config, dict):
            raise ValueError("hooksConfig must be a JSON object")
        hooks_config["enabled"] = True
        _merge_hook_map(existing, generated["hooks"])
        _write_json_object(config_path, existing)
        return {"cli": "gemini", "written": [str(config_path)], "config_root": str(root)}

    if cli_id == "kiro":
        config_path = _resolve_path(root, f".kiro/agents/{agent_name}.json")
        generated = json.loads(
            generate_hook_config(
                cli_name="kiro",
                workspace_root=workspace_root,
                agent_name=agent_name,
                session_name=session_name,
                tool_profile=tool_profile,
            )["snippet"]
        )
        existing = _load_json_object(config_path)
        _merge_named_server(existing, "aip", generated["mcpServers"]["aip"])
        _merge_hook_map(existing, generated["hooks"])
        _write_json_object(config_path, existing)
        return {"cli": "kiro", "written": [str(config_path)], "config_root": str(root)}

    if cli_id == "codex":
        generated = generate_hook_config(
            cli_name="codex",
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        hooks_path = _resolve_path(root, ".codex/hooks.json")
        config_path = _resolve_path(root, ".codex/config.toml")

        existing_hooks = _load_json_object(hooks_path)
        incoming_hooks = json.loads(generated["snippet"])
        _merge_hook_map(existing_hooks, incoming_hooks["hooks"])
        _write_json_object(hooks_path, existing_hooks)

        existing_toml = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        merged_toml = _ensure_codex_hooks_enabled(existing_toml)
        bootstrap_lines = generated["bootstrap_snippet"].strip().splitlines()
        table_body = [
            line
            for line in bootstrap_lines
            if line.strip() and line.strip() not in {"[features]", "[mcp_servers.aip]", "codex_hooks = true"}
        ]
        merged_toml = _upsert_toml_table(merged_toml, "mcp_servers.aip", table_body)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(merged_toml, encoding="utf-8")
        return {"cli": "codex", "written": [str(hooks_path), str(config_path)], "config_root": str(root)}

    if cli_id == "claude-code":
        config_path = _resolve_path(root, ".claude/settings.json")
        generated = json.loads(
            generate_hook_config(
                cli_name="claude-code",
                workspace_root=workspace_root,
                agent_name=agent_name,
                session_name=session_name,
                tool_profile=tool_profile,
            )["snippet"]
        )
        existing = _load_json_object(config_path)
        _merge_named_server(existing, "aip", generated["mcpServers"]["aip"])
        _merge_hook_map(existing, generated["hooks"])
        _write_json_object(config_path, existing)
        return {"cli": "claude-code", "written": [str(config_path)], "config_root": str(root)}

    if cli_id == "copilot":
        config_path = _resolve_path(root, ".github/copilot/hooks.json")
        generated = json.loads(
            generate_hook_config(
                cli_name="copilot",
                workspace_root=workspace_root,
                agent_name=agent_name,
                session_name=session_name,
                tool_profile=tool_profile,
            )["snippet"]
        )
        existing = _load_json_object(config_path)
        _merge_named_server(existing, "aip", generated["mcpServers"]["aip"])
        _merge_hook_map(existing, generated["hooks"])
        _write_json_object(config_path, existing)
        return {"cli": "copilot", "written": [str(config_path)], "config_root": str(root)}

    if cli_id == "cursor":
        config_path = _resolve_path(root, ".cursor/settings.json")
        generated = json.loads(
            generate_hook_config(
                cli_name="cursor",
                workspace_root=workspace_root,
                agent_name=agent_name,
                session_name=session_name,
                tool_profile=tool_profile,
            )["snippet"]
        )
        existing = _load_json_object(config_path)
        _merge_named_server(existing, "aip", generated["mcpServers"]["aip"])
        _merge_hook_map(existing, generated["hooks"])
        _write_json_object(config_path, existing)
        return {"cli": "cursor", "written": [str(config_path)], "config_root": str(root)}

    if cli_id == "qwen":
        config_path = _resolve_path(root, ".qwen/settings.json")
        generated = json.loads(
            generate_hook_config(
                cli_name="qwen",
                workspace_root=workspace_root,
                agent_name=agent_name,
                session_name=session_name,
                tool_profile=tool_profile,
            )["snippet"]
        )
        existing = _load_json_object(config_path)
        _merge_named_server(existing, "aip", generated["mcpServers"]["aip"])
        _merge_hook_map(existing, generated["hooks"])
        _write_json_object(config_path, existing)
        return {"cli": "qwen", "written": [str(config_path)], "config_root": str(root)}

    raise ValueError(f"Unsupported CLI for hook config install: {cli_name}")


def generate_hook_config(
    *,
    cli_name: str,
    workspace_root: str,
    agent_name: str,
    session_name: str,
    tool_profile: str,
) -> dict[str, Any]:
    cli_id = cli_name.strip().lower()
    if cli_id == "gemini":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        command = build_hook_proxy_command(
            workspace_root=workspace_root,
            agent_name=agent_name,
            output_mode="json-empty",
        )
        config = {
            "mcpServers": {"aip": mcp_server},
            "hooksConfig": {"enabled": True},
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "name": "aip-session-start", "command": command}]}],
                "SessionEnd": [{"hooks": [{"type": "command", "name": "aip-session-end", "command": command}]}],
                "BeforeAgent": [{"hooks": [{"type": "command", "name": "aip-before-agent", "command": command}]}],
                "AfterAgent": [{"hooks": [{"type": "command", "name": "aip-after-agent", "command": command}]}],
                "BeforeTool": [{"matcher": ".*", "hooks": [{"type": "command", "name": "aip-before-tool", "command": command}]}],
                "AfterTool": [{"matcher": ".*", "hooks": [{"type": "command", "name": "aip-after-tool", "command": command}]}],
            },
        }
        return {
            "cli": "gemini",
            "format": "json",
            "path_hint": ".gemini/settings.json",
            "snippet": json.dumps(config, indent=2),
            "notes": [
                "Gemini hooks expect JSON on stdin/stdout, so the proxy uses --output-mode json-empty.",
                "Merge this snippet into existing settings rather than overwriting unrelated keys.",
            ],
        }

    if cli_id == "kiro":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        command = build_hook_proxy_command(
            workspace_root=workspace_root,
            agent_name=agent_name,
            output_mode="silent",
        )
        config = {
            "mcpServers": {"aip": mcp_server},
            "hooks": {
                "agentSpawn": [{"command": command}],
                "userPromptSubmit": [{"command": command}],
                "preToolUse": [{"matcher": "*", "command": command}],
                "postToolUse": [{"matcher": "*", "command": command}],
                "stop": [{"command": command}],
            },
        }
        return {
            "cli": "kiro",
            "format": "json",
            "path_hint": f".kiro/agents/{agent_name}.json",
            "snippet": json.dumps(config, indent=2),
            "notes": [
                "Kiro hook stdout can be added to context, so the proxy uses --output-mode silent.",
                "Place the mcpServers and hooks sections inside the target agent configuration JSON.",
            ],
        }

    if cli_id == "codex":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        bootstrap_lines = [
            "[features]",
            "codex_hooks = true",
            "",
            "[mcp_servers.aip]",
            f'command = "{mcp_server["command"]}"',
            "args = [" + ", ".join(json.dumps(arg) for arg in mcp_server["args"]) + "]",
        ]
        return {
            "cli": "codex",
            "format": "json",
            "path_hint": ".codex/hooks.json",
            "snippet": json.dumps(
                build_codex_hooks_config(workspace_root=workspace_root, agent_name=agent_name),
                indent=2,
            ),
            "bootstrap_format": "toml",
            "bootstrap_path_hint": ".codex/config.toml",
            "bootstrap_snippet": "\n".join(bootstrap_lines) + "\n",
            "legacy_notify_command": build_codex_notify_command(
                workspace_root=workspace_root,
                agent_name=agent_name,
            ),
            "notes": [
                "Codex loads hooks from hooks.json alongside the active config layers, typically ~/.codex/hooks.json or <repo>/.codex/hooks.json.",
                "Enable hooks with [features] codex_hooks = true in config.toml, and keep the mcp_servers.aip entry there as well.",
                "The legacy notify command is included as a reference, but hooks.json is now the primary Codex integration path.",
            ],
        }

    if cli_id == "claude-code":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        command = build_hook_proxy_command(
            workspace_root=workspace_root,
            agent_name=agent_name,
            output_mode="silent",
        )
        config = {
            "mcpServers": {"aip": mcp_server},
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                            }
                        ],
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                            }
                        ],
                    }
                ],
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                            }
                        ],
                    }
                ],
                "SubagentStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                            }
                        ],
                    }
                ],
                "SubagentStop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": command,
                            }
                        ],
                    }
                ],
            },
        }
        return {
            "cli": "claude-code",
            "format": "json",
            "path_hint": ".claude/settings.json",
            "snippet": json.dumps(config, indent=2),
            "notes": [
                "Claude Code hooks are configured in .claude/settings.json.",
                "The matcher field is empty string to match all events of each type.",
                "Merge this snippet into existing settings rather than overwriting unrelated keys.",
            ],
        }

    if cli_id == "copilot":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        command = build_hook_proxy_command(
            workspace_root=workspace_root,
            agent_name=agent_name,
            output_mode="silent",
        )
        config = {
            "mcpServers": {"aip": mcp_server},
            "hooks": {
                "sessionStart": [
                    {
                        "command": command,
                    }
                ],
                "preToolUse": [
                    {
                        "matcher": "*",
                        "command": command,
                    }
                ],
                "postToolUse": [
                    {
                        "matcher": "*",
                        "command": command,
                    }
                ],
            },
        }
        return {
            "cli": "copilot",
            "format": "json",
            "path_hint": ".github/copilot/hooks.json",
            "snippet": json.dumps(config, indent=2),
            "notes": [
                "Copilot hooks are configured in .github/copilot/hooks.json.",
                "The proxy uses --output-mode silent to avoid adding output to agent context.",
                "Merge this snippet into existing settings rather than overwriting unrelated keys.",
            ],
        }

    if cli_id == "cursor":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        command = build_hook_proxy_command(
            workspace_root=workspace_root,
            agent_name=agent_name,
            output_mode="silent",
        )
        # Cursor is a Claude Code fork — same hook format
        config = {
            "mcpServers": {"aip": mcp_server},
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
                "PreToolUse": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
                "PostToolUse": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
                "Stop": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
            },
        }
        return {
            "cli": "cursor",
            "format": "json",
            "path_hint": ".cursor/settings.json",
            "snippet": json.dumps(config, indent=2),
            "notes": [
                "Cursor is a Claude Code fork — same hook format and settings structure.",
                "Place in .cursor/settings.json (project) or ~/.cursor/settings.json (global).",
                "Merge this snippet into existing settings rather than overwriting unrelated keys.",
            ],
        }

    if cli_id == "qwen":
        mcp_server = build_mcp_server_config(
            workspace_root=workspace_root,
            agent_name=agent_name,
            session_name=session_name,
            tool_profile=tool_profile,
        )
        command = build_hook_proxy_command(
            workspace_root=workspace_root,
            agent_name=agent_name,
            output_mode="silent",
        )
        # Qwen Code is a Claude Code fork — same hook structure, .qwen/ config path
        config = {
            "mcpServers": {"aip": mcp_server},
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
                "PreToolUse": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
                "PostToolUse": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
                "Stop": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ],
            },
        }
        return {
            "cli": "qwen",
            "format": "json",
            "path_hint": ".qwen/settings.json",
            "snippet": json.dumps(config, indent=2),
            "notes": [
                "Qwen Code is a Claude Code fork — same hook format, .qwen/ config path.",
                "Merge this snippet into existing settings rather than overwriting unrelated keys.",
            ],
        }

    raise ValueError(f"Unsupported CLI for hook config generation: {cli_name}")
