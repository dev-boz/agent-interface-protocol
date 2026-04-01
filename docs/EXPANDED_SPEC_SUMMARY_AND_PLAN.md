# Expanded Spec Summary And Plan

## Baseline

- Spec reviewed: [agent-nexus.md](../agent-nexus.md)
- Current implementation reviewed: `aip/`, `tests/`, `README.md`
- Validation result: `pytest -q` passed with `194 passed`

## Current Infrastructure Status

### Implemented

- Workspace primitives are in place:
  - `summaries/`
  - `status/`
  - `tasks/pending|claimed|done|failed`
  - `events.jsonl`
  - `agent_tree.json`
- tmux control exists for:
  - session creation
  - agent window spawning
  - pane capture
  - sending text to panes
  - killing windows
- Task queue exists with:
  - atomic claim via rename
  - lease expiry and reclaim
  - done/failed transitions
  - interrupted-task requeue with handoff metadata
- MCP runtime already supports the expanded coordination set:
  - `wait_for`
  - `spawn_teammate`
  - `notify`
  - interest maps
  - agent tree registration
  - high-priority pane injection
- Recursive shutdown and subtree cleanup already exist.

### Partially Implemented

- `wait_for` exists and works, but it is implemented as a polling loop rather than filesystem notifications.
- Interest maps are stored and validated, but there is not yet higher-level orchestration logic that uses them to shape tool installation or task payload generation.

### Implemented (formerly partially implemented or missing)

- `notify` now supports `elicit` parameter for MCP elicitation (claude-code, codex, cursor, qwen)
- Three-tier hook system fully implemented:
  - Tier 1: Native CLI hooks for 8 backends (claude-code, copilot, gemini, kiro, codex, opencode, cursor, qwen)
  - Tier 2: aip-shim interactive intercept for 2 backends (amp, vibe/Mistral)
  - Tier 3: MCP fallback via `report_status` / `report_progress` (e.g. kilo)
- Selective MCP tool exposure by role and CLI capability
- Hook fallback split where only hookless CLIs receive `report_status` and `report_progress`
- Task dependency support via `blocked_by` — blocked tasks stay unclaimable until dependencies are done
- `INJECTION_COMMANDS` for mid-stream messaging (claude-code, codex, copilot, gemini, cursor, qwen)
- `BACKEND_LAUNCH_COMMANDS` maps all 11 backends to their shell commands

### Still Missing

- Prompt detection, output settling, and incremental cursor-based pane reads
- IDE bridge / VSIX extension
- Cloud bridge / GitHub watcher

## Main Gaps Against Expanded Spec

### 1. ~~Hooks Are Architectural In The Spec But Absent In The Repo~~ — RESOLVED

Hooks are fully implemented with a three-tier system: Tier 1 native CLI hooks (claude-code, copilot, gemini, kiro, codex, opencode, cursor, qwen), Tier 2 aip-shim intercept (amp, vibe/Mistral), and Tier 3 MCP fallback (kilo). Hook configs exist for all 8 Tier 1 backends. Workers using hook-capable CLIs need zero MCP coordination tools.

### 2. ~~Tool Exposure Is Static~~ — RESOLVED

Selective MCP tool exposure is implemented. `aip-mcp --tool-profile` trims the surface area by role, and hook-capable workers avoid `report_status` and `report_progress` entirely. Pure workers carry zero extra coordination surface.

### 3. ~~Task Dependency Semantics Are Not Implemented~~ — RESOLVED

`blocked_by` task dependencies are fully implemented with claim-time validation. Blocked tasks stay unclaimable until dependencies are done. Queue state remains filesystem-native and deterministic.

### 4. Orchestration Runtime Needs Hardening — PARTIALLY RESOLVED

- `notify` now supports `elicit` parameter for MCP elicitation (claude-code, codex, cursor, qwen)
- `wait_for` still uses polling (file-watch upgrade is a future improvement)
- Output-settling checks and incremental cursor-based pane reads remain future work

### 5. Docs And Live Scripts Are Behind — IN PROGRESS

Documentation is being updated to reflect the current three-tier hook system, 11 supported backends, selective MCP, and `blocked_by` task dependencies.

## Execution Plan

### Phase 1. Hooks Foundation — ✅ COMPLETE

Goal: establish a minimal generic hook runtime that external CLIs can call today.

All deliverables met: hook event normalization, workspace writer, `aip hook emit` CLI entry point, lifecycle and tool event tests. Hook configs exist for 8 Tier 1 backends (claude-code, copilot, gemini, kiro, codex, opencode, cursor, qwen). aip-shim implemented for Tier 2 backends (amp, vibe/Mistral).

### Phase 2. Selective Tool Exposure — ✅ COMPLETE

Goal: align MCP surface area with the hooks-first design.

All deliverables met: configurable tool filtering in `aip-mcp`, role/profile-based tool sets, hookless fallback profiles. Workers run with reduced or zero coordination tools; orchestrators retain full surface. Tests verify `tools/list` varies by profile.

### Phase 3. Task Dependencies — ✅ COMPLETE

Goal: add `blocked_by` to the queue model.

All deliverables met: task parser/serializer supports dependencies, claim-time validation against completed tasks, tests for blocked/unblocked/transition scenarios. Blocked tasks stay unclaimable until dependencies are done.

### Phase 4. Runtime Hardening

Goal: bring runtime behavior in line with the expanded orchestration spec.

Deliverables:

- file-watch backend for `wait_for` with polling fallback
- output-settling check before inline `notify` injection
- initial support path for `elicit`
- groundwork for prompt detection / incremental pane reads

Success criteria:

- waiting becomes event-driven where platform support exists
- inline injection is safer on Codex, Copilot, and Gemini

### Phase 5. Documentation And Integration Cleanup

Goal: make the repo describe the actual system.

Deliverables:

- update README and architecture docs
- update live MCP test script to current tool model
- document hook installation examples per CLI

### Phase 6. Optional Extensions

- VSIX / IDE bridge
- GitHub watcher for cloud agents
- richer orchestration logic driven by interest maps

## Recommended Immediate Work

Phases 1–3 are complete. 194 tests passing. 11 backends supported across three tiers. Recommended next steps:

- Phase 4: file-watch upgrade for `wait_for`, output-settling checks
- Phase 5: finish documentation updates to fully reflect current state
- Phase 6: IDE bridge / VSIX extension, GitHub watcher for cloud agents
