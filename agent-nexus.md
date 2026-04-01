# Agent-Nexus

## The Core Insight

AI agents are Unix processes. They read input, they reason, they produce output. Unix solved process orchestration, communication, and persistence decades ago. tmux is the missing piece that turns these solved problems into a multi-agent system with zero infrastructure.

Instead of protocols (ACP, SSE), servers, message brokers, or frameworks — you need tmux, a shared filesystem, CLI hooks for automatic reporting, and selective MCP tools only where agent judgment is required.

## How Agent-Nexus Is Different

Every existing tool in this space either adds unnecessary infrastructure or solves only part of the problem.

| Tool | What it does | What it doesn't do |
|---|---|---|
| **CAO** (AWS Labs) | tmux sessions + MCP coordination | Requires an HTTP server to broker messages |
| **CSP** | CLI agents in tmux with PTY proxy | Requires a WebSocket/HTTP gateway |
| **dmux** | Parallel agents in tmux + git worktrees | No inter-agent communication — agents are isolated |
| **NTM** | Named tmux pane management + broadcast | No agent awareness — just sends same prompt to all |
| **Overstory** | Rich orchestration with tool guards | SQLite mail system, heavy framework, 36 CLI commands |
| **Operator** | Kanban-driven agent orchestration | REST API, web component, ticket-first not agent-first |
| **workmux** | tmux windows per git worktree | Session manager, no agent coordination |
| **claude-code-agent-farm** | 20+ parallel Claude Code agents | Single-vendor (Claude only), lock-based coordination |
| **organisciak/atmux** | tmux session manager with browse/send | Session management only, no shared memory or MCP |

**Agent-Nexus is the only design where agents read each other directly via tmux panes, coordinate through one shared MCP server, and need zero servers, zero brokers, zero frameworks.**

The key differences:

- **No server**: CAO and CSP both require a running server process to broker messages. Agent-Nexus has none. The tmux server IS the infrastructure.
- **Agents are aware of each other**: dmux, NTM, and workmux run agents in parallel but agents don't know about each other. In Agent-Nexus, any agent can read any other agent's pane.
- **Vendor neutral**: claude-code-agent-farm is Claude-only. Agent-Nexus works with any CLI agent — Claude, Gemini, Kimi, aider, Codex, anything.
- **Minimal footprint**: Overstory has 36 CLI commands and a SQLite mail system. Agent-Nexus is hooks (3-tier) + 5 MCP tools and tmux commands.
- **The orchestrator is just another agent**: not a special process, not a server, not a framework. Any CLI agent can orchestrate. Swap orchestrators mid-session.

## Architecture

```
tmux server "anex"
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
├── events.jsonl        ← append-only event log (orchestrator's dashboard)
└── agent_tree.json     ← live agent hierarchy (who spawned whom)
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

**Remote support**: SSH gives you everything. `ssh box "tmux capture-pane -pt anex:coder"` reads a remote agent's output. Same primitives, different machine.

## The Output Problem (Solved)

**Problem**: how does the orchestrator know when an agent is done thinking vs still working, and where the actual output starts?

**Solution 1 — Marker calculation**: tell the agent to output a sentinel marker using a calculation so the marker doesn't leak into thinking. Example: instruct the agent to "output %# five times" — it computes `%#%#%#%#%#` cleanly at the boundary. Parser does a simple string split.

**Solution 2 — Export hooks**: most CLIs support hooks or custom tools. The agent calls an export tool when done. Output appears in the shared workspace, not in the terminal stream.

**Solution 3 — Let the agent parse**: the consuming agent IS an LLM. It can read raw terminal output and understand it natively. No structured parsing needed. The protocol is English.

**Solution 4 — Read backwards**: the pane buffer is right there. Read from the bottom up until you hit the thinking/marker boundary. Trivially cheap.

**Solution 5 — Push formatting inward**: every CLI agent supports some extensibility — MCP servers, custom tools, hooks, shell aliases. Install a tiny shim that formats output on the agent's side. The agent doesn't even know it's doing it. No external parser needed.

**Solution 6 — Prompt detection**: auto-detect the CLI's prompt pattern (e.g. `$`, `>>>`, `#`, `claude>`) on startup. After sending a command, watch for the prompt to reappear — that means the command finished. Cleaner than the marker trick for one-shot calls via the universal CLI MCP. Doesn't require instructing the agent about markers.

**Solution 7 — Output settling**: no new output for 300ms means the command is probably done. Simple fallback when prompt detection fails or the CLI has a non-standard prompt. Combined with prompt detection, these two cover completion detection for headless/one-shot calls without any agent cooperation.

## Coordination: Hooks + MCP

Agent-nexus uses two coordination mechanisms. Hooks are automatic and cost zero agent context tokens. MCP tools are intentional and require tool definitions in the agent's context. The design principle: **use hooks for everything automatic, reserve MCP tools for things that require agent judgment.**

This is the single biggest token savings in the architecture. Every MCP tool definition is ~60-100 tokens of context. Eight tools = 500-800 tokens carried by every agent, every turn, whether used or not. By moving automatic reporting to hooks, pure worker agents can run with **zero MCP tools** and zero coordination token overhead.

### Hooks (Automatic — Zero Agent Context)

Three tiers of hook support. All produce identical events in `workspace/events.jsonl`. From the orchestrator's perspective, there is no difference between a native hook and an intercepted prompt — the event format is the same.

