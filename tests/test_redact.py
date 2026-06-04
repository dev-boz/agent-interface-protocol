"""Tests for secret/PII redaction (aip.redact + `aip redact` CLI)."""

from __future__ import annotations

import json

from aip.cli import main
from aip.redact import redact_file, redact_text


def test_redacts_github_token():
    text = "deploy key ghp_abcdefghijklmnopqrstuvwxyz0123456789 used"
    out, reasons = redact_text(text)
    assert "ghp_" not in out
    assert reasons.get("github_token") == 1
    assert "[REDACTED:github_token]" in out


def test_redacts_secret_assignment_keeps_key():
    text = "PASSWORD=hunter2secret\napi_key: abcd1234efgh"
    out, reasons = redact_text(text)
    assert "hunter2secret" not in out
    assert "abcd1234efgh" not in out
    # The key name is preserved, only the value is scrubbed.
    assert "PASSWORD=" in out
    assert reasons.get("secret_assignment") == 2


def test_redacts_email_and_private_ip():
    text = "contact byron@example.com on host 10.1.2.3"
    out, reasons = redact_text(text)
    assert "byron@example.com" not in out
    assert "10.1.2.3" not in out
    assert reasons.get("email") == 1
    assert reasons.get("private_ip") == 1


def test_redacts_private_key_block():
    text = (
        "before\n-----BEGIN RSA PRIVATE KEY-----\nMIIabc\nmore\n"
        "-----END RSA PRIVATE KEY-----\nafter"
    )
    out, reasons = redact_text(text)
    assert "MIIabc" not in out
    assert reasons.get("private_key") == 1
    assert out.startswith("before")
    assert out.endswith("after")


def test_benign_text_unchanged():
    text = "# Review\nEverything looks good. No issues found."
    out, reasons = redact_text(text)
    assert out == text
    assert reasons == {}


def test_redact_file_rewrites_in_place(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n")
    reasons = redact_file(p)
    assert reasons.get("github_token") == 1
    assert "ghp_" not in p.read_text()


def test_redact_file_no_match_leaves_file(tmp_path):
    p = tmp_path / "clean.md"
    p.write_text("nothing secret here\n")
    reasons = redact_file(p)
    assert reasons == {}
    assert p.read_text() == "nothing secret here\n"


def test_cli_redact_command(tmp_path, capsys):
    p = tmp_path / "summary.md"
    p.write_text("password=supersecretvalue\n")
    rc = main(["redact", str(p)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total_redactions"] == 1
    assert "supersecretvalue" not in p.read_text()
