"""Tests for the human-escalation gate flow (aip.gates + aip.aip_shim)."""

from __future__ import annotations

import json

from aip.aip_shim import AipShim, BlockRule, load_profile
from aip.gates import open_gate_path, read_gate, resolve_gate, write_gate
import re


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
# gates module
# ---------------------------------------------------------------------------


def test_write_and_read_gate(tmp_path):
    ws = tmp_path / "workspace"
    path = write_gate(ws, "coder", tool="git", command="git push origin main")
    record = read_gate(path)
    assert record["agent"] == "coder"
    assert record["approved"] is None
    assert record["resolved"] is False
    assert path.name == "gate-coder-001.json"


def test_open_gate_path_finds_unresolved(tmp_path):
    ws = tmp_path / "workspace"
    path = write_gate(ws, "coder", tool="git", command="git push")
    assert open_gate_path(ws, "coder") == path
    resolve_gate(path, approved=True)
    assert open_gate_path(ws, "coder") is None


# ---------------------------------------------------------------------------
# shim gate flow
# ---------------------------------------------------------------------------


def _gated_shim(tmp_path):
    tmux = FakeTmuxController()
    shim = AipShim(
        str(tmp_path / "workspace"),
        tmux_controller=tmux,
        auto_approve=True,
        escalate_rules=[BlockRule(pattern=re.compile(r"git push"), reason="git push")],
    )
    shim.add_agent("agent-amp", load_profile("amp"))
    return shim, tmux


def test_escalation_creates_gate_and_blocks(tmp_path):
    shim, tmux = _gated_shim(tmp_path)
    pane = "Running: git push origin main\nAllow this action? [Y/n]"
    result = shim.check_once("agent-amp", pane_content=pane)

    # Gated: no keys injected, a pending gate record written, human_gate event.
    assert result["action"] == "gated"
    assert tmux.sent_keys == []
    gate_path = open_gate_path(str(tmp_path / "workspace"), "agent-amp")
    assert gate_path is not None
    assert read_gate(gate_path)["approved"] is None

    events = [
        json.loads(line)
        for line in (tmp_path / "workspace" / "events.jsonl").read_text().splitlines()
    ]
    human_gate = next(e for e in events if e["event"] == "human_gate")
    assert human_gate["requires_approval"] is True
    assert "git push" in human_gate["command"]


def test_pending_gate_keeps_agent_waiting(tmp_path):
    shim, tmux = _gated_shim(tmp_path)
    shim.check_once("agent-amp", pane_content="Running: git push\nAllow this action? [Y/n]")
    # Second poll while unresolved → waiting, still no keys injected.
    result = shim.check_once("agent-amp", pane_content="anything")
    assert result["action"] == "waiting"
    assert tmux.sent_keys == []


def test_human_approval_injects_keys(tmp_path):
    shim, tmux = _gated_shim(tmp_path)
    shim.check_once("agent-amp", pane_content="Running: git push\nAllow this action? [Y/n]")
    gate_path = open_gate_path(str(tmp_path / "workspace"), "agent-amp")

    # A human approves the gate by flipping approved in the gate file.
    record = read_gate(gate_path)
    record["approved"] = True
    gate_path.write_text(json.dumps(record))

    result = shim.check_once("agent-amp", pane_content="anything")
    assert result["action"] == "approved"
    assert len(tmux.sent_keys) == 1
    # Gate is now resolved.
    assert read_gate(gate_path)["resolved"] is True
    assert open_gate_path(str(tmp_path / "workspace"), "agent-amp") is None


def test_human_denial_injects_deny_keys(tmp_path):
    shim, tmux = _gated_shim(tmp_path)
    shim.check_once("agent-amp", pane_content="Running: git push\nAllow this action? [Y/n]")
    gate_path = open_gate_path(str(tmp_path / "workspace"), "agent-amp")
    record = read_gate(gate_path)
    record["approved"] = False
    gate_path.write_text(json.dumps(record))

    result = shim.check_once("agent-amp", pane_content="anything")
    assert result["action"] == "denied"
    assert len(tmux.sent_keys) == 1


def test_non_escalated_command_auto_decides(tmp_path):
    shim, tmux = _gated_shim(tmp_path)
    # 'ls' does not match the escalation rule → normal auto-approve path.
    result = shim.check_once("agent-amp", pane_content="Running: ls -la\nAllow this action? [Y/n]")
    assert result["action"] == "approved"
    assert len(tmux.sent_keys) == 1
