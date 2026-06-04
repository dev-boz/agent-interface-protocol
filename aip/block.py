"""Dangerous-command block rules shared by the native hook path and the Tier-2
shim (spec §"Hook Guard Patterns").

Patterns live in ``.aip/block`` — one regex per line, ``#`` comments and blank
lines ignored. Both the native PreToolUse hook (aip.hooks) and the interactive
intercept shim (aip.aip_shim) load these rules so dangerous commands are
intercepted regardless of which tier an agent runs under.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("aip.block")


@dataclass
class BlockRule:
    """A pattern that should be denied automatically."""

    pattern: re.Pattern[str]
    reason: str


def load_block_rules_from_file(path: str | Path) -> list[BlockRule]:
    """Load block rules from a plain-text file (one regex per line).

    Lines starting with '#' and blank lines are skipped.
    """
    rules: list[BlockRule] = []
    p = Path(path)
    if not p.exists():
        return rules
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rules.append(BlockRule(pattern=re.compile(line, re.IGNORECASE), reason=line[:80]))
        except re.error:
            logger.warning("Invalid regex in block file %s: %r", path, line)
    return rules


def find_block_file(workspace_root: str | Path) -> Path | None:
    """Locate the ``.aip/block`` file relative to a workspace root, if present."""
    ws = Path(workspace_root)
    for candidate in (ws.parent / ".aip" / "block", ws / ".aip" / "block"):
        if candidate.exists():
            return candidate
    return None


def load_block_rules(workspace_root: str | Path) -> list[BlockRule]:
    """Load block rules from the workspace's ``.aip/block`` file (or [] if none)."""
    path = find_block_file(workspace_root)
    if path is None:
        return []
    rules = load_block_rules_from_file(path)
    logger.info("Loaded %d block rules from %s", len(rules), path)
    return rules


def load_escalate_rules(workspace_root: str | Path) -> list[BlockRule]:
    """Load human-escalation patterns from ``.aip/escalate``.

    Same one-regex-per-line format as ``.aip/block``. A command matching one of
    these requires human approval via a gate record rather than auto-decision
    (spec §"Human escalation shims").
    """
    ws = Path(workspace_root)
    for candidate in (ws.parent / ".aip" / "escalate", ws / ".aip" / "escalate"):
        if candidate.exists():
            rules = load_block_rules_from_file(candidate)
            logger.info("Loaded %d escalation rules from %s", len(rules), candidate)
            return rules
    return []


def match_block(command: str, rules: list[BlockRule]) -> BlockRule | None:
    """Return the first block rule matching ``command``, or None."""
    if not command:
        return None
    for rule in rules:
        if rule.pattern.search(command):
            return rule
    return None
