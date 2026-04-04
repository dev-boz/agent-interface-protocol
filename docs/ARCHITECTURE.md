# Agent Interface Protocol (AIP) — Agent Teams Mux

## The Core Insight

AI agents are Unix processes. They read input, they reason, they produce output. Unix solved process orchestration, communication, and persistence decades ago. tmux is the missing piece that turns these solved problems into a multi-agent system with zero infrastructure.

Instead of protocols (ACP, SSE), servers, message brokers, or frameworks — you need tmux, a shared filesystem, and one small MCP server.

## How AIP Is Different

| Other approaches | AIP |
|---|---|
| Requires HTTP server or WebSocket gateway | tmux only — zero servers |
| Agents isolated, no inter-agent communication | Any agent reads any other agent's pane |
| Vendor lock-in (single CLI or model) | Any CLI agent or vendor (11 backends, 3 tiers) |
| Heavy frameworks, dozens of CLI commands | Selective MCP tools + tmux commands |
| Message brokers, pub/sub, REST APIs | Shared filesystem + tmux pane buffers |
| Protocol adapters per vendor | LLMs are the parser — the protocol is English |
| Special orchestrator process or server | Orchestrator is just another agent — swap mid-session |

The key differences:

- **No server**: Doesn't require a running server process to broker messages. Agent Interface Protocol has none. The tmux server IS the infrastructure.
- **Agents are aware of each other**: In Agent Interface Protocol, any agent can read any other agent's pane.
- **Vendor neutral**: Agent Interface Protocol works with any CLI agent — Claude, Gemini, Kimi, aider, Codex, anything.
- **Minimal footprint**: Agent Interface Protocol is hooks (3-tier) + 5 MCP tools and tmux commands.
- **The orchestrator is just another agent**: not a special process, not a server, not a framework. Any CLI agent can orchestrate. Swap orchestrators mid-session.


## Architecture

```
tmux server "aip"
├── window 0: orchestrator (any CLI agent)
├── window 1: coder (claude/gemini/aider/etc)
├── window 2: reviewer (any CLI agent)
├── window 3: researcher (any CLI agent)
└── ...spawned and killed as needed

workspace/
├── summaries/          ← agent output summaries
│   ├── coder-0317-1423.md
│   └── reviewer-0317-1425.md
├── status/             ← agent status files
│   ├── coder.json      {"status": "idle", "capabilities": [...]}
│   └── reviewer.json   {"status": "working", "task": "..."}
├── tasks/              ← task queue (atomic claiming via mv)
│   ├── pending/        ← unclaimed tasks
│   ├── claimed/        ← in-progress (agent-name prefixed)
│   ├── done/           ← completed
│   └── failed/         ← move back to pending/ to retry
└── events.jsonl        ← append-only event log (orchestrator's dashboard)
```

### Three-Tier Memory

**Hot memory** — tmux pane buffers. Live, streamable, instant. Any agent reads any other agent's current output via `tmux capture-pane`. Note: the pane buffer is just a viewport — the CLI process itself maintains its own complete session history internally (that's what `/export` and `--resume` operate on). tmux scrollback rolling off loses nothing of substance.

**Event log** — `workspace/events.jsonl`. Append-only, chronological. Every status change, task assignment, progress update, and export from every agent. This is the orchestrator's primary dashboard — it reads one file to know the state of the entire system without touching any panes.

**Cold storage** — workspace files. Persistent, searchable, survives full system restarts. Summaries from dead agents remain for others to reference. Agent session history lives inside the CLI's own native storage (resumed via `--resume` flags).

The orchestrator's default loop is: read the event log, act on what's changed. Pane reads are the exception — only when it needs to see what an agent is doing in detail right now.

## Why tmux

**Process management**: spawn, kill, list agents with native commands.

**Live communication**: any agent reads any other agent's pane via `tmux capture-pane`. No pipes, no pub/sub, no message bus. The tmux server IS the shared memory space.

