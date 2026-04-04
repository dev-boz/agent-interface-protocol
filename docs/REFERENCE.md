# AIP Reference

## CLI Reference (`aip`)

The operator-facing command-line tool. All output is JSON for machine readability.

### Global Options

```
--workspace-root PATH   Workspace directory (default: workspace)
--session-name NAME     tmux session name (default: aip)
```

### `aip init`

Initialize the workspace directory structure and optionally create the tmux session.

```bash
aip init                                    # workspace only
aip init --ensure-session                   # workspace + tmux session
aip init --ensure-session --orchestrator-command "gemini"
aip init --ensure-session --start-directory /path/to/project
```

**Output**:
```json
{"workspace": "workspace", "session": "aip", "session_created": true}
```

### `aip session ensure`

Create the tmux session if it doesn't exist.

```bash
aip session ensure
aip session ensure --window-name orchestrator --command "claude-code"
aip session ensure --start-directory /path/to/project
```

### `aip agent spawn <name> <command>`

Spawn a new agent in a named tmux window.

```bash
aip agent spawn coder "gemini"
aip agent spawn reviewer "claude-code"
aip agent spawn researcher "amp --execute 'research auth libraries'"
aip agent spawn tester "copilot -p 'run the test suite'"
```

**Options**:
```
--start-directory PATH   Working directory for the agent
```

### `aip agent list`

List all windows (agents) in the tmux session.

```bash
aip agent list
```

**Output**:
```json
[
  {"index": 0, "name": "orchestrator", "command": "bash", "active": true},
  {"index": 1, "name": "coder", "command": "gemini", "active": false},
  {"index": 2, "name": "reviewer", "command": "claude-code", "active": false}
]
```

### `aip agent capture <target>`

Read an agent's tmux pane output. By default strips ANSI escape sequences.

```bash
aip agent capture coder                    # full pane (plain text)
aip agent capture coder --lines 20         # last 20 lines
aip agent capture coder --include-escape   # preserve ANSI escapes
```

**Progressive reverse reading** — always read from the bottom up:
```bash
aip agent capture coder --lines 5     # usually enough
aip agent capture coder --lines 20    # need more?
aip agent capture coder --lines 50    # still more? (rare)
```

### `aip agent send <target> <text>`

Send text to an agent's pane (simulates typing + Enter).

```bash
aip agent send coder "implement the auth module"
aip agent send coder "/export" --no-enter    # send without pressing Enter
```

### `aip agent kill <target>`

Gracefully stop an agent subtree. AIP shuts down descendants first, captures recent pane output into a handoff summary when useful, re-queues any claimed tasks back to `pending/`, and then removes tmux windows.

```bash
aip agent kill coder
```

This is intentionally cleanup-oriented rather than a blind kill. If an agent is interrupted mid-task, the re-queued task includes handoff metadata so another agent can continue safely.

### `aip task list`

List tasks in a given stage.

```bash
aip task list                     # pending (default)
aip task list --stage claimed
aip task list --stage done
aip task list --stage failed
aip task list --claimable         # pending tasks with all blocked_by dependencies met
```

### `aip task claim <task_id> <agent_name>`

Claim a pending task (atomic `mv` — first caller wins). `--lease-seconds` must be a positive integer.

```bash
aip task claim task-001 coder
aip task claim task-001 coder --lease-seconds 3600
```

### `aip task complete <task_id>`

Move a claimed task to done.

```bash
aip task complete task-001
aip task complete task-001 --agent-name coder
```

### `aip task fail <task_id>`

Move a claimed task to failed.

```bash
aip task fail task-001
```

### `aip task reclaim-expired`

Reclaim tasks whose lease has expired (crash recovery).

```bash
aip task reclaim-expired
aip task reclaim-expired --json    # pretty-printed output
```

### `aip hook emit`

Write a normalized hook event into the workspace manually. Useful for testing hook mappings or debugging adapter behavior.

```bash
aip hook emit --agent-name coder --event SessionStart --payload-json '{"message":"starting auth work"}'
aip hook emit --agent-name coder --event PreToolUse --payload-file payload.json
```

### `aip hook proxy`