```
Hook events → workspace/events.jsonl + workspace/status/{agent}.json

SessionStart   → {"event":"status","status":"working"}
PreToolUse     → {"event":"tool","tool":"bash","status":"started"}
PostToolUse    → {"event":"tool","tool":"bash","status":"completed"}
TaskCompleted  → {"event":"status","status":"finished"}
SessionEnd     → {"event":"status","status":"idle"}
SubagentStart  → {"event":"subagent","action":"spawned"}
SubagentStop   → {"event":"subagent","action":"terminated"}
```

#### Tier 1 — Native Hooks

CLI fires hooks natively. A hook handler script writes to the workspace. Zero overhead, zero agent awareness.

| CLI | Hook Support |
|---|---|
| Claude Code | SessionStart, PreToolUse, PostToolUse, Stop, SessionEnd, SubagentStart, SubagentStop, TeammateIdle, TaskCompleted |
| Copilot | sessionStart, preToolUse, postToolUse, etc. |
| Gemini | BeforeTool, AfterTool, agent/model lifecycle events |
| Kiro | AgentSpawn, UserPromptSubmit, PreToolUse, PostToolUse, Stop |
| OpenCode | tool.execute.before, tool.execute.after (plugin events) |
| Codex | agent-turn-complete, userpromptsubmit, preToolUse, postToolUse |

#### Tier 2 — Interactive Intercept (`aip-shim`)

For CLIs without native hooks (Amp, Aider, Open Interpreter, any CLI with interactive approval prompts). The `aip-shim` watches the tmux pane output via `pipe-pane`, regex-matches approval prompts, emits standard events to the workspace, and injects responses via `send-keys`.

**How it works:**

1. Agent runs in interactive mode (auto-approve OFF) — it naturally blocks on approval prompts
2. `aip-shim` watches `tmux pipe-pane` output, regex-matches the CLI's specific prompt
3. Shim emits a standard event to `events.jsonl`:
   ```jsonl
   {"ts":"...","agent":"coder-1","event":"PreToolUse","status":"pending_approval","tool":"bash","data":{"command":"rm -rf .git"}}
   ```
4. The orchestrator (or guardrail rules in `.aip/block`) evaluates the action
5. Shim injects the response: `tmux send-keys -t anex:coder-1 "y" Enter` or `"n" Enter`

The agent never knows it's being supervised. It thinks a human approved or denied. From the event log, it looks identical to a native `PreToolUse` hook.

**Shim profiles** — per-CLI YAML defining the prompt pattern and response keys:

```yaml
# aip-shim profiles
claude-code:
  tier: native  # uses native hooks, shim not needed

amp:
  tier: intercept
  interactive_intercept:
    prompt_regex: 'Allow this action\? \[Y/n\]'
    approve_keys: 'y\n'
    deny_keys: 'n\n'

aider:
  tier: intercept
  interactive_intercept:
    prompt_regex: 'Run this command\? \(Y/n\):'
    approve_keys: 'y\n'
    deny_keys: 'n\n'

open-interpreter:
  tier: intercept
  interactive_intercept:
    prompt_regex: 'Would you like to run this code\?'
    approve_keys: 'y\n'
    deny_keys: 'n\n'
```

Adding a new CLI to the protocol: write a 5-line YAML profile with the prompt regex. That's it.

#### Tier 3 — MCP Fallback

For the theoretical CLI that has no hooks AND no interactive approval prompts. The agent calls `report_status` as an explicit MCP tool. Highest token cost, last resort.

In practice, Tier 3 may never be needed. Every known CLI agent either has native hooks (Tier 1) or interactive mode (Tier 2).

#### Universal Coverage

| CLI | Tier | Mechanism | Coverage |
|---|---|---|---|
| Claude Code | 1 — Native | Hook scripts | Complete |
| Copilot | 1 — Native | Hook scripts | Complete |
| Gemini | 1 — Native | Hook scripts | Complete |
| Kiro | 1 — Native | Hook scripts | Complete |
| OpenCode | 1 — Native | Plugin events | Complete |
| Codex | 1 — Native | Hook scripts | Complete |
| Amp | 2 — Intercept | `aip-shim` + interactive mode | Complete |
| Aider | 2 — Intercept | `aip-shim` + interactive mode | Complete |
| Open Interpreter | 2 — Intercept | `aip-shim` + interactive mode | Complete |
| Any future CLI | 1 or 2 | Native hooks or shim profile | Add a YAML profile |

**Every CLI agent is now protocol-compliant. The shim eliminates all gaps.**

### MCP Tools (Intentional — Requires Agent Context)

Only install tools an agent actually needs based on its role. A pure worker might need zero. An orchestrator needs the full set.

Every tool call also appends to `workspace/events.jsonl`.

