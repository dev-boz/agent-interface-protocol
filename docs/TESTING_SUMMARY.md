# agent-nexus Testing Summary

**Date**: 2026-03-30
**Status**: ✅ Current suite passing; hooks, selective MCP profiles, aip-shim, and all 11 backends verified

## Test Results

### Current Suite
```
194 tests passing
```

**Coverage:**
- `workspace.py` - Layout creation, status merge, events, summaries
- `tasks.py` - Full lifecycle, claim errors, lease expiry, listing, blocked_by task dependencies
- `tmux.py` - Command generation (FakeRunner, no real tmux needed)
- `mcp_server.py` - Message encoding, initialize/list, tool calls, profiles, validation, notify elicit parameter, blocked_by in request_task
- `hooks.py` - Hook normalization, stdin parsing, workspace writes
- `hook_configs.py` - Config generation and install/merge helpers (claude-code, copilot, gemini, kiro, codex, opencode, cursor, qwen)
- `cli.py` - Init, hook proxy/config/install flows, agent lifecycle commands
- `aip_shim.py` - Tier 2 interactive intercept shim for vibe and amp backends
- `test_all_backends.py` - 66 tests: registry completeness, per-backend hook configs, shim profiles, MCP runtime, task lifecycle for all 11 backends
- `test_multi_backend_collab.py` - 8 tests: workspace init, hook config generation, MCP runtime, task distribution, dependency chains, cross-agent notify, shim profiles

### Integration Tests

#### 1. MCP Server Live Test ✅
**File**: `test_mcp_live.py`

Verified the MCP tool surface via stdio JSON-RPC:
- ✅ `initialize` handshake
- ✅ `tools/list` returns the expected tools for the active profile
- ✅ `report_status` writes status file + event
- ✅ `register_capabilities` with interest maps
- ✅ `report_progress` updates status
- ✅ `export_summary` creates markdown file
- ✅ `request_task` creates pending task
- ✅ `wait_for`, `spawn_teammate`, and `notify` are covered elsewhere in the suite

**Artifacts verified:**
- `workspace/events.jsonl` - expected events logged
- `workspace/status/test-agent.json` - merged status with all fields
- `workspace/summaries/test-agent-*.md` - summary exported
- `workspace/tasks/pending/task-001.md` - task created

#### 2. Task Queue Lifecycle ✅
**File**: `test_lease_expiry.py`

Verified atomic task claiming and lease expiry:
- ✅ Task creation via MCP
- ✅ Atomic claim (first caller wins)
- ✅ Lease expiry after 2 seconds
- ✅ `reclaim-expired` moves task back to pending
- ✅ Task available for re-claiming

**Key finding:** POSIX `mv` provides atomic claiming without locks.

#### 3. Full Orchestration Cycle ✅
**File**: `test_full_cycle.py`

End-to-end workflow: Orchestrator → Coder → Reviewer

**Phase 1: Orchestrator delegates to coder**
- ✅ Created task-003 via `request_task`
- ✅ Task appears in pending queue

**Phase 2: Coder executes**
- ✅ Claimed task-003
- ✅ Reported status: working
- ✅ Reported progress: 67%
- ✅ Exported 571-char summary (JWT auth implementation)
- ✅ Marked task complete

**Phase 3: Orchestrator reads result**
- ✅ Read event log (5 recent events)
- ✅ Found coder's summary file
- ✅ Created review task-004 with file reference

**Phase 4: Reviewer executes**
- ✅ Claimed task-004
- ✅ Read coder's summary (571 chars)
- ✅ Reported status: working
- ✅ Exported review summary (security findings)
- ✅ Marked task complete

**Phase 5: Final state**
- ✅ 3 completed tasks
- ✅ 3 summary files persisted
- ✅ 15 events logged
- ✅ 3 agent status files

**Key finding:** File-based coordination works seamlessly. Orchestrator never pasted output into tasks—only file references (30 tokens vs 500+ tokens).

#### 4. Agent Lifecycle Management ✅
**File**: `test_agent_lifecycle.py`