Read a hook payload from stdin, normalize it, and write status/events into the workspace. This is the adapter command used by supported CLI hook handlers.

```bash
cat payload.json | aip hook proxy --agent-name coder
cat payload.json | aip hook proxy --agent-name coder --output-mode json-empty
```

### `aip hook print-config`

Generate hook and MCP config snippets for supported CLIs.

```bash
aip hook print-config --cli gemini --agent-name coder --tool-profile worker
aip hook print-config --cli kiro --agent-name reviewer --tool-profile reviewer
aip hook print-config --cli codex --agent-name architect --tool-profile architect
aip hook print-config --cli cursor --agent-name editor --tool-profile worker
aip hook print-config --cli qwen --agent-name analyst --tool-profile worker
```

### `aip hook install`

Merge supported hook and MCP config directly into a CLI config root.

```bash
aip hook install --cli gemini --agent-name coder --tool-profile worker --config-root /repo
aip hook install --cli kiro --agent-name reviewer --tool-profile reviewer --config-root /repo
aip hook install --cli codex --agent-name architect --tool-profile architect --config-root /repo
aip hook install --cli cursor --agent-name editor --tool-profile worker --config-root /repo
aip hook install --cli qwen --agent-name analyst --tool-profile worker --config-root /repo
```

### `aip shim watch`

Start the `aip-shim` watcher for Tier 2 CLIs (Vibe, Amp) that lack native hook support. Intercepts lifecycle events via process monitoring and translates them to workspace events.

```bash
aip shim watch --agent-name vibe-worker --cli vibe
aip shim watch --agent-name amp-worker --cli amp
```

### `aip shim check`

Check the shim status for a running agent.

```bash
aip shim check --agent-name vibe-worker
```

### `aip shim list-profiles`

List available shim profiles and their supported CLIs.

```bash
aip shim list-profiles
```

---

## MCP Server Reference (`aip-mcp`)

A stdio MCP server built on the `mcp` Python SDK. Install it on CLI agents for shared workspace coordination, but use per-role tool profiles rather than exposing every tool everywhere.

### Running

```bash
aip-mcp --workspace workspace --agent-name coder --session-name aip
aip-mcp --workspace workspace --agent-name coder --session-name aip --tool-profile worker
aip-mcp --workspace workspace --agent-name amp-worker --session-name aip --tool-profile worker-hookless
```

The server reads JSON-RPC messages from stdin and writes responses to stdout. It implements the full MCP protocol handshake (`initialize`, `ping`, `tools/list`, `tools/call`).

### Installing on CLI Agents

Each CLI agent has its own MCP configuration mechanism. Point it at `aip-mcp` with the agent's name and the appropriate tool profile:

```json
{
  "mcpServers": {
    "aip": {
      "command": "aip-mcp",
      "args": ["--workspace", "/path/to/workspace", "--agent-name", "coder", "--session-name", "aip", "--tool-profile", "worker"]
    }
  }
}
```

The exact config location varies per CLI. For Gemini, Kiro, Codex, Cursor, and Qwen, prefer `aip hook install ...` over manual edits.

### Tool Profiles

| Profile | Intended Use | Tools |
|---|---|---|
| `full` / `orchestrator` | broad control surface | all 9 tools |
| `worker` | hook-capable execution agents | `export_summary`, `register_capabilities` |
| `worker-hookless` | workers on CLIs without hooks | `report_status`, `report_progress`, `export_summary`, `register_capabilities` |
| `reviewer` / `architect` | advisory agents | `export_summary`, `read_pane`, `notify`, `register_capabilities` |
| `manager` | delegation-heavy coordinators | `export_summary`, `register_capabilities`, `read_pane`, `request_task`, `wait_for`, `spawn_teammate` |

Hook-capable workers should use hooks for lifecycle/status telemetry. `report_status` and `report_progress` remain as fallback tools for hookless CLIs.

### Tools

AIP exposes the full 9-tool coordination surface described in the project spec.

Every tool call automatically appends a one-line JSON event to `workspace/events.jsonl`.

#### `report_status`