```
export_summary
  - args: content (markdown string), task_id (optional)
  - writes: workspace/summaries/{agent_name}-{timestamp}.md
  - appends: workspace/events.jsonl
  - purpose: concise output for other agents to reference
  - WHO NEEDS IT: any agent that produces output others will read

register_capabilities
  - args: capabilities (list of strings eg ["python", "rust", "testing"]), interests (optional, see Agent Interest Maps)
  - writes: workspace/status/{agent_name}.json (merged with status)
  - appends: workspace/events.jsonl
  - purpose: orchestrator knows who can do what and what each agent cares about
  - WHO NEEDS IT: agents at startup only (can be done by hook handler instead)

request_task
  - args: target_role (optional), task_description, context (optional), priority (optional)
  - writes: workspace/tasks/pending/{task_id}.md
  - appends: workspace/events.jsonl
  - purpose: structured task delegation — agents can also self-serve from pending/
  - WHO NEEDS IT: orchestrators, managers

wait_for
  - args: event_filter (eg "agent:coder,status:finished"), timeout (optional)
  - blocks until: matching event appears in events.jsonl
  - returns: the matching event(s)
  - purpose: LLM yields to process-level waiting — zero tokens burned while idle
  - WHO NEEDS IT: orchestrators, managers waiting on workers

spawn_teammate
  - args: name, cli_type (eg "gemini", "claude-code", "aider"), capabilities, interests (optional), parent_id, depth
  - enforces: depth < max_depth (default 3), parent's children < max_breadth (default 4)
  - creates: new tmux window running the specified CLI
  - writes: workspace/agent_tree.json (adds node)
  - writes: workspace/status/{name}.json (initial registration)
  - appends: workspace/events.jsonl
  - returns: { agent_id, tmux_window, depth, parent_id }
  - purpose: any agent can spawn sub-agents on demand
  - WHO NEEDS IT: orchestrators, managers

notify
  - args: target_agent (or "all"), message, priority (high | medium | low), elicit (optional bool)
  - always: appends to workspace/events.jsonl (permanent record)
  - if priority "high": injects mid-stream via CLI steering (tmux send-keys with CLI-specific template)
  - if elicit=true AND CLI supports MCP elicitation: pops structured dialog forcing agent to respond
  - purpose: direct agent-to-agent communication, from FYI to real-time interrupts to forced decisions
  - WHO NEEDS IT: high-impetus agents (architects, tech leads)
```

### Token Cost by Role

| Role | Hooks | MCP Tools Needed | Tool Context Cost |
|---|---|---|---|
| Pure worker (coder, tester) | Tier 1 or 2 (automatic) | `export_summary` only (or zero) | ~60-100 tokens |
| Reviewer | Tier 1 or 2 (automatic) | `export_summary` + `notify` | ~150-200 tokens |
| Architect | Tier 1 or 2 (automatic) | `export_summary` + `notify` | ~150-200 tokens |
| Manager | Tier 1 or 2 (automatic) | `export_summary` + `request_task` + `wait_for` + `spawn_teammate` | ~350-400 tokens |
| Orchestrator | Tier 1 or 2 (automatic) | Full set | ~500-600 tokens |

**No agent needs `report_status` or `report_progress` as MCP tools.** Hooks (native or shim) handle all status reporting automatically. Every worker agent carries ≤100 tokens of tool overhead regardless of which CLI it runs.

**Compare to "every agent gets all tools": 500-800 tokens × 20 agents = 10,000-16,000 tokens wasted. With hooks + selective MCP, 15 of those 20 agents carry under 100 tokens of tool definitions.**

### How `notify` Works

Dual-mode delivery. Every notify appends to the event log (the permanent record). But for high-priority messages to CLIs that support mid-stream injection, the tool also injects the message directly into the agent's running session:

Delivery depends on priority and CLI capability:

**High priority + injection support** — the message arrives in the agent's context *while it's thinking*. The agent factors it in on its very next reasoning step. This is critical for messages like "don't edit file X or you'll create a race condition" — the coder needs that information before it finishes, not after.

Confirmed CLI steering support:

| CLI | Template | Mechanism | Behaviour |
|---|---|---|---|
| claude-code | `/btw {message}` | Slash command | Fork-and-merge — async, non-disruptive. Separate instance processes the message and merges back when main agent is ready |
| codex | `{message}` | Text + Enter | Inline injection — synchronous, injects into current turn |
| copilot | `{message}` | Text + Enter | Inline injection — sends at next gap in thinking/tool use |
| gemini | `{message}` | Text + Enter | Inline injection — model steering hint (experimental) |

All four major CLIs support mid-stream injection. Claude Code's fork-and-merge is the safest — `/btw` is always safe to send regardless of agent state. For inline injection CLIs (Codex, Copilot, Gemini), the anex-mcp server should check for output settling (300ms no new output) before injecting to avoid corrupting mid-tool-call state.

CLIs that also support **MCP elicitation** (Claude Code, Codex, Cursor) can go further — the MCP server pops a structured dialog that forces the agent to make a decision before continuing. Use this for critical decision points, not just FYI messages.

**High priority + no injection** — for any CLI without steering support, agent reads the notification at its next event log check, which happens frequently because its interest map marks notify as "high."

**Medium/low priority** — event log only, regardless of CLI support. The agent reads it at its own pace.

Example: preventing a mistake in real time:

```jsonl
{"ts":"...","agent":"architect","event":"notify","target":"coder","priority":"high","message":"DO NOT edit auth.py — db-architect is refactoring the session model, you'll create a race condition. Use auth_v2.py instead."}
```

If the coder is Claude Code, this becomes:
```bash
tmux send-keys -t anex:coder "/btw architect says: DO NOT edit auth.py — db-architect is refactoring the session model, you'll create a race condition. Use auth_v2.py instead." Enter
```

The coder sees this mid-stream and avoids the mistake entirely. Without injection, the coder finishes, creates the race condition, the reviewer catches it, the coder undoes and redoes. Wasted tokens, wasted time.

Example: two architects collaborating in real time:

```jsonl
{"ts":"...","agent":"api-architect","event":"notify","target":"db-architect","priority":"high","message":"auth flow needs a session table — affects your schema design"}
{"ts":"...","agent":"db-architect","event":"notify","target":"api-architect","priority":"high","message":"added sessions table, FK to users — your endpoint can assume session_id exists"}
```