**Persistence**: sessions survive crashes. If the orchestrator dies, every agent keeps running. Reconnect and continue.

**Service discovery**: `tmux list-windows` tells you what agents exist. Name windows by role and you have a registry.

**Debugging**: `tmux attach` and watch agents think in real time.

**Remote support**: SSH gives you everything. `ssh box "tmux capture-pane -pt aip:coder"` reads a remote agent's output. Same primitives, different machine.

## The Output Problem (Solved)

**Problem**: how does the orchestrator know when an agent is done thinking vs still working, and where the actual output starts?

**Solution 1 — Marker calculation**: tell the agent to output a sentinel marker using a calculation so the marker doesn't leak into thinking. Example: instruct the agent to "output %# five times" — it computes `%#%#%#%#%#` cleanly at the boundary. Parser does a simple string split.

**Solution 2 — Export hooks**: most CLIs support hooks or custom tools. The agent calls an export tool when done. Output appears in the shared workspace, not in the terminal stream.

**Solution 3 — Let the agent parse**: the consuming agent IS an LLM. It can read raw terminal output and understand it natively. No structured parsing needed. The protocol is English.

**Solution 4 — Read backwards**: the pane buffer is right there. Read from the bottom up until you hit the thinking/marker boundary. Trivially cheap.

**Solution 5 — Push formatting inward**: every CLI agent supports some extensibility — MCP servers, custom tools, hooks, shell aliases. Install a tiny shim that formats output on the agent's side. The agent doesn't even know it's doing it. No external parser needed.

## The MCP Server (aip-mcp)

One MCP server, installed where needed. Coordination uses a three-tier hook system:

- **Tier 1 — Native CLI hooks** (8 backends: claude-code, copilot, gemini, kiro, codex, opencode, cursor, qwen): zero overhead, zero agent awareness. Hook configs emit lifecycle telemetry directly.
- **Tier 2 — aip-shim interactive intercept** (2 backends: amp, vibe/Mistral): watches tmux pane via regex, matches approval prompts, injects responses. No hook support needed from the CLI.
- **Tier 3 — MCP fallback** (e.g. kilo): `report_status` and `report_progress` tools for any CLI without hooks or shim support. Highest token cost.

Additional coordination features:
- `elicit` parameter on `notify` for MCP elicitation (claude-code, codex, cursor, qwen)
- `INJECTION_COMMANDS` for mid-stream messaging: claude-code (`/btw`), codex, copilot, gemini, cursor, qwen
- `BACKEND_LAUNCH_COMMANDS` maps all 11 backends to their shell commands

### Tools

Every tool call automatically appends a one-line JSON event to `workspace/events.jsonl`. The orchestrator reads this single file to track the entire system.

```
report_status
  - fallback status writer for hookless CLIs

export_summary
  - persist markdown output for other agents

register_capabilities
  - declare capabilities and interest maps

request_task
  - create a pending task for delegation

report_progress
  - fallback progress writer for hookless CLIs

wait_for
  - block on matching event-log activity

spawn_teammate
  - create tmux-backed child agents with tree registration

notify
  - send agent-to-agent messages with event-log delivery and priority-aware injection

read_pane
  - inspect live tmux pane output, including cursor-based incremental reads
```

The key change from the original design is selectivity: not every agent gets all 9 tools. `aip-mcp --tool-profile ...` trims the surface area by role, and hook-capable workers can avoid `report_status` and `report_progress` entirely.

### Event Log Format

```jsonl
{"ts":"2026-03-17T14:23:01Z","agent":"coder","event":"status","status":"working","message":"starting auth module"}
{"ts":"2026-03-17T14:23:45Z","agent":"coder","event":"progress","progress":"2 of 4 files done"}
{"ts":"2026-03-17T14:25:12Z","agent":"coder","event":"status","status":"finished"}
{"ts":"2026-03-17T14:25:13Z","agent":"coder","event":"export","file":"summaries/coder-0317-1425.md"}
{"ts":"2026-03-17T14:25:15Z","agent":"orchestrator","event":"task","target":"reviewer","task":"review auth module"}
{"ts":"2026-03-17T14:25:16Z","agent":"reviewer","event":"status","status":"working"}
```