Write the agent's current status and optionally a message. This is primarily the fallback path for CLIs that do not expose lifecycle hooks.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `status` | string | ✅ | One of: `blocked`, `failed`, `finished`, `idle`, `working` |
| `message` | string | | Human-readable context |

**Writes**: `workspace/status/{agent_name}.json` (merged)
**Appends**: `workspace/events.jsonl`

```json
{"status": "working", "message": "implementing auth module"}
```

#### `export_summary`

Persist a markdown summary for other agents to reference.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `content` | string | ✅ | Markdown content |
| `task_id` | string | | Link to a task ID |

**Writes**: `workspace/summaries/{agent_name}-{MMDD-HHMMSS}.md`
**Appends**: `workspace/events.jsonl`

```json
{"content": "## Auth Module\n\nImplemented JWT login with refresh tokens.", "task_id": "task-001"}
```

#### `register_capabilities`

Declare what this agent can do and what it cares about.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `capabilities` | string[] | ✅ | Skill labels (e.g., `["python", "testing"]`) |
| `interests` | object | | Agent interest map (see below) |

**Writes**: `workspace/status/{agent_name}.json` (merged)
**Appends**: `workspace/events.jsonl`

```json
{
  "capabilities": ["system-design", "api-design", "code-review"],
  "interests": {
    "agents": {"coder": "high", "reviewer": "high"},
    "events": {"status:failed": "high", "status:finished": "medium"},
    "summaries": {"architect-*": "high", "coder-*": "medium"}
  }
}
```

**Interest Map Structure**:

| Section | Key Format | Description |
|---|---|---|
| `agents` | agent name (e.g., `"coder"`) | How closely to watch this agent |
| `events` | event pattern (e.g., `"status:failed"`) | Which events matter |
| `summaries` | glob pattern (e.g., `"coder-*"`) | Which summaries to read |

**Priority Levels**:

| Priority | Meaning | Behavior |
|---|---|---|
| `high` | Must read immediately | Check between tasks or interrupt |
| `medium` | Read when available | Check at natural breakpoints |
| `low` | Get to it eventually | Background awareness only |

**Example Interest Maps by Role**:

```
Architect:  agents: coder=high, reviewer=high | events: failed=high, blocked=high
Coder:      agents: architect=high            | summaries: architect-*=high
Reviewer:   agents: coder=high                | events: finished=high
Tester:     agents: coder=medium              | summaries: coder-*=high
Worker:     {} (no interests — zero coordination overhead)
```

**Token Spend by Role**:

| Role | Coordination Overhead |
|---|---|
| Pure worker | ~0 tokens |
| Coder | ~150 tokens (architect summary only) |
| Reviewer | ~250 tokens (coder + architect) |
| Architect | ~500 tokens (reads broadly) |
| Orchestrator | ~100 tokens (event log tail + refs) |

#### `request_task`