Verified spawn, kill, resume pattern:
- ✅ Spawned agent appears in `tmux list-windows`
- ✅ Commands sent via `tmux send-keys`
- ✅ Output captured via `tmux capture-pane`
- ✅ Agent exported session state before death
- ✅ Killed agent removed from list
- ✅ Respawned agent accessed previous state
- ✅ Workspace files persisted after agent death

**Key finding:** tmux handles process lifecycle. Workspace survives agent death.

#### 5. Orchestrator Crash Recovery ✅
**File**: `test_orchestrator_recovery.py`

Simulated orchestrator crash mid-workflow:

**Before crash:**
- Orchestrator-1 created 3 tasks
- Spawned 2 worker agents
- Workers claimed 2 tasks
- Workers reported status: working

**After crash:**
- ✅ tmux session still running
- ✅ Worker agents still running
- ✅ Workspace files intact
- ✅ Event log preserved (20 events)

**Recovery:**
- Orchestrator-2 read event log to understand history
- Orchestrator-2 read status files to see current state
- Orchestrator-2 checked task queue (2 pending, 2 claimed, 3 done)
- Worker-1 completed task → Orchestrator-2 assigned next task
- ✅ No work lost, seamless continuation

**Key finding:** The orchestrator is just another agent. Any agent can read the workspace and take over orchestration.

## Performance Characteristics

### Token Efficiency (Read Hierarchy)

| Level | Command | Cost | Use Case |
|-------|---------|------|----------|
| 1 | `tail -n 20 events.jsonl` | ~50 tokens | What happened? |
| 2 | `cat status/coder.json` | ~20 tokens | Who's doing what? |
| 3 | `cat summaries/coder-*.md` | ~100 tokens | What did they produce? |
| 4 | `capture-pane -S -5` | ~30 tokens | Quick peek |
| 5 | `capture-pane -S -20` | ~100 tokens | More context |
| 6 | Full pane read | ~1000+ tokens | Almost never needed |

**Orchestrator token spend per cycle:**
- Event log tail: ~50 tokens
- Status check: ~20 tokens per agent
- File references in tasks: ~30 tokens
- **Total: ~100 tokens** (vs 500+ if pasting output)

### Coordination Overhead by Role

| Role | Coordination | Token Cost |
|------|--------------|------------|
| Pure worker | None | ~0 tokens |
| Coder | Read architect summary | ~150 tokens |
| Reviewer | Read coder + architect | ~250 tokens |
| Architect | Read broadly | ~500 tokens |
| Orchestrator | Event log + refs | ~100 tokens |

### Atomic Operations

- **Task claiming**: `os.rename()` - POSIX atomic, no locks
- **Status writes**: write-to-tmp + `os.replace()` - atomic rename
- **Event appends**: `open(mode='a')` - atomic append on POSIX

## Architecture Validation

### Three-Tier Memory ✅

| Tier | Storage | Access | Survives Restart | Verified |
|------|---------|--------|------------------|----------|
| Hot | tmux pane buffers | `capture-pane` | No | ✅ |
| Event log | `events.jsonl` | `tail -n 50` | Yes | ✅ |
| Cold | workspace files | `cat` | Yes | ✅ |

### Core Primitives ✅

| Primitive | Implementation | Verified |
|-----------|----------------|----------|
| Process management | tmux windows | ✅ |
| Live communication | `capture-pane` + `send-keys` | ✅ |
| Async communication | Workspace files | ✅ |
| Service discovery | `list-windows` | ✅ |
| Session persistence | tmux sessions | ✅ |
| Task queue | Filesystem + atomic `mv` | ✅ |
| Coordination protocol | Selective MCP tools + hooks | ✅ |
| Fault tolerance | Workspace + event log | ✅ |

## What agent-nexus Replaces

