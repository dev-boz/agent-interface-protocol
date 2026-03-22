# ATMUX Quick Reference Card

## Essential Commands

### Session Management
```bash
atmux init --ensure-session              # Initialize workspace + tmux session
tmux attach -t atmux                     # Watch agents in real-time
tmux detach                              # (or Ctrl-b d) Exit without killing
```

### Agent Operations
```bash
atmux agent spawn <name> <command>       # Spawn new agent
atmux agent list                         # List all agents
atmux agent capture <name> --lines 5     # Read agent output (last 5 lines)
atmux agent send <name> "text"           # Send command to agent
atmux agent kill <name>                  # Kill agent
```

### Task Queue
```bash
atmux task list                          # List pending tasks
atmux task list --stage claimed          # List claimed tasks
atmux task list --stage done             # List completed tasks
atmux task claim <id> <agent>            # Claim a task
atmux task complete <id>                 # Mark task done
atmux task reclaim-expired               # Reclaim expired tasks
```

### Monitoring
```bash
tail -f workspace/events.jsonl | jq      # Watch event stream
cat workspace/status/<agent>.json        # Check agent status
ls workspace/summaries/                  # List agent outputs
```

---

## MCP Tools (For Agents)

Agents with atmux-mcp installed have these tools:

### report_status
```json
{
  "status": "working|idle|blocked|failed|finished",
  "message": "optional context"
}
```

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
  "context": "reference to files, not pasted content"
}
```

### report_progress
```json
{
  "progress": "3 of 5 files done",
  "percentage": 60
}
```

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
    ↓ (agent claims via atomic mv)
claimed/coder-task-042.md
    ↓ (agent completes)
done/task-042.md
    ↓ (or fails)
failed/task-042.md
    ↓ (move back to retry)
pending/task-042.md
```

---

## Fault Tolerance

### Agent Crash
- Task stays in `claimed/` with lease
- After expiry → `atmux task reclaim-expired`
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
  atmux agent spawn $role "gemini"
done
```

### Monitor All Agents
```bash
atmux agent list | jq -r '.[].name' | while read agent; do
  echo "=== $agent ==="
  cat workspace/status/$agent.json 2>/dev/null || echo "No status"
done
```

### Reclaim Stale Tasks
```bash
# Run periodically (e.g., every 5 minutes)
atmux task reclaim-expired
```

### Export Agent Output Before Kill
```bash
atmux agent send coder "/export"
sleep 2
atmux agent kill coder
```

---

## Debugging

### Watch Event Stream
```bash
tail -f workspace/events.jsonl | jq -r '"[\(.ts | split("T")[1] | split(".")[0])] \(.agent): \(.event)"'
```

### Check Agent Output
```bash
atmux agent capture <name> --lines 20
```

### Attach to tmux Session
```bash
tmux attach -t atmux
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
cd tools/atmux
pip install -e .          # installs atmux and atmux-mcp commands
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

## MCP Configuration

Add to your CLI agent's config file:

**Gemini** (`~/.gemini/settings.json`):
```json
{
  "mcpServers": {
    "atmux": {
      "command": "atmux-mcp",
      "args": ["--workspace", "/path/to/workspace", "--agent-name", "coder"]
    }
  }
}
```

**Claude Code** (`.mcp.json` in project root):
```json
{
  "mcpServers": {
    "atmux": {
      "command": "atmux-mcp",
      "args": ["--workspace", "/path/to/workspace", "--agent-name", "coder"]
    }
  }
}
```

---

## What ATMUX Replaces

| Traditional | ATMUX |
|-------------|-------|
| ACP protocol | 5 MCP tools |
| SSE streaming | tmux pane buffer |
| Message broker | tmux server |
| Service discovery | `tmux list-windows` |
| Session persistence | tmux + native CLI resume |
| Agent framework | bash + tmux commands |
| Database | Filesystem |
| Observability | `events.jsonl` |
| Job queue | `tasks/` + atomic `mv` |

---

**For full documentation, see README.md and atmux.md**
