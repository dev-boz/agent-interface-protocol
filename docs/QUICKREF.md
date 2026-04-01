# Agent Interface Protocol (AIP) Quick Reference Card

## Essential Commands

### Session Management
```bash
aip init --ensure-session              # Initialize workspace + tmux session
tmux attach -t aip                     # Watch agents in real-time
tmux detach                              # (or Ctrl-b d) Exit without killing
```

### Agent Operations
```bash
aip agent spawn <name> <command>       # Spawn new agent
aip agent list                         # List all agents
aip agent capture <name> --lines 5     # Read agent output (last 5 lines)
aip agent send <name> "text"           # Send command to agent
aip agent kill <name>                  # Kill agent
```

### Task Queue
```bash
aip task list                          # List pending tasks
aip task list --stage claimed          # List claimed tasks
aip task list --stage done             # List completed tasks
aip task list --claimable              # List pending tasks with all blocked_by deps met
aip task claim <id> <agent>            # Claim a task
aip task complete <id>                 # Mark task done
aip task reclaim-expired               # Reclaim expired tasks
```

### Hook Integration
```bash
aip hook print-config --cli gemini --agent-name coder --tool-profile worker
aip hook install --cli gemini --agent-name coder --tool-profile worker --config-root /repo
aip hook install --cli kiro --agent-name reviewer --tool-profile reviewer --config-root /repo
aip hook install --cli codex --agent-name architect --tool-profile architect --config-root /repo
aip hook install --cli cursor --agent-name editor --tool-profile worker --config-root /repo
aip hook install --cli qwen --agent-name analyst --tool-profile worker --config-root /repo
```

### Shim Commands (Tier 2 CLIs)
```bash
aip shim watch --agent-name vibe-worker --cli vibe    # Start aip-shim lifecycle watcher
aip shim watch --agent-name amp-worker --cli amp      # Works for any Tier 2 CLI
aip shim check --agent-name vibe-worker               # Check shim status
aip shim list-profiles                                # List available shim profiles
```

### Monitoring
```bash
tail -f workspace/events.jsonl | jq      # Watch event stream
cat workspace/status/<agent>.json        # Check agent status
ls workspace/summaries/                  # List agent outputs
```

---

## MCP Tools (For Agents)

Agents with `aip-mcp` installed get a profile-specific subset of these tools:

### report_status
```json
{
  "status": "working|idle|blocked|failed|finished",
  "message": "optional context"
}
```
Fallback tool for hookless CLIs.

### export_summary
```json
{
  "content": "## Summary\n\nMarkdown content here",
  "task_id": "optional-task-id"
}
```

### register_capabilities
```json
{
  "capabilities": ["python", "testing", "review"],
  "interests": {
    "agents": {"coder": "high"},
    "events": {"status:failed": "high"},
    "summaries": {"architect-*": "medium"}
  }
}
```

### request_task
```json
{
  "task_description": "what needs to be done",
  "target_role": "coder",
  "priority": "high|normal|low",
  "context": "reference to files, not pasted content",
  "blocked_by": ["task-id-1", "task-id-2"]
}
```

### report_progress
```json
{
  "progress": "3 of 5 files done",
  "percentage": 60
}
```
Fallback tool for hookless CLIs.

### wait_for
```json
{
  "event_filter": "agent:coder,status:finished",
  "timeout": 30
}
```

### spawn_teammate
```json
{
  "name": "reviewer",
  "cli_type": "gemini",
  "capabilities": ["review", "testing"]
}
```

### notify
```json
{
  "target_agent": "coder",
  "message": "hold off on auth.py edits",
  "priority": "high",
  "elicit": true
}
```
When `elicit` is `true`, uses MCP elicitation for interactive delivery (supported: claude-code, codex, cursor, qwen).

### Common Profiles

| Profile | Use |
|---|---|
| `worker` | Hook-capable workers |
| `worker-hookless` | Workers on Amp, Aider, or other hookless CLIs |
| `reviewer` / `architect` | Advisory agents |
| `manager` | Delegation-heavy coordinators |
| `full` / `orchestrator` | Broad control surface |

