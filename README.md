# Agent Interface Protocol (AIP)

[![CI](https://github.com/dev-boz/agent-interface-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/dev-boz/agent-interface-protocol/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Status: Work in Progress** — the core works and has 195 passing tests, but this is early-stage. Contributions, feedback, and new backend adapters are very welcome.

Provider-agnostic multi-agent orchestration with near-zero infrastructure. tmux handles process management, inter-process communication, and session persistence. A shared MCP server and filesystem workspace handle coordination. LLMs can already parse each other's natural-language output, so protocol normalization between agents may be unnecessary.

## The Idea

| Instead of... | AIP uses... |
|---|---|
| Message broker | tmux server (shared memory) |
| Agent framework | bash + tmux commands |
| Custom protocol | Agents read each other natively (LLMs as parsers) |
| Service discovery | `tmux list-windows` |
| Database for state | Filesystem with atomic `mv` |
| SSE streaming | tmux pane buffers |
| HTTP transport (remote) | SSH |
| Observability | `workspace/events.jsonl` (append-only log) |

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
pip install -e .

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

See [`examples/two-agent-review.sh`](examples/two-agent-review.sh) for a full two-agent scenario you can run in under a minute (no API keys needed).

## Supported Backends

AIP currently has adapters for 11 CLI agents across two integration tiers:

| Backend | Tier | Hook Config | Notes |
|---|---|---|---|
| Claude Code | Tier 1 (native) | `.claude/settings.json` | Hooks + MCP |
| Copilot | Tier 1 (native) | `.github/copilot/hooks.json` | Hooks + MCP |
| Gemini | Tier 1 (native) | `.gemini/settings.json` | Hooks + MCP |
| Kiro | Tier 1 (native) | `.kiro/agents/{name}.json` | Hooks + MCP |
| Codex | Tier 1 (native) | `.codex/hooks.json` | Hooks + MCP |
| OpenCode | Tier 1 (native) | Plugin events | MCP, manual hook wiring |
| Cursor | Tier 1 (native) | `.cursor/settings.json` | Hooks + MCP |
| Qwen | Tier 1 (native) | `.qwen/settings.json` | Hooks + MCP |
| Kilo | Tier 1 (native) | Plugin events | OpenCode fork |
| Vibe (Mistral) | Tier 2 (shim) | `aip-shim` intercept | MCP + shim watcher |
| Amp | Tier 2 (shim) | `aip-shim` intercept | MCP + shim watcher |

Adding a new backend is one of the easiest ways to contribute — see [CONTRIBUTING.md](CONTRIBUTING.md).

## What's Next

AIP is usable today for local multi-agent workflows, but there's plenty of room to grow:

- [ ] **PyPI publish** — installable via `pip install agent-interface-protocol`
- [ ] **More end-to-end examples** — real-world scenarios beyond the demo script
- [ ] **`aip dashboard`** — live multiplexed view of all agents working in real time
- [ ] **Additional Tier 2 shims** — Windsurf, Cline, Aider, and other CLIs without native hooks
- [ ] **ACP/A2A compatibility layer** — optional flag to emit ACP-formatted events alongside file writes
- [ ] **Incremental pane reads** — cursor-based reads to avoid re-reading old output (massive token savings)
- [ ] **Remote multi-machine orchestration** — SSH-based workspace sync across hosts
- [ ] **IDE extension (VSIX)** — integrate AIP transparently into VS Code, Cursor, Windsurf

If any of these interest you, [open a discussion](https://github.com/dev-boz/agent-interface-protocol/discussions) or grab a [good first issue](https://github.com/dev-boz/agent-interface-protocol/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

## Installation

One runtime dependency: the `mcp` SDK. Everything else is Python stdlib.

```bash
git clone https://github.com/dev-boz/agent-interface-protocol.git
cd agent-interface-protocol
pip install -e .          # installs `aip` and `aip-mcp` commands
pip install -e '.[dev]'   # also installs pytest for development
```

**Requirements**: Python 3.10+, tmux.

## Development

```bash
python -m pytest tests/ -q
```

195 tests covering workspace primitives, task queue, hook normalization, MCP tools, CLI commands, and multi-backend collaboration. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Documentation

- 📖 [Full CLI & MCP Reference](docs/REFERENCE.md)
- 🏗️ [Architecture Deep Dive](docs/ARCHITECTURE.md)
- ⚡ [Quick Reference Card](docs/QUICKREF.md)
- 📋 [Design Spec](agent-interface-protocol.md)

## License

MIT
