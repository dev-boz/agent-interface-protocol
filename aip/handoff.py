"""Contamination-aware handoff packet validation (spec §"Contamination-Aware
Handoffs").

When one agent hands a task to another, the handoff packet must propagate
contamination state and increment ``chain_depth``. Before an EXTERNAL dispatch a
handoff packet must:

1. ``provenance.chain_depth`` equals the incoming packet's ``chain_depth`` + 1
2. ``contamination_risk.sanitized`` is ``true`` OR
   ``contamination_risk.untrusted_sources`` is a non-empty list
3. ``outcome`` is set

Packets that fail validation are written to
``workspace/gates/{handoff_id}-rejected.json`` with the reason, not dispatched.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .workspace import isoformat_z, utc_now

logger = logging.getLogger("aip.handoff")


def validate_handoff_packet(packet: dict, *, incoming_chain_depth: int) -> tuple[bool, str]:
    """Return (ok, reason) for an outgoing handoff packet."""
    if not isinstance(packet, dict):
        return (False, "handoff packet must be an object")

    provenance = packet.get("provenance")
    if not isinstance(provenance, dict):
        return (False, "provenance is required and must be an object")
    try:
        outgoing_depth = int(provenance.get("chain_depth"))
    except (TypeError, ValueError):
        return (False, "provenance.chain_depth must be an integer")
    expected = incoming_chain_depth + 1
    if outgoing_depth != expected:
        return (
            False,
            f"chain_depth not incremented correctly: expected {expected}, got {outgoing_depth}",
        )

    contamination = packet.get("contamination_risk")
    if not isinstance(contamination, dict):
        return (False, "contamination_risk is required and must be an object")
    sanitized = contamination.get("sanitized") is True
    untrusted = contamination.get("untrusted_sources")
    has_untrusted = isinstance(untrusted, list) and len(untrusted) > 0
    if not sanitized and not has_untrusted:
        return (
            False,
            "contamination_risk.sanitized must be true or untrusted_sources must be listed",
        )

    if not packet.get("outcome"):
        return (False, "outcome must be set")

    return (True, "ok")


def reject_handoff(
    workspace_root: str | Path,
    handoff_id: str,
    reason: str,
    packet: dict,
) -> Path:
    """Write a rejected handoff to ``workspace/gates/{handoff_id}-rejected.json``."""
    gates = Path(workspace_root) / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    path = gates / f"{handoff_id}-rejected.json"
    record = {
        "handoff_id": handoff_id,
        "rejected": True,
        "reason": reason,
        "packet": packet,
        "rejected_at": isoformat_z(utc_now()),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    logger.info("Rejected handoff %s: %s", handoff_id, reason)
    return path
