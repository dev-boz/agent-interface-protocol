import json

import pytest

from aip.hook_configs import generate_hook_config, install_hook_config
from aip.hooks import (
    HookError,
    HookRuntime,
    normalize_hook_event,
    parse_codex_notification,
    parse_hook_payload,
    parse_hook_stdin,
)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("SessionStart", "session_start"),
        ("sessionStart", "session_start"),
        ("userpromptsubmit", "session_start"),
        ("PreToolUse", "pre_tool_use"),
        ("tool.execute.before", "pre_tool_use"),
        ("AfterTool", "post_tool_use"),
        ("agent-turn-complete", "task_completed"),
        ("TaskCompleted", "task_completed"),
        ("SessionEnd", "session_end"),
        ("AgentSpawn", "subagent_start"),
        ("SubagentStop", "subagent_stop"),
    ],
)
def test_normalize_hook_event_aliases(raw, normalized):
    assert normalize_hook_event(raw) == normalized


def test_normalize_hook_event_rejects_unknown():
    with pytest.raises(HookError, match="Unsupported hook event"):
        normalize_hook_event("mystery-event")


def test_session_start_writes_status_and_event(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "coder")

    result = runtime.emit("SessionStart", {"message": "starting auth work"})

    assert result["normalized_event"] == "session_start"
    status = result["status"]
    assert status["status"] == "working"
    assert status["active"] is True
    assert status["message"] == "starting auth work"

    events = [json.loads(line) for line in runtime.workspace.events_path.read_text(encoding="utf-8").splitlines()]
    assert events == [
        {
            "ts": events[0]["ts"],
            "agent": "coder",
            "event": "status",
            "status": "working",
            "message": "starting auth work",
            "source": "hook",
            "hook_event": "SessionStart",
        }
    ]


def test_pre_and_post_tool_use_write_tool_events(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "coder")

    started = runtime.emit("PreToolUse", {"tool": "bash"})
    completed = runtime.emit("PostToolUse", {"tool_name": "bash"})

    assert started["status"]["current_tool"] == "bash"
    assert started["status"]["last_tool_status"] == "started"
    assert completed["status"]["last_tool_status"] == "completed"
    assert "current_tool" not in completed["status"]

    events = [json.loads(line) for line in runtime.workspace.events_path.read_text(encoding="utf-8").splitlines()]
    assert [(event["event"], event.get("tool"), event.get("status")) for event in events] == [
        ("tool", "bash", "started"),
        ("tool", "bash", "completed"),
    ]


def test_task_completed_and_session_end_update_status(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "reviewer")

    finished = runtime.emit("agent-turn-complete", {"summary": "review done"})
    ended = runtime.emit("SessionEnd", {})

    assert finished["status"]["status"] == "finished"
    assert ended["status"]["status"] == "idle"
    assert ended["status"]["active"] is False


def test_subagent_events_append_event_log_entries(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "manager")

    result = runtime.emit("AgentSpawn", {"name": "worker-1"})

    assert result["status"] is None
    event = result["event"]
    assert event["event"] == "subagent"
    assert event["action"] == "spawned"
    assert event["child"] == "worker-1"


def test_parse_hook_payload_variants(tmp_path):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"tool":"bash"}', encoding="utf-8")

    assert parse_hook_payload('{"message":"ok"}', None) == {"message": "ok"}
    assert parse_hook_payload(None, str(payload_file)) == {"tool": "bash"}


def test_parse_hook_payload_rejects_invalid_inputs(tmp_path):
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('["not","an","object"]', encoding="utf-8")

    with pytest.raises(HookError, match="Specify only one"):
        parse_hook_payload("{}", str(payload_file))
    with pytest.raises(HookError, match="Hook payload must decode to a JSON object"):
        parse_hook_payload(None, str(payload_file))


def test_parse_hook_stdin_reads_event_and_payload():
    event_name, payload = parse_hook_stdin('{"hook_event_name":"BeforeTool","tool_name":"read_file"}')
    assert event_name == "BeforeTool"
    assert payload["tool_name"] == "read_file"


def test_parse_codex_notification_maps_agent_turn_complete():
    event_name, payload = parse_codex_notification('{"type":"agent-turn-complete","cwd":"/tmp"}')
    assert event_name == "agent-turn-complete"
    assert payload["cwd"] == "/tmp"


def test_generate_gemini_hook_config_contains_mcp_profile():
    config = generate_hook_config(
        cli_name="gemini",
        workspace_root="/workspace",
        agent_name="coder",
        session_name="aip",
        tool_profile="worker",
    )

    assert config["cli"] == "gemini"
    snippet = json.loads(config["snippet"])
    assert snippet["mcpServers"]["aip"]["args"][-1] == "worker"
    assert "BeforeTool" in snippet["hooks"]


def test_generate_kiro_hook_config_uses_silent_proxy():
    config = generate_hook_config(
        cli_name="kiro",
        workspace_root="/workspace",
        agent_name="reviewer",
        session_name="aip",
        tool_profile="reviewer",
    )

    assert config["cli"] == "kiro"
    snippet = json.loads(config["snippet"])
    assert "preToolUse" in snippet["hooks"]
    assert "--output-mode silent" in snippet["hooks"]["preToolUse"][0]["command"]


