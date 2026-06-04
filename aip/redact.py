"""Secret / PII redaction for exported artifacts (spec §"Redaction hooks").

Scrubs secrets, tokens, internal hostnames, and PII from text before it lands in
``workspace/`` where other agents can read it. Used by ``export_summary`` (so
secrets never reach a summary) and by the ``aip redact <file>`` CLI — the
``aip-redact`` tool the spec runs from PostToolUse / SessionEnd hooks over
exported summaries and transcripts.
"""
from __future__ import annotations

import re
from pathlib import Path

# (label, pattern, replacement). Order matters: structured tokens are matched
# before generic assignments so the most specific label wins.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED:private_key]",
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED:jwt]"),
    ("github_token", re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github_token]"),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack_token]"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_access_key]"),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "[REDACTED:bearer_token]"),
    ("authorization", re.compile(r"(?i)\bauthorization:\s*\S+"), "[REDACTED:authorization]"),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)(\s*[:=]\s*)['\"]?[^\s'\"]{4,}['\"]?"
        ),
        r"\1\2[REDACTED:secret]",
    ),
    (
        "private_ip",
        re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b"),
        "[REDACTED:private_ip]",
    ),
    (
        "internal_host",
        re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:internal|corp|local|intranet)\b", re.IGNORECASE),
        "[REDACTED:internal_host]",
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED:email]"),
]


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, {label: count}) for the given text."""
    reasons: dict[str, int] = {}
    for label, pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        if n:
            reasons[label] = reasons.get(label, 0) + n
    return text, reasons


def redact_file(path: str | Path) -> dict[str, int]:
    """Redact a file in place. Returns {label: count}; the file is only rewritten
    when something matched."""
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    redacted, reasons = redact_text(original)
    if reasons:
        p.write_text(redacted, encoding="utf-8")
    return reasons
