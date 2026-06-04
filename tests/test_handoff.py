"""Tests for contamination-aware handoff validation (aip.handoff + aip.hooks)."""

from __future__ import annotations

import json

from aip.handoff import reject_handoff, validate_handoff_packet
from aip.hooks import HookRuntime


def _good_packet(depth=1):
    return {
        "task_id": "task-042",
        "risk_tier": "EXTERNAL",
        "provenance": {"chain_depth": depth},
        "contamination_risk": {"sanitized": True},
        "outcome": "completed",
    }


# ---------------------------------------------------------------------------
# validate_handoff_packet
# ---------------------------------------------------------------------------


def test_valid_packet_passes():
    ok, reason = validate_handoff_packet(_good_packet(1), incoming_chain_depth=0)
    assert ok is True


def test_chain_depth_not_incremented_fails():
    ok, reason = validate_handoff_packet(_good_packet(1), incoming_chain_depth=1)
    assert ok is False
    assert "chain_depth" in reason


def test_missing_contamination_fails():
    packet = _good_packet(1)
    del packet["contamination_risk"]
    ok, reason = validate_handoff_packet(packet, incoming_chain_depth=0)
    assert ok is False
    assert "contamination_risk" in reason


def test_unsanitized_without_untrusted_sources_fails():
    packet = _good_packet(1)
    packet["contamination_risk"] = {"sanitized": False}
    ok, reason = validate_handoff_packet(packet, incoming_chain_depth=0)
    assert ok is False


def test_unsanitized_with_untrusted_sources_passes():
    packet = _good_packet(1)
    packet["contamination_risk"] = {"sanitized": False, "untrusted_sources": ["web:example.com"]}
    ok, reason = validate_handoff_packet(packet, incoming_chain_depth=0)
    assert ok is True


def test_missing_outcome_fails():
    packet = _good_packet(1)
    del packet["outcome"]
    ok, reason = validate_handoff_packet(packet, incoming_chain_depth=0)
    assert ok is False
    assert "outcome" in reason


def test_reject_handoff_writes_file(tmp_path):
    ws = tmp_path / "workspace"
    path = reject_handoff(ws, "task-042", "bad depth", _good_packet(1))
    assert path.name == "task-042-rejected.json"
    record = json.loads(path.read_text())
    assert record["rejected"] is True
    assert record["reason"] == "bad depth"


# ---------------------------------------------------------------------------
# hook integration
# ---------------------------------------------------------------------------


def test_hook_blocks_bad_handoff(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "coder")
    bad = _good_packet(5)  # depth wrong vs incoming 0
    result = runtime.emit(
        "PreToolUse",
        {"tool": "write_handoff_packet", "handoff_packet": bad, "incoming_chain_depth": 0},
    )
    assert result["blocked"] is True
    assert result["exit_code"] == 1

    # Rejected file written under workspace/gates/
    rejected = tmp_path / "workspace" / "gates" / "task-042-rejected.json"
    assert rejected.exists()

    events = [
        json.loads(line)
        for line in (tmp_path / "workspace" / "events.jsonl").read_text().splitlines()
    ]
    deny = next(e for e in events if e["event"] == "DENY")
    assert deny["reason"] == "handoff_rejected"


def test_hook_allows_good_handoff(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "coder")
    good = _good_packet(1)
    result = runtime.emit(
        "PreToolUse",
        {"tool": "write_handoff_packet", "handoff_packet": good, "incoming_chain_depth": 0},
    )
    assert result.get("blocked") is not True
    assert result["status"]["last_tool_status"] == "started"


def test_hook_ignores_non_external_handoff(tmp_path):
    runtime = HookRuntime(str(tmp_path / "workspace"), "coder")
    local = _good_packet(5)
    local["risk_tier"] = "LOCAL"  # guard scoped to EXTERNAL → not validated
    result = runtime.emit(
        "PreToolUse",
        {"tool": "write_handoff_packet", "handoff_packet": local, "incoming_chain_depth": 0},
    )
    assert result.get("blocked") is not True


def test_hook_blocks_bad_handoff_nested_tool_input(tmp_path):
    # Native hooks may nest the packet under tool_input (regression for
    # fail-open hole where a nested packet skipped validation).
    runtime = HookRuntime(str(tmp_path / "workspace"), "coder")
    bad = _good_packet(5)
    result = runtime.emit(
        "PreToolUse",
        {"tool": "write_handoff_packet", "tool_input": {"handoff_packet": bad}, "incoming_chain_depth": 0},
    )
    assert result["blocked"] is True


def test_hook_reads_incoming_depth_from_current_json(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "tasks").mkdir(parents=True)
    (ws / "tasks" / "current.json").write_text(json.dumps({"provenance": {"chain_depth": 2}}))

    runtime = HookRuntime(str(ws), "coder")
    # depth 3 is correct vs incoming 2 from current.json
    good = _good_packet(3)
    result = runtime.emit(
        "PreToolUse",
        {"tool": "write_handoff_packet", "handoff_packet": good},
    )
    assert result.get("blocked") is not True