def test_generate_codex_hook_config_contains_hooks_json_and_bootstrap():
    config = generate_hook_config(
        cli_name="codex",
        workspace_root="/workspace",
        agent_name="architect",
        session_name="aip",
        tool_profile="architect",
    )

    assert config["cli"] == "codex"
    assert config["path_hint"] == ".codex/hooks.json"
    snippet = json.loads(config["snippet"])
    assert "SessionStart" in snippet["hooks"]
    assert snippet["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert "--output-mode silent" in snippet["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert config["bootstrap_path_hint"] == ".codex/config.toml"
    assert "codex_hooks = true" in config["bootstrap_snippet"]
    assert '"--tool-profile", "architect"' in config["bootstrap_snippet"]


def test_install_gemini_hook_config_merges_existing_settings(tmp_path):
    config_root = tmp_path / "repo"
    settings_path = config_root / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "light",
                "mcpServers": {"other": {"command": "other-mcp", "args": []}},
            }
        ),
        encoding="utf-8",
    )

    result = install_hook_config(
        cli_name="gemini",
        config_root=config_root,
        workspace_root="/workspace",
        agent_name="coder",
        session_name="aip",
        tool_profile="worker",
    )

    merged = json.loads(settings_path.read_text(encoding="utf-8"))
    assert result["written"] == [str(settings_path.resolve())]
    assert merged["theme"] == "light"
    assert merged["mcpServers"]["other"]["command"] == "other-mcp"
    assert merged["mcpServers"]["aip"]["args"][-1] == "worker"
    assert merged["hooksConfig"]["enabled"] is True
    assert "BeforeTool" in merged["hooks"]


def test_generate_claude_code_hook_config_contains_mcp_and_hooks():
    config = generate_hook_config(
        cli_name="claude-code",
        workspace_root="/workspace",
        agent_name="coder",
        session_name="aip",
        tool_profile="worker",
    )

    assert config["cli"] == "claude-code"
    assert config["path_hint"] == ".claude/settings.json"
    snippet = json.loads(config["snippet"])
    assert "aip" in snippet["mcpServers"]
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "Stop", "SubagentStart", "SubagentStop"):
        assert event in snippet["hooks"], f"Missing hook event: {event}"
        assert "--output-mode silent" in snippet["hooks"][event][0]["hooks"][0]["command"]


def test_generate_copilot_hook_config_uses_camel_case_hooks():
    config = generate_hook_config(
        cli_name="copilot",
        workspace_root="/workspace",
        agent_name="coder",
        session_name="aip",
        tool_profile="worker",
    )

    assert config["cli"] == "copilot"
    assert config["path_hint"] == ".github/copilot/hooks.json"
    snippet = json.loads(config["snippet"])
    assert "aip" in snippet["mcpServers"]
    for event in ("sessionStart", "preToolUse", "postToolUse"):
        assert event in snippet["hooks"], f"Missing hook event: {event}"
        assert "--output-mode silent" in snippet["hooks"][event][0]["command"]


def test_install_claude_code_hook_config_merges_existing_settings(tmp_path):
    config_root = tmp_path / "repo"
    settings_path = config_root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {"other": {"command": "other-mcp", "args": []}},
            }
        ),
        encoding="utf-8",
    )

    result = install_hook_config(
        cli_name="claude-code",
        config_root=config_root,
        workspace_root="/workspace",
        agent_name="coder",
        session_name="aip",
        tool_profile="worker",
    )

    merged = json.loads(settings_path.read_text(encoding="utf-8"))
    assert result["written"] == [str(settings_path.resolve())]
    assert merged["theme"] == "dark"
    assert merged["mcpServers"]["other"]["command"] == "other-mcp"
    assert "aip" in merged["mcpServers"]
    assert "SessionStart" in merged["hooks"]
    assert "SubagentStop" in merged["hooks"]


def test_install_copilot_hook_config_creates_config_file(tmp_path):
    config_root = tmp_path / "repo"

    result = install_hook_config(
        cli_name="copilot",
        config_root=config_root,
        workspace_root="/workspace",
        agent_name="reviewer",
        session_name="aip",
        tool_profile="reviewer",
    )

    config_path = config_root / ".github" / "copilot" / "hooks.json"
    assert config_path.exists()
    assert result["written"] == [str(config_path.resolve())]
    content = json.loads(config_path.read_text(encoding="utf-8"))
    assert "aip" in content["mcpServers"]
    assert "sessionStart" in content["hooks"]
    assert "preToolUse" in content["hooks"]
    assert "postToolUse" in content["hooks"]
    assert "--output-mode silent" in content["hooks"]["sessionStart"][0]["command"]


def test_install_codex_hook_config_merges_hooks_and_bootstrap(tmp_path):
    config_root = tmp_path / "repo"
    hooks_path = config_root / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    hooks_path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing-stop"}]}]}}),
        encoding="utf-8",
    )
    config_toml_path = config_root / ".codex" / "config.toml"
    config_toml_path.write_text(
        '[features]\nfoo = true\n\n[mcp_servers.other]\ncommand = "other"\nargs = []\n',
        encoding="utf-8",
    )

    result = install_hook_config(
        cli_name="codex",
        config_root=config_root,
        workspace_root="/workspace",
        agent_name="architect",
        session_name="aip",
        tool_profile="architect",
    )

    merged_hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    merged_toml = config_toml_path.read_text(encoding="utf-8")
    assert result["written"] == [str(hooks_path.resolve()), str(config_toml_path.resolve())]
    assert len(merged_hooks["hooks"]["Stop"]) == 2
    assert "SessionStart" in merged_hooks["hooks"]
    assert "codex_hooks = true" in merged_toml
    assert '[mcp_servers.other]' in merged_toml
    assert '[mcp_servers.aip]' in merged_toml
    assert '"architect"' in merged_toml
