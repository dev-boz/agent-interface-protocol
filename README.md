# Agent Interface Protocol (AIP)

[![CI](https://github.com/dev-boz/agent-interface-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/dev-boz/agent-interface-protocol/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Zero-infra multi-agent orchestration. Every agent framework reinvents transport — tmux already solved it. LLMs are parsers, so protocol normalization is unnecessary. One shared MCP server, filesystem workspace, tmux panes. No servers, no brokers, no frameworks.

## What AIP Replaces

| Traditional Approach | AIP Equivalent |
|---|---|
| ACP protocol | 8 MCP tools |
| SSE streaming | tmux pane buffer |
| Message broker | tmux server (shared memory) |
| Service discovery | `tmux list-windows` |
| Session persistence | tmux sessions + native CLI resume |
| Agent framework | bash + tmux commands |
| Protocol normalisation | Agents read each other natively (LLMs ARE parsers) |
| Database for state | Filesystem |
| HTTP transport (remote) | SSH |
| Observability / audit trail | `workspace/events.jsonl` |
| Distributed job queue | `workspace/tasks/` with atomic `mv` |
| Circuit breaker / retry | Orchestrator reads errors in English |
| Remote cloud agents | git push/pull + watcher pane |
| Agent communication model | Interest maps (targeted subscriptions) |
| Blocking/callback mechanism | `wait_for` tool (process-level wait, zero token burn) |
| Sub-agent / sub-task spawning | `spawn_teammate` with depth/breadth limits |
| Agent-to-agent messaging | `notify` tool with mid-stream injection |

## Architecture

```
tmux server "aip"
├── window 0: orchestrator (any CLI agent)
├── window 1: coder (claude/gemini/aider/etc)
├── window 2: reviewer (any CLI agent)
└── ...spawned and killed as needed

workspace/
├── summaries/          ← agent output summaries (markdown)
├── status/             ← agent status files (JSON, merge semantics)
├── tasks/              ← task queue (atomic claiming via mv)
│   ├── pending/        ← unclaimed tasks
│   ├── claimed/        ← in-progress (agent-name prefixed)
│   ├── done/           ← completed
│   └── failed/         ← move back to pending/ to retry
├── events.jsonl        ← append-only event log
└── agent_tree.json     ← teammate hierarchy and parent/child links
```

### Three-Tier Memory

| Tier | Storage | Access | Survives Restart |
|---|---|---|---|
| **Hot** | tmux pane buffers | `tmux capture-pane` | No (rolling window) |
| **Event log** | `workspace/events.jsonl` | `tail -n 50` | Yes |
| **Cold** | workspace files (summaries, status, tasks) | `cat` | Yes |

### Read Hierarchy (Token Efficiency)

Always check in this order — never jump to pane reads when structured data exists:

| Level | Command | Cost | When |
|---|---|---|---|
| 1 | `tail -n 20 workspace/events.jsonl` | ~50 tokens | What happened? |
| 2 | `cat workspace/status/coder.json` | ~20 tokens | Who's doing what? |
| 3 | `cat workspace/summaries/coder-0317.md` | ~100 tokens | What did they produce? |
| 4 | `tmux capture-pane -pt aip:coder -S -5` | ~30 tokens | Quick peek (last 5 lines) |
| 5 | `tmux capture-pane -pt aip:coder -S -20` | ~100 tokens | More context |
| 6 | Full pane read | ~1000+ tokens | Almost never needed |

## Quick Start

```bash
# Initialize workspace and tmux session
aip init --ensure-session

# Spawn an agent
aip agent spawn coder "gemini"

# Read what the agent is doing
aip agent capture coder

# Send it a task
aip agent send coder "implement the auth module"

# List all agents
aip agent list
```

## Backend Compatibility

| Backend | CLI Name | Launch Command | Tier | Hook Config | Recommended Setup |
|---|---|---|---|---|---|
| **Claude Code** | claude-code | `claude` | Tier 1 (native) | `.claude/settings.json` | Both: hooks for telemetry, MCP for coordination |
| **Copilot** | copilot | `copilot` | Tier 1 (native) | `.github/copilot/hooks.json` | Both |
| **Gemini** | gemini | `gemini` | Tier 1 (native) | `.gemini/settings.json` | Both. `aip hook install --cli gemini ...` |
| **Kiro** | kiro | `kiro-cli` | Tier 1 (native) | `.kiro/agents/{name}.json` | Both. `aip hook install --cli kiro ...` |
| **Codex** | codex | `codex` | Tier 1 (native) | `.codex/hooks.json` + `config.toml` | Both. `aip hook install --cli codex ...` |
| **OpenCode** | opencode | `opencode` | Tier 1 (native) | Plugin events | Both, but hook wiring is currently manual |
| **Cursor** | cursor | `agent` | Tier 1 (native) | `.cursor/settings.json` | Both. `aip hook install --cli cursor ...` |
| **Qwen** | qwen | `qwen` | Tier 1 (native) | `.qwen/settings.json` | Both. `aip hook install --cli qwen ...` |
| **Kilo** | kilo | `kilo` | Tier 1 (native) | Plugin events (opencode fork) | Same as opencode — uses plugin event system |
| **Vibe (Mistral)** | vibe | `vibe` | Tier 2 (shim) | `aip-shim` intercept | MCP + `aip shim watch` for lifecycle telemetry |
| **Amp** | amp | `amp` | Tier 2 (shim) | `aip-shim` intercept | MCP + `aip shim watch` for lifecycle telemetry |

## Installation

AIP is a small Python package with one runtime Python dependency: the `mcp` SDK used by `aip-mcp`.

```bash
cd /path/to/agent-interface-protocol
pip install -e .          # installs `aip` and `aip-mcp` commands
pip install -e '.[dev]'   # also installs pytest for development
```

**Requirements**: Python 3.10+, tmux (any version).

## Development

```bash
PYTHONPATH=. python -m pytest -q tests/
```

**Current**: 195 tests, all passing.

## Links

📖 [Full CLI & MCP Reference](docs/REFERENCE.md) · [Architecture Deep Dive](docs/ARCHITECTURE.md) · [Quick Reference Card](docs/QUICKREF.md) · [Spec](agent-interface-protocol.md)

## License

MIT
