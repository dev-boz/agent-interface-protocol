"""Tests for context adapters (aip.context_adapter)."""

from __future__ import annotations

import json

import pytest

from aip.context_adapter import (
    build_and_write,
    build_context_pack,
    estimate_tokens,
    load_context_config,
    write_context_pack,
)
from aip.cli import main


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_build_includes_conventions(tmp_path):
    (tmp_path / "CONVENTIONS.md").write_text("Always write tests.\n")
    config = {
        "task_class": "code_review",
        "max_context_tokens": 1000,
        "sources": [{"kind": "conventions", "files": ["CONVENTIONS.md", "MISSING.md"]}],
    }
    pack = build_context_pack(config, task_id="t-1", repo_root=tmp_path)
    rendered = pack.render()
    assert "Always write tests." in rendered
    assert "## Conventions" in rendered
    assert pack.used_tokens > 0


def test_inject_task_packet(tmp_path):
    config = {"task_class": "impl", "max_context_tokens": 1000, "inject_task_packet": True, "sources": []}
    packet = {"task_id": "t-2", "risk_tier": "LOCAL"}
    pack = build_context_pack(config, task_id="t-2", repo_root=tmp_path, task_packet=packet)
    rendered = pack.render()
    assert "## Task packet" in rendered
    assert '"risk_tier": "LOCAL"' in rendered


def test_unresolvable_sources_marked_omitted(tmp_path):
    config = {
        "task_class": "code_review",
        "max_context_tokens": 1000,
        "sources": [
            {"kind": "gitmem_procedures", "query": {"task_class": "code_review"}},
            {"kind": "role_card", "profile": "reviewer"},
        ],
    }
    pack = build_context_pack(config, task_id="t-3", repo_root=tmp_path, roles_dir=tmp_path / "roles")
    rendered = pack.render()
    assert "## Omitted sources" in rendered
    assert "gitmem_procedures" in rendered
    assert "role_card" in rendered


def test_role_card_resolved_when_present(tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "reviewer.md").write_text("# Reviewer\nBe thorough.\n")
    config = {
        "task_class": "code_review",
        "max_context_tokens": 1000,
        "sources": [{"kind": "role_card", "profile": "reviewer"}],
    }
    pack = build_context_pack(config, task_id="t-4", repo_root=tmp_path, roles_dir=roles)
    rendered = pack.render()
    assert "Be thorough." in rendered
    assert "Role card: reviewer" in rendered


def test_budget_truncates_oversized_source(tmp_path):
    big = "x" * 1000
    (tmp_path / "CONVENTIONS.md").write_text(big)
    config = {
        "task_class": "code_review",
        "max_context_tokens": 10,  # ~40 chars
        "sources": [{"kind": "conventions", "files": ["CONVENTIONS.md"]}],
    }
    pack = build_context_pack(config, task_id="t-5", repo_root=tmp_path)
    assert pack.used_tokens <= 10
    assert "[truncated to fit budget]" in pack.render()


def test_second_source_omitted_when_budget_exhausted(tmp_path):
    (tmp_path / "A.md").write_text("a" * 40)  # ~10 tokens
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "reviewer.md").write_text("role text")
    config = {
        "task_class": "code_review",
        "max_context_tokens": 10,
        "sources": [
            {"kind": "conventions", "files": ["A.md"]},
            {"kind": "role_card", "profile": "reviewer"},
        ],
    }
    pack = build_context_pack(config, task_id="t-6", repo_root=tmp_path, roles_dir=roles)
    rendered = pack.render()
    assert "budget exhausted" in rendered


def test_load_config_and_write(tmp_path):
    config_dir = tmp_path / ".aip" / "context"
    config_dir.mkdir(parents=True)
    (config_dir / "code_review.yaml").write_text(
        "task_class: code_review\nmax_context_tokens: 500\nsources:\n  - kind: conventions\n    files:\n      - CONVENTIONS.md\n"
    )
    (tmp_path / "CONVENTIONS.md").write_text("Be careful.\n")
    config = load_context_config("code_review", config_dir=config_dir)
    assert config["task_class"] == "code_review"
    pack = build_context_pack(config, task_id="t-7", repo_root=tmp_path)
    out = write_context_pack(pack, tmp_path / "workspace")
    assert out == tmp_path / "workspace" / "context" / "t-7.md"
    assert "Be careful." in out.read_text()


def test_cli_context_build(tmp_path, capsys):
    config_dir = tmp_path / ".aip" / "context"
    config_dir.mkdir(parents=True)
    (config_dir / "impl.yaml").write_text(
        "task_class: impl\nmax_context_tokens: 500\nsources:\n  - kind: conventions\n    files:\n      - CONVENTIONS.md\n"
    )
    (tmp_path / "CONVENTIONS.md").write_text("Rules here.\n")
    rc = main([
        "context", "build",
        "--task-class", "impl",
        "--task-id", "t-8",
        "--workspace-root", str(tmp_path / "workspace"),
        "--config-dir", str(config_dir),
        "--repo-root", str(tmp_path),
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    pack_path = tmp_path / "workspace" / "context" / "t-8.md"
    assert out["context_pack"] == str(pack_path)
    assert "Rules here." in pack_path.read_text()


def test_real_code_review_config_loads():
    # The repo's own .aip/context/code_review.yaml should parse and build.
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    config = load_context_config("code_review", config_dir=repo / ".aip" / "context")
    pack = build_context_pack(config, task_id="t-real", repo_root=repo)
    assert pack.task_class == "code_review"
    assert pack.render().startswith("# Context pack — t-real")