The orchestrator reads the last N lines of this file and knows exactly what's happening across all agents. No pane reads, no polling status files individually. One file, one read.

### This is the coordination surface.

### Why Not ACP?

ACP is a spec that everyone implements differently. You end up writing adapters per vendor — the exact same normalisation problem as parsing different CLI outputs, just moved from terminal formatting to protocol implementation.

AIP sidesteps this entirely. You write the MCP server once, layer hooks on top where the CLI supports them, and expose only the tools the role actually needs. The agents don't need to know they're participating in a multi-agent system. They just see a small coordination surface and workspace files.

If ACP ever stabilises, your MCP tools can emit ACP-compatible events as a translation layer. But you're not blocked waiting.

### Why Not SSE?

SSE exists to stream events from a server to a client over HTTP. In AIP, live output is the tmux pane buffer — already streaming, already there. SSE is a transport layer you've made redundant.

SSE is also what makes ACP implementations inconsistent — everyone handles connection lifecycle, reconnection, and event formatting differently. tmux handles all of it natively.

### Why Not PTY as a Separate Layer?

tmux already uses PTYs internally. Every pane is backed by one. Adding a PTY layer on top is redundant. tmux manages them for you. You don't need to think about them.

## Task Queue

Tasks live in `workspace/tasks/` and move through directories. POSIX `mv` within the same filesystem is atomic — no locks needed.

### Lifecycle

```
pending/task-042.md  →  claimed/coder-task-042.md  →  done/task-042.md
                                                   →  failed/task-042.md (retry: mv back to pending/)
```

An agent claims a task by moving it: `mv tasks/pending/task-042.md tasks/claimed/coder-task-042.md`. If the move succeeds, the agent owns it. If it fails, someone else got there first.

### Task Format

```markdown
# task-042
type: coding
priority: high
description: implement oauth login
context: see summaries/architect-0317-1400.md
```

### Push vs Pull

The orchestrator can still push tasks directly via `tmux send-keys` for urgent interactive work. But idle agents can also poll `tasks/pending/` and grab work autonomously. This means agents keep working even if the orchestrator is busy or crashed.

### Crash Recovery via Leasing

Task files in `claimed/` include a lease:

```
claimed_by: coder
lease_expires: 2026-03-17T12:40:00Z
```

If an agent dies, the task stays in `claimed/` past its lease. Any agent (or the orchestrator) can reclaim expired tasks by moving them back to `pending/`.

## Fault Tolerance

The orchestrator is an LLM reading panes. It doesn't need error codes or retry logic — it reads "quota exceeded" or "rate limited" in plain English and reacts naturally.

**Model fallback**: orchestrator keeps a list of available models and endpoints. When it reads an error in an agent's pane, it spawns a replacement on a different model and redirects the task. When the original model recovers, it can be brought back.

**Orchestrator crash**: tmux sessions survive. All agents keep running. Event log and workspace are intact. New orchestrator reconnects to existing tmux server, reads `events.jsonl`, picks up where things left off.

**Agent crash**: task remains in `claimed/` with a lease. After expiry, it's reclaimable. The crashed agent's last summary (if exported) is still in the workspace for context.

## IDE Agents

IDE agents (Cursor, Windsurf, Cline, Copilot) become full team members by running their terminal work inside the AIP tmux session. One instruction: "use `tmux attach -t aip:frontend` for your terminal."

Now the IDE agent shows up in `tmux list-windows`, the orchestrator reads its pane like any other agent, and it calls the same MCP tools. From the orchestrator's perspective, it's just another agent — it doesn't know or care that there's a GUI attached.

