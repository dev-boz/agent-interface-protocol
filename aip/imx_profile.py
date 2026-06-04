"""IMX capability profile enforcement seam per AIP spec §16."""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("aip.imx_profile")

DEFAULT_IMX_CATALOG = Path.home() / ".imx" / "catalog"


@dataclass
class ImxProfileConstraints:
    """Constraints loaded from an IMX capability profile."""

    profile_name: str = ""
    allow: list[str] = field(default_factory=list)    # allowed tool patterns
    deny: list[str] = field(default_factory=list)     # denied tool patterns
    risk_tiers: list[str] = field(default_factory=lambda: ["READ_ONLY", "LOCAL"])
    approval_required_for: list[str] = field(default_factory=list)  # risk tiers requiring approval
    pre_tool_hooks: list[dict] = field(default_factory=list)  # {name, tool_pattern, script, on_fail}
    loaded: bool = False   # True if an IMX profile was actually found and loaded


def _parse_yaml_simple(text: str) -> dict:
    """Parse a flat YAML with string values and string-list values."""
    result = {}
    current_key = None
    current_list = None
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("  - ") or stripped.startswith("- "):
            item = stripped.lstrip("- ").strip()
            if current_list is not None:
                current_list.append(item)
        elif ":" in stripped and not stripped.startswith(" "):
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = val
                current_key = key
                current_list = None
            else:
                result[key] = []
                current_key = key
                current_list = result[key]
    return result


def load_imx_profile(
    profile_name: str, *, catalog_dir: Path | None = None
) -> ImxProfileConstraints:
    """Load an IMX capability profile from the catalog directory.

    If the profile file is not found, returns permissive defaults (loaded=False).
    """
    catalog = catalog_dir or DEFAULT_IMX_CATALOG
    profile_path = catalog / "profiles" / f"{profile_name}.yaml"

    if not profile_path.exists():
        logger.debug("No IMX profile found at %s — using permissive defaults", profile_path)
        return ImxProfileConstraints(profile_name=profile_name, loaded=False)

    text = profile_path.read_text(encoding="utf-8")
    data = _parse_yaml_simple(text)

    def _as_list(val) -> list[str]:
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str) and val:
            return [val]
        return []

    constraints = ImxProfileConstraints(
        profile_name=profile_name,
        allow=_as_list(data.get("allow", [])),
        deny=_as_list(data.get("deny", [])),
        risk_tiers=_as_list(data.get("risk_tiers", [])) or ["READ_ONLY", "LOCAL"],
        approval_required_for=_as_list(data.get("approval_required_for", [])),
        pre_tool_hooks=[],  # nested dicts not parseable by simple parser
        loaded=True,
    )
    logger.info("Loaded IMX profile %r from %s", profile_name, profile_path)
    return constraints


def check_tool_allowed(
    tool_name: str, constraints: ImxProfileConstraints
) -> tuple[bool, str]:
    """Check whether a tool is allowed by the IMX profile constraints.

    Returns (allowed, reason).
    """
    if not constraints.loaded:
        return (True, "no IMX profile — permissive default")

    # Deny list checked first
    for pattern in constraints.deny:
        if fnmatch.fnmatch(tool_name, pattern):
            return (
                False,
                f"tool '{tool_name}' denied by IMX profile '{constraints.profile_name}'",
            )

    # Allow list enforced only if non-empty
    if constraints.allow:
        for pattern in constraints.allow:
            if fnmatch.fnmatch(tool_name, pattern):
                return (True, "allowed")
        return (
            False,
            f"tool '{tool_name}' not in IMX profile allowlist",
        )

    return (True, "allowed")


def check_risk_tier_allowed(
    risk_tier: str, constraints: ImxProfileConstraints
) -> tuple[bool, str]:
    """Check whether a risk tier is permitted by the IMX profile constraints.

    Returns (allowed, reason).
    """
    if not constraints.loaded:
        return (True, "no IMX profile — permissive default")

    if risk_tier in constraints.risk_tiers:
        return (True, "allowed")

    return (
        False,
        f"risk tier '{risk_tier}' not permitted by IMX profile '{constraints.profile_name}'",
    )


def list_available_profiles(*, catalog_dir: Path | None = None) -> list[str]:
    """List all available IMX profiles in the catalog directory.

    Returns a sorted list of profile stem names (without .yaml extension).
    """
    catalog = catalog_dir or DEFAULT_IMX_CATALOG
    profiles_dir = catalog / "profiles"

    if not profiles_dir.is_dir():
        return []

    return sorted(p.stem for p in profiles_dir.glob("*.yaml"))