---

## Workspace Layout

```
workspace/
├── events.jsonl          # Append-only event log (orchestrator's dashboard)
├── status/               # Agent status files (JSON, merge semantics)
│   ├── coder.json
│   └── reviewer.json
├── summaries/            # Agent output summaries (markdown)
│   ├── coder-0318-1423.md
│   └── reviewer-0318-1425.md
└── tasks/                # Task queue (atomic claiming via mv)
    ├── pending/          # Unclaimed tasks
    ├── claimed/          # In-progress (agent-name prefixed)
    ├── done/             # Completed
    └── failed/           # Move back to pending/ to retry
```

---

## Read Hierarchy (Token Efficiency)

Always check in this order:

| Level | Command | Cost | When |
|-------|---------|------|------|
| 1 | `tail -n 20 events.jsonl` | ~50 tokens | What happened? |
| 2 | `cat status/coder.json` | ~20 tokens | Who's doing what? |
| 3 | `cat summaries/coder-*.md` | ~100 tokens | What did they produce? |
| 4 | `capture-pane -S -5` | ~30 tokens | Quick peek |
| 5 | `capture-pane -S -20` | ~100 tokens | More context |
| 6 | Full pane read | ~1000+ tokens | Almost never needed |

---

## Orchestrator Pattern

```python
# 1. Read event log — what changed?
tail -n 50 workspace/events.jsonl

# 2. Check status files — who's available?
cat workspace/status/*.json

# 3. If work is done, read summary
cat workspace/summaries/coder-latest.md

# 4. Delegate next task with file reference (not pasted content)
# Use request_task MCP tool:
{
  "task_description": "review the auth module",
  "target_role": "reviewer",
  "context": "See workspace/summaries/coder-0318-1423.md"
}

# 5. Repeat
```

**Key rule**: Reference files, don't paste content (30 tokens vs 500+)

---

## Task Lifecycle

```
pending/task-042.md
    ↓ (agent claims via atomic mv — blocked_by deps must be done first)
claimed/coder-task-042.md
    ↓ (agent completes)
done/task-042.md
    ↓ (or fails)
failed/task-042.md
    ↓ (move back to retry)
pending/task-042.md
```

### Task File Format
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

Tasks with `blocked_by` remain unclaimable until all listed task IDs reach `done`. Use `aip task list --claimable` to see only tasks with satisfied dependencies.

---

## Fault Tolerance

### Agent Crash
- Task stays in `claimed/` with lease
- After expiry → `aip task reclaim-expired`
- Last summary persists in workspace

### Orchestrator Crash
- tmux session survives
- All agents keep running
- New orchestrator: read `events.jsonl` and continue

### Model Fallback
- Orchestrator reads errors in plain English
- Spawns replacement on different model
- Redirects task

---

## Common Patterns

### Spawn Multiple Agents
```bash
for role in coder reviewer tester; do
  aip agent spawn $role "gemini"
done
```

### Monitor All Agents
```bash
aip agent list | jq -r '.[].name' | while read agent; do
  echo "=== $agent ==="
  cat workspace/status/$agent.json 2>/dev/null || echo "No status"
done
```

### Reclaim Stale Tasks
```bash
# Run periodically (e.g., every 5 minutes)
aip task reclaim-expired
```

### Export Agent Output Before Kill
```bash
aip agent send coder "/export"
sleep 2
aip agent kill coder
```

---

## Debugging

### Watch Event Stream
```bash
tail -f workspace/events.jsonl | jq -r '"[\(.ts | split("T")[1] | split(".")[0])] \(.agent): \(.event)"'
```

### Check Agent Output
```bash
aip agent capture <name> --lines 20
```

### Attach to tmux Session
```bash
tmux attach -t aip
# Navigate: Ctrl-b n (next window), Ctrl-b p (previous window)
# Detach: Ctrl-b d
```