Create a task in the pending queue for another agent to pick up.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_description` | string | ✅ | What needs to be done |
| `target_role` | string | | Intended agent role |
| `context` | string | | Reference to summaries or files |
| `priority` | string | | `high`, `normal`, `low` |
| `blocked_by` | string[] | | Task IDs that must complete before this task is claimable |

**Writes**: `workspace/tasks/pending/{task_id}.md`
**Appends**: `workspace/events.jsonl`

```json
{
  "task_description": "review the auth module",
  "target_role": "reviewer",
  "context": "see workspace/summaries/coder-0317-1423.md",
  "priority": "high"
}
```

With dependencies:
```json
{
  "task_description": "integration tests for auth + payments",
  "target_role": "tester",
  "priority": "normal",
  "blocked_by": ["task-041", "task-043"]
}
```

**Critical token rule**: reference files, don't paste content. Instead of sending 500 tokens of another agent's output, send a 30-token file reference.

#### `report_progress`

Lightweight progress update without full pane reads. This is primarily the fallback path for CLIs that do not expose lifecycle hooks.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `progress` | string | ✅ | Human-readable progress (e.g., `"3 of 5 files done"`) |
| `percentage` | number | | 0-100 completion estimate |

**Writes**: `workspace/status/{agent_name}.json` (merged)
**Appends**: `workspace/events.jsonl`

```json
{"progress": "3 of 5 files done", "percentage": 60}
```

#### `read_pane`

Read another agent's tmux pane. In incremental mode, AIP keeps an in-memory cursor per `(reader, target)` pair and returns only newly appended output.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `target_agent` | string | ✅ | Which agent pane to read |
| `lines` | integer | | Tail-like capture hint for non-incremental reads |
| `include_escape` | boolean | | Preserve ANSI escape sequences |
| `incremental` | boolean | | Return only output added since this reader last checked |

**Reads**: target tmux pane
**Returns**: pane content plus cursor metadata

```json
{"target_agent": "coder", "incremental": true}
```

Notes:
- `incremental: true` cannot be combined with `lines`
- first incremental read falls back to a full pane capture and seeds the cursor
- if tmux history shrinks or the cursor becomes invalid, AIP falls back to a full capture automatically

#### `wait_for`

Block until a matching event appears in `workspace/events.jsonl` or a timeout expires.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `event_filter` | string or string[] | ✅ | One or more comma-delimited `key:value` filters, e.g. `"agent:coder,status:finished"` |
| `timeout` | number | | Seconds to wait before returning a timeout result |

**Reads**: `workspace/events.jsonl`
**Returns**: matched filter, matched event, and timeout status

```json
{"event_filter": "agent:coder,status:finished", "timeout": 30}
```

#### `spawn_teammate`

Spawn a new tmux-backed teammate, register it in `agent_tree.json`, and write initial status metadata.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✅ | New teammate name |
| `cli_type` | string | ✅ | Command to run in the new tmux window |
| `capabilities` | string[] | ✅ | Initial capability labels |
| `interests` | object | | Optional interest map |
| `parent_id` | string | | Parent agent ID; defaults to the calling agent |
| `depth` | integer | | Expected depth; validated against the parent |

**Creates**: tmux window, `workspace/agent_tree.json`, `workspace/status/{agent}.json`
**Appends**: `workspace/events.jsonl`

```json
{
  "name": "reviewer",
  "cli_type": "claude-code",
  "capabilities": ["code-review", "security"],
  "interests": {"agents": {"coder": "high"}}
}
```

#### `notify`

Send a direct message to another agent (or all agents) via the event log. Dual-mode delivery: every notification is appended to the event log (permanent record). For high-priority messages to CLIs that support mid-stream injection, the message is also sent directly into the agent's running tmux pane.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `target_agent` | string | ✅ | Agent name or `"all"` for broadcast |
| `message` | string | ✅ | The message to send |
| `priority` | string | ✅ | `high`, `medium`, or `low` |
| `elicit` | boolean | | Use MCP elicitation to deliver the message interactively (supported: claude-code, codex, cursor, qwen) |

**Appends**: `workspace/events.jsonl`
**Injects** (high priority only): sends message into target agent's tmux pane if CLI supports it
**Elicits** (when `elicit: true`): uses MCP elicitation protocol for interactive delivery on supported CLIs

```json
{"target_agent": "coder", "message": "don't edit auth.py — use auth_v2.py instead", "priority": "high"}
```

With elicitation:
```json
{"target_agent": "coder", "message": "should we switch to auth_v2.py?", "priority": "high", "elicit": true}
```

**Mid-stream injection support by CLI**:

| CLI | Injection | Mechanism |
|---|---|---|
| **Claude Code** | ✅ | `/btw {message}` — injected into active thinking |
| **Codex** | ✅ | Text + Enter — injected into current turn |
| **Copilot** | ✅ | Text + Enter — sent at next gap in thinking/tool use |
| **Gemini** | ✅ | Text + Enter — model steering hint (experimental) |
| **Cursor** | ✅ | Text + Enter — injected into current turn |
| **Qwen** | ✅ | Text + Enter — injected into current turn |
| **Vibe** | ⚠️ | `aip-shim` intercept — delivered at next shim checkpoint |
| **Amp** | ⚠️ | `aip-shim` intercept — delivered at next shim checkpoint |
| Others | ❌ | Event log only |

**Medium/low priority** — event log only, regardless of CLI support. The agent reads it at its own pace.

---

## Workspace Format Reference

### Status Files (`workspace/status/{agent}.json`)

JSON files with merge semantics — each write merges into existing data.

```json
{
  "agent": "coder",
  "status": "working",
  "message": "implementing auth",
  "capabilities": ["python", "django"],
  "interests": {
    "agents": {"architect": "high"},
    "summaries": {"architect-*": "high"}
  },
  "progress": "2 of 4 files done",
  "percentage": 50,
  "updated_at": "2026-03-17T14:23:01Z"
}
```

### Event Log (`workspace/events.jsonl`)

Append-only, one JSON object per line. The orchestrator's primary dashboard.

```jsonl
{"ts":"2026-03-17T14:23:01Z","agent":"coder","event":"status","status":"working","message":"starting auth module"}
{"ts":"2026-03-17T14:23:45Z","agent":"coder","event":"progress","progress":"2 of 4 files done"}
{"ts":"2026-03-17T14:25:12Z","agent":"coder","event":"status","status":"finished"}
{"ts":"2026-03-17T14:25:13Z","agent":"coder","event":"export","file":"summaries/coder-0317-1425.md"}
{"ts":"2026-03-17T14:25:15Z","agent":"orchestrator","event":"task","action":"create","task_id":"task-002","stage":"pending","target_role":"reviewer","description":"review auth module"}
{"ts":"2026-03-17T14:25:16Z","agent":"reviewer","event":"status","status":"working"}
{"ts":"2026-03-17T14:26:00Z","agent":"coder","event":"capabilities","capabilities":["python","django"],"interests":{"agents":{"architect":"high"}}}
{"ts":"2026-03-17T14:26:10Z","agent":"reviewer","event":"task","action":"claim","task_id":"task-002","stage":"claimed","claimed_by":"reviewer"}
{"ts":"2026-03-17T14:28:00Z","agent":"architect","event":"notify","target":"coder","priority":"high","message":"don't edit auth.py — db-architect is refactoring sessions"}
{"ts":"2026-03-17T14:30:00Z","agent":"coder","event":"shutdown","phase":"completed","reason":"manual shutdown"}
```

Always read the tail: `tail -n 50 workspace/events.jsonl`

### Agent Tree (`workspace/agent_tree.json`)

Persistent teammate hierarchy used by `spawn_teammate`.

```json
{
  "orchestrator": {
    "depth": 0,
    "parent": null,
    "children": ["coder"],
    "tmux_window": "aip:orchestrator"
  },
  "coder": {
    "depth": 1,
    "parent": "orchestrator",
    "children": [],
    "tmux_window": "aip:coder",
    "cli_type": "gemini"
  }
}
```

### Task Files (`workspace/tasks/{stage}/{task_id}.md`)

Task IDs must contain only alphanumerics, dots, hyphens, or underscores (path separators are rejected to prevent directory traversal).

```markdown
# task-042
type: coding
priority: high
target_role: coder
description: implement oauth login
context: see summaries/architect-0317-1400.md
blocked_by: task-040, task-041
created_at: 2026-03-17T14:25:15Z
```

When claimed, the file moves to `claimed/` and gets lease metadata:

```markdown
# task-042
type: coding
priority: high
target_role: coder
description: implement oauth login
context: see summaries/architect-0317-1400.md
blocked_by: task-040, task-041
created_at: 2026-03-17T14:25:15Z
claimed_by: coder
lease_expires: 2026-03-17T15:25:15Z
```

### Summary Files (`workspace/summaries/{agent}-{MMDD-HHMMSS}.md`)

Free-form markdown. Named by agent and timestamp.

```markdown
## Auth Module Implementation

