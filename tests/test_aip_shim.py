"""Tests for the Tier 2 interactive intercept shim (aip.aip_shim)."""

from __future__ import annotations

import json
import re

import pytest

from aip.aip_shim import (
    BUILTIN_PROFILES,
    AipShim,
    BlockRule,
    ShimProfile,
    _parse_simple_yaml,
    load_profile,
)
from aip.cli import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeTmuxController:
    def __init__(self, session_name="aip"):
        self.session_name = session_name
        self.sent_keys: list[tuple[str, str, bool]] = []
        self.pane_content: dict[str, str] = {}

    def capture_pane(self, target, *, lines=None, include_escape=False):
        return self.pane_content.get(target, "")

    def send_keys(self, target, text, *, press_enter=True):
        self.sent_keys.append((target, text, press_enter))


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def test_load_builtin_profile_intercept():
    profile = load_profile("amp")
    assert profile.tier == "intercept"
    assert profile.prompt_regex is not None


def test_load_builtin_profile_native():
    profile = load_profile("claude-code")
    assert profile.tier == "native"


def test_load_profile_unknown_raises():
    with pytest.raises(ValueError, match="No shim profile found"):
        load_profile("nonexistent-cli-xyz")


# ---------------------------------------------------------------------------
# ShimProfile.from_dict
# ---------------------------------------------------------------------------


def test_shim_profile_from_dict_intercept():
    data = {
        "tier": "intercept",
        "interactive_intercept": {
            "prompt_regex": r"Approve\? \[Y/n\]",
            "approve_keys": "y\n",
            "deny_keys": "n\n",
        },
    }
    profile = ShimProfile.from_dict("test-cli", data)
    assert profile.cli_name == "test-cli"
    assert profile.tier == "intercept"
    assert profile.prompt_regex is not None
    assert profile.prompt_regex.search("Approve? [Y/n]")
    assert profile.approve_keys == "y\n"
    assert profile.deny_keys == "n\n"


def test_shim_profile_from_dict_native():
    data = {"tier": "native"}
    profile = ShimProfile.from_dict("claude-code", data)
    assert profile.tier == "native"
    assert profile.prompt_regex is None


# ---------------------------------------------------------------------------
# AipShim.add_agent
# ---------------------------------------------------------------------------


def test_add_agent_rejects_native_profile(tmp_path):
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
    )
    native_profile = ShimProfile.from_dict("claude-code", {"tier": "native"})
    with pytest.raises(ValueError, match="not 'intercept'"):
        shim.add_agent("agent-1", native_profile)


# ---------------------------------------------------------------------------
# AipShim.check_once
# ---------------------------------------------------------------------------


def test_check_once_detects_approval_prompt(tmp_path):
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
        auto_approve=True,
    )
    profile = load_profile("amp")
    shim.add_agent("agent-amp", profile)

    pane_text = "Running: rm -rf /\nAllow this action? [Y/n]"
    result = shim.check_once("agent-amp", pane_content=pane_text)

    assert result is not None
    assert result["action"] == "approved"
    assert result["agent"] == "agent-amp"
    assert "prompt" in result
    assert len(tmux.sent_keys) == 1


def test_check_once_no_prompt_returns_none(tmp_path):
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
    )
    profile = load_profile("amp")
    shim.add_agent("agent-amp", profile)

    result = shim.check_once("agent-amp", pane_content="Just normal output here")
    assert result is None


def test_check_once_with_block_rule_denies(tmp_path):
    block_rules = [
        BlockRule(pattern=re.compile(r"rm -rf"), reason="dangerous command"),
    ]
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
        auto_approve=True,
        block_rules=block_rules,
    )
    profile = load_profile("amp")
    shim.add_agent("agent-amp", profile)

    pane_text = "rm -rf /\nAllow this action? [Y/n]"
    result = shim.check_once("agent-amp", pane_content=pane_text)

    assert result is not None
    assert result["action"] == "denied"


def test_check_once_auto_approve_false_denies(tmp_path):
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
        auto_approve=False,
    )
    profile = load_profile("amp")
    shim.add_agent("agent-amp", profile)

    pane_text = "Allow this action? [Y/n]"
    result = shim.check_once("agent-amp", pane_content=pane_text)

    assert result is not None
    assert result["action"] == "denied"


# ---------------------------------------------------------------------------
# _parse_simple_yaml
# ---------------------------------------------------------------------------


def test_parse_simple_yaml():
    yaml_text = """\
tier: intercept
interactive_intercept:
  prompt_regex: 'Allow this action\\? \\[Y/n\\]'
  approve_keys: "y\\n"
  deny_keys: "n\\n"
"""
    data = _parse_simple_yaml(yaml_text)
    assert data["tier"] == "intercept"
    assert "interactive_intercept" in data
    intercept = data["interactive_intercept"]
    assert "prompt_regex" in intercept
    assert intercept["approve_keys"] == "y\n"
    assert intercept["deny_keys"] == "n\n"


# ---------------------------------------------------------------------------
# AipShim.poll_all
# ---------------------------------------------------------------------------


def test_poll_all_checks_all_agents(tmp_path):
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
        auto_approve=True,
    )
    amp_profile = load_profile("amp")
    aider_profile = load_profile("aider")
    shim.add_agent("agent-amp", amp_profile)
    shim.add_agent("agent-aider", aider_profile)

    tmux.pane_content["agent-amp"] = "Allow this action? [Y/n]"
    tmux.pane_content["agent-aider"] = "Run this command? (Y/n):"

    events = shim.poll_all()
    assert len(events) == 2
    agents_handled = {e["agent"] for e in events}
    assert agents_handled == {"agent-amp", "agent-aider"}


# ---------------------------------------------------------------------------
# CLI: shim list-profiles
# ---------------------------------------------------------------------------


def test_shim_list_profiles_cli(capsys, tmp_path):
    exit_code = main(["--workspace-root", str(tmp_path / "workspace"), "shim", "list-profiles"])
    assert exit_code == 0
    captured = capsys.readouterr()
    profiles = json.loads(captured.out)
    # All BUILTIN_PROFILES should be present
    for cli_name in BUILTIN_PROFILES:
        assert cli_name in profiles
    # Spot-check tiers
    assert profiles["amp"]["tier"] == "intercept"
    assert profiles["claude-code"]["tier"] == "native"
