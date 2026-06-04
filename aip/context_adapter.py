"""Context adapters (spec §"Context Adapters").

A context adapter applies inclusion/omission rules from ``.aip/context/{task_class}.yaml``
to build a *bounded* context pack written to ``workspace/context/{task_id}.md``.
The pack is injected once at task start and is the only permitted injection
surface for that task; the token budget prevents over-injection from unbounded
dynamic recall.

The packing logic (``build_context_pack``) is dependency-free and operates on an
already-parsed config dict, so it is fully testable without YAML. Loading config
files from disk (``load_context_config``) uses PyYAML, declared as the optional
``context`` extra.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# A rough chars-per-token heuristic so budgeting needs no tokenizer dependency.
_CHARS_PER_TOKEN = 4

DEFAULT_ROLES_DIR = Path.home() / ".imx" / "catalog" / "roles"


@dataclass
class ResolvedSource:
    kind: str
    title: str
    text: str
    tokens: int = 0
    omitted: str | None = None  # reason if omitted, else None


@dataclass
class ContextPack:
    task_id: str
    task_class: str
    sources: list[ResolvedSource] = field(default_factory=list)
    budget_tokens: int = 0
    used_tokens: int = 0

    def render(self) -> str:
        lines = [
            f"# Context pack — {self.task_id}",
            "",
            f"- task_class: {self.task_class}",
            f"- budget_tokens: {self.budget_tokens}",
            f"- used_tokens: {self.used_tokens}",
            "",
        ]
        included = [s for s in self.sources if s.omitted is None]
        omitted = [s for s in self.sources if s.omitted is not None]
        for source in included:
            lines.append(f"## {source.title}")
            lines.append("")
            lines.append(source.text.rstrip("\n"))
            lines.append("")
        if omitted:
            lines.append("## Omitted sources")
            lines.append("")
            for source in omitted:
                lines.append(f"- {source.title} ({source.kind}): {source.omitted}")
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4) used for budget enforcement."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _resolve_source(
    source: dict,
    *,
    repo_root: Path,
    roles_dir: Path,
) -> ResolvedSource | None:
    """Resolve a single config source into text, or None to skip silently."""
    kind = source.get("kind", "unknown")

    if kind == "conventions":
        chunks: list[str] = []
        for name in source.get("files", []):
            path = repo_root / name
            if path.exists():
                chunks.append(f"### {name}\n{path.read_text(encoding='utf-8')}")
        if not chunks:
            return ResolvedSource(kind, "conventions", "", omitted="no convention files found")
        text = "\n\n".join(chunks)
        return ResolvedSource(kind, "Conventions", text, estimate_tokens(text))

    if kind == "role_card":
        profile = source.get("profile", "")
        path = roles_dir / f"{profile}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            return ResolvedSource(kind, f"Role card: {profile}", text, estimate_tokens(text))
        return ResolvedSource(kind, f"Role card: {profile}", "", omitted=f"role card not found at {path}")

    if kind in ("gitmem_procedures", "gitmem_route_cards", "attention_refresh"):
        # These resolve from gitmem/runtime systems not available to a local,
        # file-only pack. Record the request transparently rather than fabricate.
        return ResolvedSource(kind, kind, "", omitted="source not resolvable in local file-only mode")

    return ResolvedSource(kind, kind, "", omitted=f"unknown source kind {kind!r}")


def build_context_pack(
    config: dict,
    *,
    task_id: str,
    repo_root: Path | str = ".",
    roles_dir: Path | str | None = None,
    task_packet: dict | None = None,
) -> ContextPack:
    """Build a bounded context pack from a parsed adapter config.

    Sources are resolved in config order (highest priority first) and admitted
    until the token budget is exhausted; a source that does not fit is truncated
    to the remaining budget, and anything past that is marked omitted.
    """
    repo_root = Path(repo_root)
    roles_dir = Path(roles_dir) if roles_dir is not None else DEFAULT_ROLES_DIR
    budget = int(config.get("max_context_tokens") or config.get("budget_tokens") or 0)
    task_class = str(config.get("task_class", ""))

    pack = ContextPack(task_id=task_id, task_class=task_class, budget_tokens=budget)
    used = 0

    def _admit(resolved: ResolvedSource) -> None:
        """Admit a source under the token budget, truncating/omitting as needed."""
        nonlocal used
        remaining = budget - used if budget else None
        if remaining is not None and remaining <= 0:
            resolved.omitted = "omitted: token budget exhausted"
            resolved.text = ""
            pack.sources.append(resolved)
            return
        if remaining is not None and resolved.tokens > remaining:
            keep_chars = remaining * _CHARS_PER_TOKEN
            resolved.text = resolved.text[:keep_chars] + "\n…[truncated to fit budget]"
            resolved.tokens = remaining
        used += resolved.tokens
        pack.sources.append(resolved)

    # Optionally include the task packet first (structured context) — it is
    # budget-enforced like any other source so it cannot blow the budget.
    if config.get("inject_task_packet") and task_packet is not None:
        import json

        text = "```json\n" + json.dumps(task_packet, indent=2) + "\n```"
        _admit(ResolvedSource("task_packet", "Task packet", text, estimate_tokens(text)))

    for raw in config.get("sources", []):
        if not isinstance(raw, dict):
            continue
        resolved = _resolve_source(raw, repo_root=repo_root, roles_dir=roles_dir)
        if resolved is None:
            continue
        if resolved.omitted is not None:
            pack.sources.append(resolved)
            continue
        _admit(resolved)

    pack.used_tokens = used
    return pack


def load_context_config(task_class: str, *, config_dir: Path | str) -> dict:
    """Load ``.aip/context/{task_class}.yaml`` (requires the ``context`` extra)."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
        raise RuntimeError(
            "Context adapters need PyYAML. Install with: pip install 'aip[context]'"
        ) from exc
    path = Path(config_dir) / f"{task_class}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"context config not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_context_pack(pack: ContextPack, workspace_root: Path | str) -> Path:
    """Write the rendered pack to ``workspace/context/{task_id}.md``."""
    context_dir = Path(workspace_root) / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    path = context_dir / f"{pack.task_id}.md"
    path.write_text(pack.render(), encoding="utf-8")
    return path


def build_and_write(
    task_class: str,
    task_id: str,
    *,
    workspace_root: Path | str,
    config_dir: Path | str,
    repo_root: Path | str = ".",
    roles_dir: Path | str | None = None,
    task_packet: dict | None = None,
) -> Path:
    """Load a config, build the bounded pack, and write it to the workspace."""
    config = load_context_config(task_class, config_dir=config_dir)
    pack = build_context_pack(
        config,
        task_id=task_id,
        repo_root=repo_root,
        roles_dir=roles_dir,
        task_packet=task_packet,
    )
    return write_context_pack(pack, workspace_root)