- Added JWT login endpoint at /api/auth/login
- Added refresh token rotation
- Tests passing: 12/12
```

---

## Task Queue

Tasks live on the filesystem and move through directories. POSIX `mv` within the same filesystem is atomic — no locks needed.

### Lifecycle

```
pending/task-042.md  →  claimed/coder-task-042.md  →  done/task-042.md
                                                   →  failed/task-042.md
```

### Claiming

An agent claims a task by atomically moving it:
```bash
aip task claim task-042 coder
```

If two agents race, one succeeds and the other gets a `TaskClaimError`. No locks, no retries — atomic rename is the lock.

### Push vs Pull

- **Push**: orchestrator sends tasks via `tmux send-keys` for urgent interactive work
- **Pull**: idle agents poll `tasks/pending/` and self-serve autonomously
- Both work simultaneously. Agents keep working even if the orchestrator crashes.

### Crash Recovery

Claimed tasks include a `lease_expires` timestamp. If an agent dies, the task stays in `claimed/` past its lease. Any agent (or the orchestrator) can reclaim:

```bash
aip task reclaim-expired
```

Default lease: 30 minutes. Override with `--lease-seconds` (must be positive).

---

## Agent Lifecycle

### Spawn
```bash
aip agent spawn coder "gemini"
aip agent spawn coder "claude-code --resume"
```

### Communicate (live)
```bash
aip agent capture coder --lines 5    # read output
aip agent send coder "now add tests" # send task
```

### Communicate (async)
Agents read/write workspace files. Summaries from dead agents persist.

### Pause
Leave it idle. CLI agents use ~150-300MB RAM. Mark status as `idle`.

### Resume
```bash
# Agent already running — send new input
aip agent send coder "now add tests"