Both architects support injection, so these arrive mid-thought. They're having a real conversation while both actively working. Low-impetus agents (coders, testers) never see these messages because their interest maps filter them out.

### How `wait_for` Works

This is the critical cost-saving tool. When an agent calls `wait_for`, the MCP server process watches `events.jsonl` using filesystem notifications (`inotifywait`, `fswatch`, or `tail -f`). The LLM is completely idle — no polling, no token burn. The moment a matching event appears, the tool returns and the LLM resumes thinking.

This is the same pattern Claude Code's teammate system uses internally: the runtime waits at the process level (free), and only invokes the LLM when there's something to act on.

Orchestrator flow becomes:

1. Send task to coder
2. Call `wait_for agent:coder,status:finished` — LLM yields, zero cost
3. Coder works... minutes pass... zero tokens burning
4. Coder finishes, calls `report_status finished` → event hits `events.jsonl`
5. `wait_for` returns → orchestrator LLM wakes up
6. Read summary, decide next step

Multiple waits can run in parallel: "wait for coder OR reviewer to finish" by passing multiple filters. The tool returns whichever matches first.

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

### That's it. Hooks (3-tier) for all automatic reporting, 5 MCP tools for intentional coordination. Most agents only need 0-2 of them.

### Recursive Spawning

`spawn_teammate` turns Agent-Nexus from a flat orchestrator into a self-organising agent tree. Any agent can spawn sub-agents, and those sub-agents can spawn their own. The task structure dictates the team structure — no pre-configuration needed.

**Limits prevent explosion:**
- **max_depth: 3** — orchestrator (0) → managers (1) → specialists (2) → workers (3)
- **max_breadth: 4** — each agent spawns at most 4 children
- **Theoretical max: 85 agents** (1 + 4 + 16 + 64), but typical is 5-30

**The parent responsibility pattern:**
1. Agent decides it needs help → calls `spawn_teammate`
2. Assigns work to children via `request_task`
3. Calls `wait_for` on each child (zero token burn while waiting)
4. Reads children's summaries when they finish
5. Aggregates results into its own summary
6. Terminates children (kills their tmux windows)
7. Reports to its own parent via `export_summary`

Each agent is responsible for its own children. Cleanup cascades naturally — when a parent terminates its children, those children terminate their children first.

**Agent Tree** (`workspace/agent_tree.json`):

```json
{
  "orchestrator": {
    "depth": 0, "parent": null,
    "children": ["review-mgr", "impl-mgr"],
    "tmux_window": "anex:0"
  },
  "review-mgr": {
    "depth": 1, "parent": "orchestrator",
    "children": ["security-rev", "perf-rev"],
    "tmux_window": "anex:review-mgr"
  },
  "security-rev": {
    "depth": 2, "parent": "review-mgr",
    "children": ["cred-checker"],
    "tmux_window": "anex:security-rev"
  },
  "cred-checker": {
    "depth": 3, "parent": "security-rev",
    "children": [],
    "tmux_window": "anex:cred-checker"
  }
}
```

**Emergent topologies** — the same system produces different team shapes based on task complexity:

```
Simple task:     orchestrator → worker                              (2 agents)
Medium task:     orchestrator → manager → 3 workers                 (5 agents)
Complex task:    orchestrator → 3 managers → specialists → workers  (20-30 agents)
```

No configuration change. The orchestrator's prompt and the task's complexity determine how many agents get spawned. Simple tasks stay flat. Complex tasks grow a deep tree. The system scales by intent, not by infrastructure.

**Resource efficiency** — agents are spawned on demand and terminated when their task is done. A complex review might peak at 30 agents for 5 minutes, then drop back to 1. Compare this to pre-spawning a static pool of 100 agents that sit idle most of the time.

### Why Not Claude Code's TeammateTool?

Claude Code has a native teammate system with `sendMessage` and `readMessages`. Agent-Nexus's anex-mcp replaces it entirely:

- **TeammateTool is Claude-only**. anex-mcp works on any CLI agent.
- **TeammateTool is hub-and-spoke**. anex-mcp supports recursive trees.
- **TeammateTool has no task queue**. anex-mcp has atomic claiming.
- **TeammateTool has no interest maps**. anex-mcp has impetus-driven subscriptions.
- **TeammateTool has no event log**. anex-mcp has full audit trail.
- **TeammateTool has no direct peer messaging**. anex-mcp has `notify` for agent-to-agent collaboration.
- **TeammateTool costs every agent context tokens**. Agent-nexus uses hooks for automatic reporting — workers carry zero tool overhead.

If an agent happens to be Claude Code, it uses anex-mcp — not the native TeammateTool. One protocol for all agents.

### Why Not ACP?

ACP is a spec that everyone implements differently. You end up writing adapters per vendor — the exact same normalisation problem as parsing different CLI outputs, just moved from terminal formatting to protocol implementation.

Agent-Nexus sidesteps this entirely. You wrote the MCP server once, you install it everywhere, it behaves identically on every CLI. The agents don't need to know they're participating in a multi-agent system. They just have tools called `report_status` and `export_summary` and they use them naturally.

If ACP ever stabilises, your MCP tools can emit ACP-compatible events as a translation layer. But you're not blocked waiting.

### Why Not SSE?