The human developer in the IDE is also just an agent. Read your task from `workspace/tasks/`, do the work, update the workspace via hooks or MCP, and the orchestrator moves on. No special approval workflow — you're just a slow agent with good taste.

## Cloud Agents (Git Bridge)

Cloud agents like Jules can't access tmux but can access a git repo. Push the workspace to GitHub and they participate via commits.

### How It Works

1. Orchestrator writes a task to `workspace/tasks/pending/`
2. Task gets pushed to the repo (manually, via hook, or on a schedule)
3. Cloud agent (Jules, CI pipeline, etc) picks up the task, does the work, commits results and a summary
4. Results get pulled back into the local workspace

### GitHub Watcher Pane

To bridge cloud agent activity back into the local event log, spawn a small watcher in its own tmux window:

```bash
tmux new-window -t aip -n github-watcher "./watch-prs.sh"
```

The watcher polls GitHub (via `gh` CLI) for PRs and commits from cloud agents, then appends events to the local log:

```jsonl
{"ts":"2026-03-17T14:40:00Z","agent":"jules","event":"status","status":"finished","pr":"#42","message":"Auth module refactor complete"}
```

Now the orchestrator sees cloud agent completions in the same event log as local agent updates. One file, one view, regardless of where agents are running.

### What Cloud Agents Are

They're contractors, not managed workers. The orchestrator can leave work for them and check results, but can't watch them in real time, can't kill them, can't redirect them mid-task. Useful for slow background work — big refactors, extensive test suites, migrations — where you don't need real-time feedback.

## Agent Lifecycle

### Spawn
```bash
tmux new-window -t aip -n coder "claude-code"
# or
tmux new-window -t aip -n coder "gemini --resume abc123"
```

### Communicate (live)
```bash
# orchestrator reads coder's latest output
tmux capture-pane -pt aip:coder

# orchestrator sends task to coder
tmux send-keys -t aip:coder "implement the auth module" Enter
```

### Communicate (async)
Agents read/write workspace files. Summaries from dead agents persist. Status files show who's doing what.

### Pause
Just leave it. Idle CLI agents use minimal resources (~150-300MB RAM). The tmux window costs almost nothing (~200KB for default scrollback). Mark status as idle.

### Resume
Every CLI supports native resume. The agent handles its own session persistence — you never need to serialise or restore context externally.
```bash
# agent already running in pane — just send new input
tmux send-keys -t aip:coder "now add tests" Enter

# agent was killed — respawn with native resume
tmux new-window -t aip -n coder "gemini --resume session_abc123"
tmux new-window -t aip -n coder "claude-code --resume"
```

### Shutdown
```bash
# agent exports its own summary via MCP tool (or orchestrator tells it to)
# most CLIs also support /export or equivalent natively
tmux send-keys -t aip:coder "/export" Enter
# wait for file
tmux kill-window -t aip:coder
```

Summary file remains in workspace for other agents to reference. Native session ID is tracked so the agent can be resumed later.

## Orchestrator Behaviour

The orchestrator is just another CLI agent in window 0. No special framework. Its loop:

1. Read tail of `workspace/events.jsonl` — what's changed?
2. Check `workspace/status/` if needed — who's available and capable?
3. Check `tmux list-windows` — who's actually running?
4. If needed agent exists and is idle → send task to pane
5. If needed agent doesn't exist → spawn in new window
6. Monitor via event log (not pane reads)
7. Collect results from summaries when events show "finished"
8. Decide next step, repeat

**Critical token rule**: the orchestrator never pastes another agent's output into a task. It references files. Instead of "here's what the architect wrote: [500 tokens]", it sends "implement the auth module per the design in `workspace/summaries/architect-0317.md`". The agent reads the file directly. The orchestrator's context stays clean and small.

The orchestrator can be ANY CLI agent. Claude Code, Copilot, Gemini, Codex, Cursor, Qwen, or any of the 11 supported backends. Swap mid-session if needed — the new orchestrator reads workspace state and continues.