### Inspect Task Files
```bash
cat workspace/tasks/claimed/coder-task-042.md
```

---

## Performance Tips

1. **Use file references in tasks** — Don't paste 500-token summaries, reference the file (30 tokens)
2. **Read event log first** — Cheaper than reading all status files
3. **Use interest maps** — Agents only read what they care about
4. **Keep pane reads minimal** — Event log + status files usually enough
5. **Let agents idle** — Idle agents use ~150MB RAM, their context is valuable

---

## Installation

```bash
cd /path/to/AIP
pip install -e .          # installs aip and aip-mcp commands
pip install -e '.[dev]'   # also installs pytest for development
```

---

## Testing

```bash
./run_tests.sh            # Run all tests
python -m pytest -v       # Run unit tests only
./demo.sh                 # Interactive demo
./quickstart.sh           # 5-minute setup guide
```

---

## Integration Configuration

Preferred path for supported CLIs:

```bash
aip hook install --cli gemini --agent-name coder --tool-profile worker --config-root /repo
aip hook install --cli kiro --agent-name reviewer --tool-profile reviewer --config-root /repo
aip hook install --cli codex --agent-name architect --tool-profile architect --config-root /repo
aip hook install --cli cursor --agent-name editor --tool-profile worker --config-root /repo
aip hook install --cli qwen --agent-name analyst --tool-profile worker --config-root /repo
```

For Tier 2 CLIs (Vibe, Amp), use the shim watcher:

```bash
aip shim watch --agent-name vibe-worker --cli vibe
aip shim watch --agent-name amp-worker --cli amp
```

For MCP-only CLIs, add `aip-mcp` manually:

```json
{
  "mcpServers": {
    "aip": {
      "command": "aip-mcp",
      "args": ["--workspace", "/path/to/workspace", "--agent-name", "amp-worker", "--session-name", "aip", "--tool-profile", "worker-hookless"]
    }
  }
}
```

---

## What AIP Replaces

| Traditional | AIP |
|-------------|-------|
| ACP protocol | Selective MCP tools + hooks |
| SSE streaming | tmux pane buffer |
| Message broker | tmux server |
| Service discovery | `tmux list-windows` |
| Session persistence | tmux + native CLI resume |
| Agent framework | bash + tmux commands |
| Database | Filesystem |
| Observability | `events.jsonl` |
| Job queue | `tasks/` + atomic `mv` |

---

**For full documentation, see README.md and AIP.md**

---

## Backend Compatibility Matrix

| Backend | CLI Name | Launch Command | Tier | Hook Config Path |
|---|---|---|---|---|
| Claude Code | claude-code | `claude` | Tier 1 (native) | `.claude/settings.json` |
| Copilot | copilot | `copilot` | Tier 1 (native) | `.github/copilot/hooks.json` |
| Gemini | gemini | `gemini` | Tier 1 (native) | `.gemini/settings.json` |
| Kiro | kiro | `kiro-cli` | Tier 1 (native) | `.kiro/agents/{name}.json` |
| Codex | codex | `codex` | Tier 1 (native) | `.codex/hooks.json` + `config.toml` |
| OpenCode | opencode | `opencode` | Tier 1 (native) | Plugin events |
| Cursor | cursor | `agent` | Tier 1 (native) | `.cursor/settings.json` |
| Qwen | qwen | `qwen` | Tier 1 (native) | `.qwen/settings.json` |
| Kilo | kilo | `kilo` | Tier 1 (native) | Plugin events (opencode fork) |
| Vibe (Mistral) | vibe | `vibe` | Tier 2 (shim) | `aip-shim` intercept |
| Amp | amp | `amp` | Tier 2 (shim) | `aip-shim` intercept |

**Tier 1 (native)**: CLI has built-in hook/plugin support; `aip hook install` writes config directly.
**Tier 2 (shim)**: No native hooks; `aip-shim` provides lifecycle telemetry via process monitoring.
**MCP-only**: No hooks or shim; agent uses MCP tools (`report_status`, `report_progress`) as fallback.