SSE exists to stream events from a server to a client over HTTP. In Agent-Nexus, live output is the tmux pane buffer — already streaming, already there. SSE is a transport layer you've made redundant.

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
blocked_by: [task-040, task-041]
```

**Task dependencies** — the optional `blocked_by` field lists task IDs that must complete before this task becomes claimable. Tasks with unresolved dependencies stay in `pending/` but agents skip them. When a blocking task moves to `done/`, the orchestrator (or any agent checking the queue) evaluates whether blocked tasks are now unblocked. No new tool needed — the queue logic just checks `blocked_by` against `done/` before allowing a claim.

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

IDE agents (Cursor, Windsurf, Cline, Copilot) become full team members by running their terminal work inside the agent-nexus tmux session. One instruction: "use `tmux attach -t anex:frontend` for your terminal."

Now the IDE agent shows up in `tmux list-windows`, the orchestrator reads its pane like any other agent, and it calls the same MCP tools. From the orchestrator's perspective, it's just another agent — it doesn't know or care that there's a GUI attached.

The human developer in the IDE is also just an agent. Read your task from `workspace/tasks/`, do the work, call `report_status` when done. The orchestrator moves on. No special approval workflow — you're just a slow agent with good taste.

### VSIX Extension (Planned)

Cursor, Windsurf, Cline, Copilot — they're all VS Code or VS Code extensions. One VSIX plugin works across all of them as the agent-nexus control interface inside every IDE simultaneously.

**What the extension does:**
- Attaches the IDE's integrated terminal to the tmux session automatically on workspace open
- Installs the agent-nexus MCP server on whatever agent the IDE is running
- Registers the IDE agent as a teammate with capabilities and interest map on startup
- Shows the dashboard as a VS Code panel — live pane streams, event log, agent tree
- Provides a sidebar for reading/writing `workspace/tasks/` and `workspace/status/`
- Gives the human developer claim/complete/export buttons — MCP tools wrapped in a GUI

The IDE agent doesn't need to know about agent-nexus. The extension handles integration transparently. The agent uses its MCP tools normally and doesn't know it joined a team.

This also cleanly solves the "human as agent" problem. You're already in the IDE. The extension gives you a task board, status controls, and summary export without running MCP tools manually.

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
tmux new-window -t anex -n github-watcher "./watch-prs.sh"
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
tmux new-window -t anex -n coder "claude-code"
# or
tmux new-window -t anex -n coder "gemini --resume abc123"
```

### Communicate (live)
```bash
# orchestrator reads coder's latest output
tmux capture-pane -pt anex:coder

# orchestrator sends task to coder
tmux send-keys -t anex:coder "implement the auth module" Enter
```

### Communicate (async)
Agents read/write workspace files. Summaries from dead agents persist. Status files show who's doing what.

### Pause
Just leave it. Idle CLI agents use minimal resources (~150-300MB RAM). The tmux window costs almost nothing (~200KB for default scrollback). Mark status as idle.

### Resume
Every CLI supports native resume. The agent handles its own session persistence — you never need to serialise or restore context externally.
```bash
# agent already running in pane — just send new input
tmux send-keys -t anex:coder "now add tests" Enter

# agent was killed — respawn with native resume
tmux new-window -t anex -n coder "gemini --resume session_abc123"
tmux new-window -t anex -n coder "claude-code --resume"
```

### Shutdown
```bash
# agent exports its own summary via MCP tool (or orchestrator tells it to)
# most CLIs also support /export or equivalent natively
tmux send-keys -t anex:coder "/export" Enter
# wait for file
tmux kill-window -t anex:coder
```

Summary file remains in workspace for other agents to reference. Native session ID is tracked so the agent can be resumed later.

## Orchestrator Behaviour

The orchestrator is just another CLI agent in window 0. No special framework. Its loop:

1. Receive or decide on next task
2. Check `workspace/status/` — who's available and capable?
3. Check `tmux list-windows` — who's actually running?
4. If needed agent exists and is idle → send task to pane
5. If needed agent doesn't exist → spawn in new window
6. Call `wait_for` — **LLM yields, zero tokens burn while agents work**
7. `wait_for` returns → read summary from the finished agent
8. Decide next step, repeat

The key is step 6. The orchestrator doesn't poll the event log in a loop. It calls `wait_for` and the MCP server handles the waiting at the process level. The LLM is completely idle until an agent finishes. This is the same pattern Claude Code's teammate system uses internally.

**Critical token rule**: the orchestrator never pastes another agent's output into a task. It references files. Instead of "here's what the architect wrote: [500 tokens]", it sends "implement the auth module per the design in `workspace/summaries/architect-0317.md`". The agent reads the file directly. The orchestrator's context stays clean and small.

The orchestrator can be ANY CLI agent. Claude, Gemini, Kimi, aider. Swap mid-session if needed — the new orchestrator reads workspace state and continues.

## Agent Interest Maps (Impetus System)

Interest maps are the impetus mechanism — they define not just what an agent passively reads, but who it actively talks to. High-impetus agents (architects, tech leads) use `notify` to communicate directly with each other mid-task. Low-impetus agents (coders, testers) stay heads-down on their work.

The interest map determines two things:
1. **What an agent reads** from the event log (passive awareness)
2. **Who an agent talks to** via `notify` (active collaboration)

An architect with high interest in other architects will both read their summaries AND send them direct messages during work. A coder with no interest in other coders will do neither.

### How It Works

Each agent has an `interests` field in its status file (`workspace/status/{agent}.json`), registered via the `register_capabilities` MCP tool on startup:

```json
{
  "agent": "architect",
  "status": "idle",
  "capabilities": ["system-design", "api-design", "code-review"],
  "interests": {
    "agents": {
      "architect-*": "high",
      "coder": "high",
      "reviewer": "high",
      "researcher": "medium",
      "tester": "low"
    },
    "events": {
      "notify": "high",
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

**high** — active collaboration. Agent checks these between subtasks, responds to `notify` messages, may interrupt current work. Two architects with high interest in each other will have a running dialogue via `notify` throughout a task.

**medium** — passive awareness. Check at natural breakpoints — after finishing a subtask, before starting the next one. Read summaries but don't initiate conversation.

**low** — background only. Get to it eventually. Don't spend tokens polling. Don't `notify` these agents unless critical.

### Example Interest Maps by Role

**Architect** — high impetus. Actively collaborates with other architects via `notify`. Reads coder output to verify design adherence. Responds to failures and blocks immediately.

```json
{
  "agents": { "architect-*": "high", "coder": "high", "reviewer": "high", "researcher": "medium" },
  "events": { "notify": "high", "status:failed": "high", "status:blocked": "high", "status:finished": "medium" },
  "summaries": { "architect-*": "high", "coder-*": "medium" }
}
```

**Coder** — low impetus. Heads-down on its task. Only reads architect designs. Doesn't initiate conversation. Everything else is someone else's problem.

```json
{
  "agents": { "architect": "high" },
  "events": { "notify": "medium", "status:blocked": "low" },
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

Interest maps and impetus level make coordination cost predictable before you spawn an agent:

| Role | Impetus | Coordination overhead |
|---|---|---|
| Pure worker | none | ~0 tokens (no reads, no notify) |
| Coder | low | ~150 tokens (architect summary only) |
| Reviewer | medium | ~250 tokens (coder + architect summaries) |
| Tester | medium | ~200 tokens (coder + reviewer summaries) |
| Architect | high | ~500-700 tokens (reads broadly, sends/receives notify) |
| Orchestrator | n/a | ~100 tokens (event log tail + file references) |

High-impetus agents burn more tokens on coordination — but they're the roles where collaboration matters most. Low-impetus agents stay cheap.

Compare this to "every agent reads every pane" which could easily be 5000+ tokens per cycle.

## Adding a New Agent Type

1. Install the agent CLI
2. Add a shim profile (5-line YAML with the CLI's approval prompt regex) — or use native hooks if supported
3. Install selective MCP tools based on the agent's role (workers may need zero)
4. `tmux new-window -t anex -n name "agent-cli"`
5. Done

No adapter code. No parser. No protocol integration. The shim or native hooks handle status automatically. MCP tools handle intentional coordination. tmux gives connectivity to every other agent.

## What This Replaces

| Traditional approach | Agent-Nexus equivalent |
|---|---|
| ACP protocol | Hooks (3-tier: native + shim + fallback) + 5 selective MCP tools |
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
| Blocking/callback mechanism | `wait_for` tool (process-level wait, zero token burn) |
| Sub-agent / sub-task spawning | `spawn_teammate` with depth/breadth limits |
| Claude Code TeammateTool | anex-mcp (vendor-neutral, richer semantics) |
| Agent-to-agent messaging | `notify` tool + interest maps (impetus-driven) |
| Task dependency graph | `blocked_by` field in task files |
| Terminal output rendering | @xterm/headless (clean text, no ANSI escapes) |
| Polling for changes | Incremental cursor-based pane reads |
| Agent status reporting (manual) | CLI hooks (3-tier: native, shim intercept, MCP fallback) |

## Key Principles

**Hooks first, MCP tools second** — if it can be automatic, use hooks. Reserve MCP tools for actions requiring agent judgment. Workers should carry zero tool overhead.

**Agents format their own output** — push responsibility inward via MCP tools or CLI hooks, don't parse from outside.

**LLMs are the parser** — an agent reading another agent's raw output understands it natively. The protocol is English.

**Everything is files and panes** — if it's alive, read the pane. If it's dead, read the file. Same interface either way.

**Don't manage what manages itself** — CLIs handle their own session persistence, their own resume, their own context. Let them.

**Spawn freely, kill only under pressure** — idle agents are cheap. Their loaded context is valuable. Don't optimise prematurely.

**LLMs build adapters in minutes** — the "hard problem" of parsing diverse CLI outputs isn't hard when the parser is an LLM that can reverse-engineer any output format in seconds. Don't design around human development speed.

**Vendor neutral by default** — built on primitives (tmux, files, SSH) that predate every AI tool. Works with anything that has a CLI.

**Entirely open stack** — every layer is built on open standards. tmux (GPL), MCP (open protocol), VSIX (open extension format), POSIX filesystem, SSH, git. Nothing proprietary at any level. Anyone can implement any piece, swap any component, or fork the whole thing.

## Token Efficiency

The whole point is to minimise token burn. Agents should almost never read raw pane output. Enforce this read hierarchy:

| Level | What | Cost | When |
|---|---|---|---|
| 1 | `tail -n 20 workspace/events.jsonl` | ~50 tokens | Always check first — what happened? |
| 2 | `cat workspace/status/coder.json` | ~20 tokens | Who's doing what right now? |
| 3 | `cat workspace/summaries/coder-0317.md` | ~100 tokens | What did they produce? |
| 4 | Incremental pane read (cursor-based, new lines only) | ~20 tokens | What's changed since I last looked? |
| 5 | `tmux capture-pane -pt anex:coder -S -5` | ~30 tokens | Quick peek at live output (last 5 lines) |
| 6 | `tmux capture-pane -pt anex:coder -S -20` | ~100 tokens | Need more context — expand progressively |
| 7 | Full pane read | ~1000+ tokens | Almost never needed |

### Progressive Reverse Reading

`tmux capture-pane` supports line ranges. Always read from the bottom up:

```bash
# last 5 lines — usually enough
tmux capture-pane -pt anex:coder -S -5

# need more? last 20
tmux capture-pane -pt anex:coder -S -20

# need more? last 50, but stop at the marker
tmux capture-pane -pt anex:coder -S -50
```

Combined with the marker system, the agent reads backwards until it hits the marker and stops. It never reads thinking tokens. The output section might be 10 lines while the thinking was 200 — progressive reverse reading means you only pay for the 10.

### Incremental Reads

For agents that periodically monitor another agent's pane (high-impetus architects watching coders), cursor-based incremental reads avoid re-reading old output. The anex-mcp server tracks "agent A last read line 47 of agent B's pane" and next time returns only lines 48+. Cost drops from "entire buffer" to "only what's new since last check" — often just a few lines, maybe 20 tokens.

This is especially valuable for the dashboard and for high-impetus agents that check panes frequently. Without incremental reads, every check re-reads the entire visible buffer. With them, repeated checks are nearly free.

### The Rule

Instruct every agent: **"Check events.jsonl first. Read summaries second. Only read panes when the structured data isn't enough, and read from the bottom up."**

The hooks + selective MCP approach is the token-efficient layer. Hooks handle what's automatic, MCP handles what's intentional, and most agents need very few of either.

## ACP/A2A Compatibility

Agent-Nexus doesn't depend on ACP or A2A, but the MCP tools map cleanly to both:

| Agent-Nexus mechanism | ACP equivalent | A2A equivalent |
|---|---|---|
| Hooks: status events | Task status events | Task status updates |
| Hooks: tool events | Progress events | Progress updates |
| `register_capabilities` | Agent description | Agent Card |
| `request_task` | Task delegation | Task assignment |
| `export_summary` | Task artifact | Task artifact |
| `wait_for` | Task completion callback | Task status subscription |
| `spawn_teammate` | Sub-task delegation | Child task creation |
| `notify` | Agent messaging | Agent-to-agent communication |

If the ecosystem converges, add a flag: `ANEX_ACP_COMPAT=true`. The MCP tools then also emit ACP-formatted events alongside the file writes. Compatibility without coupling — build it later if needed.

## Implementation Notes

**ANSI stripping**: CLI tools output heavy escape sequences — colours, spinners, cursor movement, progress bars. Three approaches in order of quality:
1. **`tmux capture-pane` without `-e`** — tmux strips escapes by default when `-e` is omitted. Simplest, handles most cases.
2. **`sed` / `ansifilter`** — regex-based stripping. Handles colour codes but breaks on cursor repositioning and progress bar overwrites.
3. **`@xterm/headless`** — the VS Code terminal renderer without a display. Processes all escape sequences server-side and outputs exactly what a human would see as clean text. Handles cursor positioning, `\r` overwrites, progress bars, TUI rendering. This is what mcp-interactive-terminal and Forge terminal MCP use. Best quality, recommended for production.

**Incremental pane reads**: instead of `tmux capture-pane` returning the whole buffer every time, track a cursor position (line number) per consumer. Next read starts from the cursor. The anex-mcp server can maintain cursors per agent in memory — when agent A reads agent B's pane, the server remembers "agent A last read line 47 of agent B's pane" and next time returns only lines 48+. Massive token savings for agents that monitor panes periodically. Falls naturally out of `tmux capture-pane -S {cursor} -E -1`.

**Atomic file writes**: the MCP server should write status files via write-to-tmp-then-rename, not direct writes. This ensures the orchestrator never reads a half-written JSON object. Example: write to `workspace/status/coder.json.tmp`, then `mv` to `workspace/status/coder.json`. Rename is atomic on all Unix filesystems.

**Event log growth**: `events.jsonl` grows indefinitely. The orchestrator should always read the tail, not the whole file — `tail -n 50 workspace/events.jsonl` gives recent state without burning tokens. Optionally rotate or truncate on session restart.

**Notify injection safety**: before injecting a `/btw` or steering prompt via `tmux send-keys`, check whether the target agent is in a state that can accept input. Some CLIs behave unpredictably if you inject text while they're mid-tool-call or rendering output. For Claude Code this is a non-issue (`/btw` fork-and-merges asynchronously). For Codex/Copilot/Gemini (inline injection), hold the message briefly if the pane shows active tool execution, and deliver at the next gap. Output settling (300ms no new output) is a reasonable signal that the agent is receptive.

## Phase 1: Test Plan

### Step 1 — Basic tmux orchestration
- Start a tmux server named `anex`
- Spawn two CLI agents in named windows
- Orchestrator in window 0 reads the other panes
- Verify agents can see each other's output

### Step 2 — Native hooks (Tier 1)
- Install hook handler on a CLI with native hooks (Claude Code, Gemini, Copilot)
- Verify lifecycle events auto-append to `events.jsonl` without agent calling any tools
- Verify `workspace/status/{agent}.json` updates automatically on status changes
- Confirm agent has zero awareness of the coordination system

### Step 3 — Interactive intercept (Tier 2 — `aip-shim`)
- Launch a hookless CLI in interactive mode (Aider or Amp without auto-approve)
- Start `aip-shim` watching the pane with the CLI's YAML profile
- Trigger an approval prompt, verify shim emits `PreToolUse` event to `events.jsonl`
- Verify shim injects `y` or `n` based on orchestrator/guardrail decision
- Confirm event format is identical to Tier 1 native hooks

### Step 4 — Selective MCP tools
- Install `export_summary` only on a worker agent
- Install full tool set on orchestrator
- Verify worker can export but doesn't carry unused tool overhead
- Verify orchestrator can spawn, delegate, wait, and notify

### Step 5 — Task delegation
- Orchestrator sends a coding task to agent in window 1
- Agent completes, exports summary
- Orchestrator reads summary, sends review task to agent in window 2
- Reviewer reads coder's summary, produces review
- Full cycle with no manual intervention

### Step 6 — Lifecycle management
- Test killing and respawning agents with native resume
- Test orchestrator crash recovery (reconnect to existing tmux)
- Test adding agents mid-session
- Test swapping orchestrator to a different CLI agent

### Step 7 — Recursive spawning
- Agent in window 1 calls `spawn_teammate` to create a sub-agent
- Verify sub-agent appears in `tmux list-windows` and `agent_tree.json`
- Verify depth and breadth limits are enforced (depth 3 rejected, 5th child rejected)
- Sub-agent completes, parent reads summary, terminates child
- Verify cleanup cascades: parent termination kills children first
- Test a 3-level deep tree with the 50-file PR review pattern

### Step 8 — Task queue
- Place tasks in `workspace/tasks/pending/`
- Verify agents can claim tasks via atomic `mv`
- Test crash recovery: kill an agent mid-task, verify lease expires and task is reclaimable
- Test agent self-service: idle agent polls `pending/` and grabs work without orchestrator
- Test `blocked_by`: create task-B blocked by task-A, verify task-B is skipped until task-A reaches `done/`

### Step 9 — Fault tolerance
- Trigger a quota error on one model
- Verify orchestrator reads the error and spawns a fallback agent
- Kill orchestrator, reconnect new orchestrator, verify it picks up state from `events.jsonl`

### Step 10 — Remote agents
- Repeat steps 1-3 with one agent on a remote machine via SSH
- Verify `ssh box "tmux capture-pane -pt anex:coder"` works transparently

### Step 11 — Cloud agents (optional)
- Push workspace to a GitHub repo
- Simulate a cloud agent committing a summary
- Verify the watcher pane picks up the commit and appends to `events.jsonl`

## Dashboard (Human Observer)

`anex dashboard` — a live multiplexed view of every agent working in real time. The primary view is a split-pane grid showing actual tmux pane output: streaming text, thinking, tool calls, spinning progress indicators, errors. Eight agents churning away simultaneously in an 8-way split.

This is a wall of monitors for AI agents. You see exactly what each agent is doing right now — the raw terminal output, not a sanitised summary. You catch rate limits the moment they appear, spot an agent going off-track before it wastes tokens, watch the orchestrator delegate and workers pick up tasks.

**Primary view — live agent output:**
- N-way split of active tmux panes (auto-scales to agent count)
- Real terminal streams: thinking, tool calls, spinners, output — everything the agent sees
- Colour-coded borders per agent role or status (working/idle/blocked/failed)
- Auto-follows the most recently active panes

**Secondary panel — workspace summary:**
- Event log tail (scrolling feed of status changes, notifications, exports)
- Agent tree with status indicators
- Task queue summary (pending/claimed/done counts)
- Error/rate-limit highlighting (spot 429s and quota hits immediately)

**What it is NOT:**
- Not a control interface — you don't assign tasks or manage agents from here
- Not required — the system works identically with or without the dashboard running
- Not an agent — it doesn't consume tokens, write to the workspace, or participate in coordination

Porting from existing cli-agent-nexus dashboard work. Second-order priority — the core system works without it.

## Platform Notes

- **Linux/Mac**: works natively
- **Windows**: WSL (tmux works perfectly), native Windows is phase 2
- **Containers**: install tmux in image, or `docker exec` into container
- **Remote**: SSH wraps everything transparently
- **IDE agents**: VSIX extension auto-integrates any VS Code-based IDE (Cursor, Windsurf, Cline, Copilot)
- **Cloud agents**: git push/pull workspace, watcher pane bridges events back to local log

## Related Projects (Separate Scope)

Agent-nexus teammates are the heavy delegation tier. Two companion tools provide lighter tiers:

**Universal CLI MCP** (in progress) — any agent can call any CLI headlessly as a one-shot function. `cli_oneshot("gemini", "lint this code", files=["main.py"])` spawns a process, gets the result, terminates immediately. Zero persistence, zero RAM overhead. Any teammate can use this as an MCP tool for quick cross-model calls.

**CLI-Agnostic Sub-Agent Wrapper** (planned) — normalises native sub-agent APIs across CLIs via start hooks that route subagent calls through tmux panes. Uses named pipes (FIFOs) for streaming responses back to the parent CLI without SSE or HTTP. Falls back to one-shot calls where native subagents aren't available. See `subagent-routing-summary.md` for full design.

Together with teammates, these form a three-tier delegation hierarchy: heavy (persistent teammates), medium (CLI sub-agents via tmux routing), light (one-shot calls). Right tool for the right job.