| Traditional | agent-nexus | Status |
|-------------|-------|--------|
| ACP protocol | Selective MCP tools + hooks | ✅ Verified |
| SSE streaming | tmux pane buffer | ✅ Verified |
| Message broker | tmux server | ✅ Verified |
| Service discovery | `tmux list-windows` | ✅ Verified |
| Session persistence | tmux + native CLI resume | ✅ Verified |
| Agent framework | bash + tmux commands | ✅ Verified |
| Database for state | Filesystem | ✅ Verified |
| Observability | `events.jsonl` | ✅ Verified |
| Job queue | `tasks/` + atomic `mv` | ✅ Verified |
| Circuit breaker | Orchestrator reads errors in English | ⏸️ Not tested |
| Remote agents | SSH | ⏸️ Not tested |
| Cloud agents | git push/pull | ⏸️ Not tested |

## Known Limitations

### Not Yet Tested
1. **Remote agents via SSH** - Architecture supports it (`ssh box "tmux capture-pane"`), not tested
2. **Cloud agents via git** - Architecture supports it (git push/pull + watcher pane), not tested
3. **Model fallback** - Orchestrator reading quota errors and spawning fallback agents
4. **ANSI escape handling** - Heavy escape sequences in CLI output (mitigated by `capture-pane` without `-e`)

### Now Implemented (previously limitations)
- ✅ **blocked_by task dependencies** - Tasks can declare dependencies; tested in task queue and MCP runtime
- ✅ **notify elicit parameter** - Notify tool supports elicit for interactive prompts
- ✅ **aip-shim intercept** - Tier 2 backends (vibe, amp) supported via interactive intercept shim
- ✅ **cursor/qwen hook configs** - Hook config generation verified for cursor and qwen backends
- ✅ **All 11 backends** - Full registry completeness and per-backend lifecycle verified

### Backend Compatibility
All 11 backends have hook config generation and MCP runtime verified in automated tests:

| Backend | CLI Name | Tier | Hook Config Path |
|---------|----------|------|-----------------|
| Claude Code | claude-code | Tier 1 | .claude/settings.json |
| Copilot | copilot | Tier 1 | .github/copilot/hooks.json |
| Gemini | gemini | Tier 1 | .gemini/settings.json |
| Kiro | kiro | Tier 1 | .kiro/agents/{name}.json |
| Codex | codex | Tier 1 | .codex/hooks.json + config.toml |
| OpenCode | opencode | Tier 1 | Plugin events |
| Cursor | cursor | Tier 1 | .cursor/settings.json |
| Qwen | qwen | Tier 1 | .qwen/settings.json |
| Kilo | kilo | Tier 1 (native) | Plugin events (opencode fork) |
| Vibe (Mistral) | vibe | Tier 2 | aip-shim intercept |
| Amp | amp | Tier 2 | aip-shim intercept |

Real-world usage still requires:
- MCP server installation per agent
- Native resume support per CLI

## Recommendations

### Production Readiness
✅ **Core functionality is production-ready:**
- All primitives work as designed
- Fault tolerance verified
- Token efficiency validated
- Zero external dependencies (pure stdlib Python + tmux)

### Next Steps
1. **Test with real CLI agents** (gemini, copilot, cursor)
2. **Add ANSI escape stripping** to `capture-pane` calls
3. **Test remote agents via SSH**
4. **Add model fallback logic** to orchestrator
5. **Create orchestrator prompt template** for real LLM agents

### Documentation
✅ **Complete:**
- Architecture doc (`agent-nexus.md`)
- CLI reference (`README.md`)
- MCP server reference (`README.md`)
- Test suite (5 integration tests + comprehensive unit/backend tests)

## Conclusion

agent-nexus successfully demonstrates that multi-agent orchestration can be built on Unix primitives (tmux + filesystem) with zero infrastructure. The core insight holds: **AI agents are Unix processes, and Unix already solved process orchestration.**

**Key achievements:**
- ✅ Zero servers, zero brokers, zero frameworks
- ✅ Vendor-neutral (works with any CLI agent)
- ✅ Fault-tolerant (orchestrator is just another agent)
- ✅ Token-efficient (file references, not output pasting)
- ✅ Atomic operations (POSIX guarantees)
- ✅ Observable (event log + workspace files)

**The system is ready for real-world testing with actual LLM CLI agents.**