## Agent Interest Maps

Agents don't communicate spontaneously. They need a reason. Interest maps define what each agent cares about and how urgently — turning inter-agent communication from "read everything" into a targeted subscription model.

### How It Works

Each agent has an `interests` field in its status file (`workspace/status/{agent}.json`), registered via the `register_capabilities` MCP tool on startup:

```json
{
  "agent": "architect",
  "status": "idle",
  "capabilities": ["system-design", "api-design", "code-review"],
  "interests": {
    "agents": {
      "coder": "high",
      "reviewer": "high",
      "researcher": "medium",
      "tester": "low"
    },
    "events": {
      "status:failed": "high",
      "status:blocked": "high",
      "status:finished": "medium",
      "progress": "low"
    },
    "summaries": {
      "architect-*": "high",
      "coder-*": "medium",
      "researcher-*": "medium"
    }
  }
}
```

### Priority Levels

**high** — must read immediately. Agent should check these between tasks or even interrupt current work. Architect must read other architects' output. Coder must read the architect's design.

**medium** — read when available. Check these at natural breakpoints — after finishing a subtask, before starting the next one.

**low** — get to it eventually. Background awareness. Don't spend tokens polling for these.

### Example Interest Maps by Role

**Architect** — high interest in everything structural. Reads coder output to verify design adherence, reviewer output for quality signals, other architects for alignment. High event interest in failures and blocks.

```json
{
  "agents": { "coder": "high", "reviewer": "high", "researcher": "medium" },
  "events": { "status:failed": "high", "status:blocked": "high", "status:finished": "medium" },
  "summaries": { "architect-*": "high", "coder-*": "medium" }
}
```

**Coder** — narrow interest. Only cares about architect designs and its own task. Everything else is someone else's problem.

```json
{
  "agents": { "architect": "high" },
  "events": { "status:blocked": "low" },
  "summaries": { "architect-*": "high" }
}
```