# Agent was killed — respawn with native resume
aip agent spawn coder "gemini --resume session_abc123"
```

### Shutdown
```bash
aip agent send coder "/export"     # agent exports summary
aip agent kill coder               # then kill
```

Summary remains in workspace. Session ID tracked for future resume.

---

## Orchestrator Behaviour

The orchestrator is just another CLI agent in window 0. Its loop:

1. Read tail of `workspace/events.jsonl` — what's changed?
2. Check `workspace/status/` if needed — who's available?
3. Check `tmux list-windows` — who's running?
4. If needed agent is idle → send task to pane
5. If needed agent doesn't exist → spawn new window
6. Monitor via event log (not pane reads)
7. Collect results from summaries when events show `finished`
8. Decide next step, repeat

**Token rule**: never paste output into tasks. Reference files.

> Instead of "here's what the architect wrote: [500 tokens]", send
> "implement the auth module per the design in `workspace/summaries/architect-0317.md`"

The orchestrator can be ANY CLI agent. Swap mid-session — the new one reads workspace state and continues.

### Interest Map Usage

When delegating, the orchestrator reads the target agent's interest map and includes file references for anything marked `high`:

```
"Implement the auth module.
Design: workspace/summaries/architect-0317.md
Related: workspace/summaries/researcher-auth-libs-0317.md"
```

Two file references, ~30 tokens. The agent reads what it needs directly.

---

## Backend Compatibility — Recommended Setup Paths

**Tier 1 CLIs with built-in installer** (Gemini, Kiro, Codex, Cursor, Qwen):

```bash
aip hook install --cli gemini --agent-name coder --tool-profile worker --config-root /repo
aip hook install --cli kiro --agent-name reviewer --tool-profile reviewer --config-root /repo
aip hook install --cli codex --agent-name architect --tool-profile architect --config-root /repo
aip hook install --cli cursor --agent-name editor --tool-profile worker --config-root /repo
aip hook install --cli qwen --agent-name analyst --tool-profile worker --config-root /repo
```

**Tier 2 CLIs** (Vibe, Amp): use `aip-shim` for lifecycle telemetry alongside MCP.

```bash
aip shim watch --agent-name vibe-worker --cli vibe
aip shim watch --agent-name amp-worker --cli amp
```

**Plugin-event CLIs** (OpenCode, Kilo, or any unsupported CLI): add `aip-mcp` manually with the right profile.

```json
{
  "mcpServers": {
    "aip": {
      "command": "aip-mcp",
      "args": [
        "--workspace", "/path/to/workspace",
        "--agent-name", "amp-worker",
        "--session-name", "aip",
        "--tool-profile", "worker-hookless"
      ]
    }
  }
}
```

For hook-capable CLIs without a built-in installer yet, use `aip hook print-config ...` to generate snippets and merge them into the vendor's config manually.

---

## Fault Tolerance

### Agent Crash
Task stays in `claimed/` with a lease. After expiry → reclaimable. Last summary (if exported) persists in workspace.

### Orchestrator Crash
tmux sessions survive. All agents keep running. New orchestrator reconnects:
```bash
tmux attach -t aip
# read events.jsonl, pick up where things left off
```

### Model Fallback
Orchestrator reads errors in plain English ("quota exceeded", "rate limited"). It spawns a replacement on a different model and redirects the task. When the original recovers, bring it back.

### Network/Auth Failures
Vendor auth, quota, or credit failures are environment-specific, not AIP bugs. AIP treats any CLI agent identically once it can run in a terminal and load either hooks, MCP, or both.

---

## ACP/A2A Compatibility

AIP MCP tools map cleanly to both protocols:

| AIP Tool | ACP Equivalent | A2A Equivalent |
|---|---|---|
| `report_status` | Task status events | Task status updates |
| `register_capabilities` | Agent description | Agent Card |
| `request_task` | Task delegation | Task assignment |
| `export_summary` | Task artifact | Task artifact |
| `report_progress` | Progress events | Progress updates |
| `read_pane` | No direct equivalent | No direct equivalent |
| `wait_for` | Task completion callback | Task status subscription |
| `spawn_teammate` | Sub-task delegation | Child task creation |
| `notify` | Agent messaging | Agent-to-agent communication |

Future: add `AIP_ACP_COMPAT=true` flag to emit ACP-formatted events alongside file writes. Compatibility without coupling.

---

## Key Design Decisions

1. **Small dependency surface**: the only runtime Python dependency is the `mcp` SDK; the rest stays stdlib.
2. **SDK-backed MCP server**: use the official MCP Python SDK rather than a handwritten JSON-RPC transport.
3. **Atomic writes**: write-to-tmp-then-rename (`os.replace`) — never a half-written file.
4. **Atomic claims**: `os.rename` for task claiming — POSIX guarantees exactly one winner.
5. **Merge semantics**: status files merge updates into existing snapshots (not overwrite).
6. **Injectable runner**: `TmuxController` accepts a `runner` callable for test isolation.
7. **Interest validation**: server-side validation of priority levels and section names.
8. **Task ID validation**: reject path separators and special characters to prevent directory traversal.
9. **Graceful degradation**: corrupt JSON/event files are logged and skipped rather than crashing.

---

## Module Overview

| File | Purpose | Lines |
|---|---|---|
| `workspace.py` | Filesystem primitives — atomic writes, status, events, summaries, agent tree | ~294 |
| `tasks.py` | Task queue — create, claim, complete, fail, reclaim | ~374 |
| `tmux.py` | tmux controller — sessions, windows, capture, send | ~120 |
| `cli.py` | Operator CLI — all `aip` subcommands | ~340 |
| `hooks.py` | Hook runtime — event normalization, stdin adapters, workspace writes | ~285 |
| `hook_configs.py` | CLI config generation and install/merge helpers | ~433 |
| `mcp_server.py` | MCP server — MCP SDK wiring, 9 tools, and selective profiles | ~786 |

---

## Test Coverage

| Module | Tests | What's Covered |
|---|---|---|
| `workspace.py` | 7 | Layout creation, status merge, events, summaries, task IDs, agent tree helpers, corrupt JSON recovery, thread-safe writes |
| `tasks.py` | 11 | Full lifecycle, claim errors, lease expiry reclaim, listing, path traversal rejection, lease validation |
| `tmux.py` | 4 | Command generation via FakeRunner (no real tmux needed) |
| `hooks.py` | 13 | Hook normalization, stdin parsing, Codex notification mapping, workspace writes |
| `hook_configs.py` | 10 | Config generation and non-destructive install/merge for Gemini, Kiro, Codex |
| `mcp_server.py` | 36 | Tool calls, profiles, interests, read_pane, wait_for, spawn_teammate, notify, spawn rollback, corrupt event recovery |
| `cli.py` | 5 | Init, hook proxy/config/install flows, agent kill with tree preservation on kill failure |