**Reviewer** — medium-broad interest. Reads coder output (that's its job), checks architect designs for context, low interest in other reviewers.

```json
{
  "agents": { "coder": "high", "architect": "medium" },
  "events": { "status:finished": "high" },
  "summaries": { "coder-*": "high", "architect-*": "medium" }
}
```

**Tester** — interested in coder output to know what to test, reviewer output for known issues.

```json
{
  "agents": { "coder": "medium", "reviewer": "medium" },
  "events": { "status:finished": "medium" },
  "summaries": { "coder-*": "high", "reviewer-*": "medium" }
}
```

**Pure worker** — no interest map at all. Gets task, does task, reports done. Zero coordination token overhead.

```json
{
  "agents": {},
  "events": {},
  "summaries": {}
}
```

### How the Orchestrator Uses Interest Maps

When delegating a task, the orchestrator reads the target agent's interest map and includes file references — not contents — for anything marked high:

```
"Implement the auth module.
Design: workspace/summaries/architect-0317.md
Related: workspace/summaries/researcher-auth-libs-0317.md"
```

Two file references, maybe 30 tokens. The agent reads what it needs directly. The orchestrator never carries other agents' output in its own context.

### Token Spend by Role

Interest maps make coordination cost predictable before you spawn an agent:

| Role | Coordination overhead |
|---|---|
| Pure worker | ~0 tokens (no reads) |
| Coder | ~150 tokens (architect summary only) |
| Reviewer | ~250 tokens (coder + architect summaries) |
| Architect | ~500 tokens (reads broadly) |
| Orchestrator | ~100 tokens (event log tail + file references) |

Compare this to "every agent reads every pane" which could easily be 5000+ tokens per cycle.

## Adding a New Agent Type

1. Install the agent CLI
2. Install the shared MCP server on it
3. `tmux new-window -t aip -n name "agent-cli"`
4. Done

No adapter code. No parser. No protocol integration. The MCP server gives it the status/export tools. tmux gives it connectivity to every other agent.

## What This Replaces

| Traditional approach | AIP equivalent |
|---|---|
| ACP protocol | Selective MCP tools + hooks |
| SSE streaming | tmux pane buffer |
| Message broker | tmux server (shared memory) |
| Service discovery | `tmux list-windows` |
| Session persistence | tmux sessions + native CLI resume |
| Agent framework | bash + tmux commands |
| Protocol normalisation | agents read each other natively (LLMs ARE parsers) |
| Database for state | filesystem |
| HTTP transport (remote) | SSH |
| Observability / audit trail | workspace/events.jsonl |
| Distributed job queue | workspace/tasks/ with atomic mv |
| Circuit breaker / retry | orchestrator reads errors in English |
| Remote cloud agents | git push/pull + watcher pane |
| Agent communication model | Interest maps (targeted subscriptions) |

## Key Principles

**Agents format their own output** — push responsibility inward via MCP tools or CLI hooks, don't parse from outside.

**LLMs are the parser** — an agent reading another agent's raw output understands it natively. The protocol is English.

**Everything is files and panes** — if it's alive, read the pane. If it's dead, read the file. Same interface either way.

**Don't manage what manages itself** — CLIs handle their own session persistence, their own resume, their own context. Let them.

**Spawn freely, kill only under pressure** — idle agents are cheap. Their loaded context is valuable. Don't optimise prematurely.

**LLMs build adapters in minutes** — the "hard problem" of parsing diverse CLI outputs isn't hard when the parser is an LLM that can reverse-engineer any output format in seconds. Don't design around human development speed.

**Vendor neutral by default** — built on primitives (tmux, files, SSH) that predate every AI tool. Works with anything that has a CLI.

## Token Efficiency

The whole point is to minimise token burn. Agents should almost never read raw pane output. Enforce this read hierarchy:

| Level | What | Cost | When |
|---|---|---|---|
| 1 | `tail -n 20 workspace/events.jsonl` | ~50 tokens | Always check first — what happened? |
| 2 | `cat workspace/status/coder.json` | ~20 tokens | Who's doing what right now? |
| 3 | `cat workspace/summaries/coder-0317.md` | ~100 tokens | What did they produce? |
| 4 | `read_pane(incremental=true)` | ~20 tokens | What's changed since the last read? |
| 5 | `tmux capture-pane -pt aip:coder -S -5` | ~30 tokens | Quick peek at live output (last 5 lines) |
| 6 | `tmux capture-pane -pt aip:coder -S -20` | ~100 tokens | Need more context — expand progressively |
| 7 | Full pane read | ~1000+ tokens | Almost never needed |

### Progressive Reverse Reading

`tmux capture-pane` supports line ranges. Always read from the bottom up:

```bash
# last 5 lines — usually enough
tmux capture-pane -pt aip:coder -S -5

# need more? last 20
tmux capture-pane -pt aip:coder -S -20

# need more? last 50, but stop at the marker
tmux capture-pane -pt aip:coder -S -50
```

Combined with the marker system, the agent reads backwards until it hits the marker and stops. It never reads thinking tokens. The output section might be 10 lines while the thinking was 200 — progressive reverse reading means you only pay for the 10.

### Incremental Reads

For agents that monitor another pane repeatedly, the MCP `read_pane` tool supports `incremental=true`. The runtime keeps a cursor per `(reader, target)` pair and returns only newly appended pane output after the first read.

If tmux history shrinks or the cursor is no longer valid, AIP falls back to a full pane capture and reseeds the cursor automatically.

### The Rule

Instruct every agent: **"Check events.jsonl first. Read summaries second. Only read panes when the structured data isn't enough, and read from the bottom up."**

The MCP server is the token-efficient layer. That's what it was designed for.

## ACP/A2A Compatibility

AIP doesn't depend on ACP or A2A, but the MCP tools map cleanly to both:

| AIP MCP tool | ACP equivalent | A2A equivalent |
|---|---|---|
| `report_status` | Task status events | Task status updates |
| `register_capabilities` | Agent description | Agent Card |
| `request_task` | Task delegation | Task assignment |
| `export_summary` | Task artifact | Task artifact |
| `report_progress` | Progress events | Progress updates |
| `read_pane` | No direct equivalent | No direct equivalent |

If the ecosystem converges, add a flag: `AIP_ACP_COMPAT=true`. The MCP tools then also emit ACP-formatted events alongside the file writes. Compatibility without coupling — build it later if needed.

## Implementation Notes

**ANSI stripping**: CLI tools output heavy escape sequences — colours, spinners, cursor movement. When reading panes, pipe through a cleaner so agents aren't confused by raw escape codes in their context window. Example: `tmux capture-pane -pt aip:coder | sed 's/\x1b\[[0-9;]*m//g'` or use `ansifilter`. Alternatively, use `tmux capture-pane -p -t aip:coder` without `-e` to get plain text (tmux strips escapes by default when `-e` is omitted).

**Atomic file writes**: the MCP server should write status files via write-to-tmp-then-rename, not direct writes. This ensures the orchestrator never reads a half-written JSON object. Example: write to `workspace/status/coder.json.tmp`, then `mv` to `workspace/status/coder.json`. Rename is atomic on all Unix filesystems.

**Event log growth**: `events.jsonl` grows indefinitely. The orchestrator should always read the tail, not the whole file — `tail -n 50 workspace/events.jsonl` gives recent state without burning tokens. Optionally rotate or truncate on session restart.

## Phase 1: Test Plan

### Step 1 — Basic tmux orchestration
- Start a tmux server named `aip`
- Spawn two CLI agents in named windows
- Orchestrator in window 0 reads the other panes
- Verify agents can see each other's output

### Step 2 — MCP server (aip-mcp)
- Build the 5-tool MCP server
- Install on two different CLI agents
- Verify status reporting and summary export work
- Verify orchestrator can read status and summaries from workspace/

### Step 3 — Task delegation
- Orchestrator sends a coding task to agent in window 1
- Agent completes, exports summary
- Orchestrator reads summary, sends review task to agent in window 2
- Reviewer reads coder's summary, produces review
- Full cycle with no manual intervention

### Step 4 — Lifecycle management
- Test killing and respawning agents with native resume
- Test orchestrator crash recovery (reconnect to existing tmux)
- Test adding agents mid-session
- Test swapping orchestrator to a different CLI agent

### Step 5 — Task queue
- Place tasks in `workspace/tasks/pending/`
- Verify agents can claim tasks via atomic `mv`
- Test crash recovery: kill an agent mid-task, verify lease expires and task is reclaimable
- Test agent self-service: idle agent polls `pending/` and grabs work without orchestrator

### Step 6 — Fault tolerance
- Trigger a quota error on one model
- Verify orchestrator reads the error and spawns a fallback agent
- Kill orchestrator, reconnect new orchestrator, verify it picks up state from `events.jsonl`

### Step 7 — Remote agents
- Repeat steps 1-3 with one agent on a remote machine via SSH
- Verify `ssh box "tmux capture-pane -pt aip:coder"` works transparently

### Step 8 — Cloud agents (optional)
- Push workspace to a GitHub repo
- Simulate a cloud agent committing a summary
- Verify the watcher pane picks up the commit and appends to `events.jsonl`

## Platform Notes

- **Linux/Mac**: works natively
- **Windows**: WSL (tmux works perfectly), native Windows is phase 2
- **Containers**: install tmux in image, or `docker exec` into container
- **Remote**: SSH wraps everything transparently
- **IDE agents**: attach IDE terminal to tmux session, install MCP server
- **Cloud agents**: git push/pull workspace, watcher pane bridges events back to local log
