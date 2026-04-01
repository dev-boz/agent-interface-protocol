# agent-nexus File Organization

## Directory Structure

```
agent-nexus/
├── README.md                    # Main documentation (start here)
├── pyproject.toml              # Package configuration
│
├── aip/                      # Core implementation
│   ├── __init__.py
│   ├── __main__.py             # CLI entry point
│   ├── aip_shim.py             # Tier 2 interactive intercept shim
│   ├── cli.py                  # Operator CLI (170 lines)
│   ├── hook_configs.py         # Per-backend hook config generation
│   ├── hooks.py                # Hook runtime (normalization, stdin, writes)
│   ├── mcp_server.py           # JSON-RPC 2.0 MCP server (370 lines)
│   ├── tasks.py                # Task queue with atomic claiming (245 lines)
│   ├── tmux.py                 # tmux controller (120 lines)
│   └── workspace.py            # Filesystem primitives (165 lines)
│
├── tests/                      # Test suite
│   ├── test_aip_shim.py        # aip-shim intercept tests
│   ├── test_all_backends.py    # All 11 backends (66 tests)
│   ├── test_cli.py             # CLI tests (1 test)
│   ├── test_hooks.py           # Hook runtime and config generation
│   ├── test_mcp.py             # MCP server tests (9 tests)
│   ├── test_tasks.py           # Task queue tests (including blocked_by)
│   ├── test_tmux.py            # tmux controller tests (4 tests)
│   ├── test_workspace.py       # Workspace tests (3 tests)
│   └── integration/            # Integration tests
│       ├── test_agent_lifecycle.py
│       ├── test_concurrent_claiming.py
│       ├── test_full_cycle.py
│       ├── test_lease_expiry.py
│       ├── test_mcp_live.py
│       ├── test_multi_backend_collab.py  # Multi-backend collaboration (8 tests)
│       └── test_orchestrator_recovery.py
│
├── scripts/                    # Utility scripts
│   ├── run_tests.sh            # Comprehensive test runner
│   ├── demo.sh                 # Interactive demo
│   └── quickstart.sh           # 5-minute setup guide
│
├── docs/                       # Documentation
│   ├── ARCHITECTURE.md         # Architecture deep-dive (598 lines)
│   ├── QUICKREF.md             # Quick reference card
│   ├── SESSION_REPORT.md       # Testing session summary
│   ├── TESTING_SUMMARY.md      # Test results and findings
│   └── FILE_ORGANIZATION.md    # This file
│
└── workspace/                  # Runtime workspace (created by init)
    ├── events.jsonl            # Event log
    ├── status/                 # Agent status files
    ├── summaries/              # Agent output summaries
    └── tasks/                  # Task queue
        ├── pending/
        ├── claimed/
        ├── done/
        └── failed/
```

## File Counts

| Category | Count | Lines |
|----------|-------|-------|
| Core implementation | 10 files | ~1,070 lines |
| Unit tests | 8 files | 194 tests |
| Integration tests | 7 files | (included above) |
| Scripts | 3 files | ~150 lines |
| Documentation | 5 files | ~2,600 lines |

## Organization Principles

### Core Implementation (`aip/`)
Pure stdlib Python, zero external dependencies. Each module has a single responsibility:
- `workspace.py` — Filesystem operations (status, events, summaries)
- `tasks.py` — Task queue with atomic claiming and blocked_by dependencies
- `tmux.py` — tmux command generation and execution
- `mcp_server.py` — JSON-RPC 2.0 MCP server (5 tools)
- `hooks.py` — Hook runtime (normalization, stdin parsing, workspace writes)
- `hook_configs.py` — Per-backend hook config generation (all 11 backends)
- `aip_shim.py` — Tier 2 interactive intercept shim (vibe, amp)
- `cli.py` — Operator CLI (init, session, agent, task commands)

### Tests (`tests/`)
- **Unit tests** (root level) — Fast, isolated, no tmux required
- **Integration tests** (`integration/`) — Full workflows with real tmux sessions

### Scripts (`scripts/`)
Executable shell scripts for common operations:
- `run_tests.sh` — Runs all tests (unit + integration + CLI)
- `demo.sh` — Interactive demo showing full orchestration cycle
- `quickstart.sh` — 5-minute setup guide with verification

### Documentation (`docs/`)
- `ARCHITECTURE.md` — Architecture deep-dive (598 lines) - the complete design document
- `QUICKREF.md` — Quick reference card (commands, patterns, tips)
- `SESSION_REPORT.md` — Testing session summary and findings
- `TESTING_SUMMARY.md` — Detailed test results
- `FILE_ORGANIZATION.md` — This file

### Workspace (`workspace/`)
Runtime directory created by `aip init`. Not checked into version control.

## Running Tests

```bash
# All tests
./scripts/run_tests.sh

# Unit tests only
python -m pytest tests/

# Integration tests only
python -m pytest tests/integration/

# Specific test
python tests/integration/test_full_cycle.py
```

## Running Scripts

```bash
# Quick start guide
./scripts/quickstart.sh

# Interactive demo
./scripts/demo.sh

# Test runner
./scripts/run_tests.sh
```

## Documentation

```bash
# Architecture deep-dive
cat docs/ARCHITECTURE.md

# Quick reference
cat docs/QUICKREF.md

# Full documentation
cat README.md

# Test results
cat docs/TESTING_SUMMARY.md

# Session report
cat docs/SESSION_REPORT.md

# File organization
cat docs/FILE_ORGANIZATION.md
```

## Installation

```bash
cd /path/to/agent-nexus
pip install -e .          # installs aip and aip-mcp commands
pip install -e '.[dev]'   # also installs pytest for development
```

## Key Files

| File | Purpose | When to Read |
|------|---------|--------------|
| `README.md` | Main documentation | Start here |
| `docs/ARCHITECTURE.md` | Architecture deep-dive | Understanding design philosophy |
| `docs/QUICKREF.md` | Quick reference | Daily usage |
| `aip/mcp_server.py` | MCP server implementation | Understanding tools |
| `aip/tasks.py` | Task queue logic | Understanding claiming |
| `tests/integration/test_full_cycle.py` | Full workflow example | Understanding orchestration |
| `scripts/run_tests.sh` | Test runner | Running tests |

## Changes from Original Structure

**Before**:
```
agent-nexus/
├── test_*.py (6 files at root)
├── *.sh (3 files at root)
├── *.md (4 files at root)
└── tests/ (unit tests)
```

**After**:
```
agent-nexus/
├── tests/
│   ├── (unit tests)
│   └── integration/ (integration tests)
├── scripts/ (shell scripts)
├── docs/ (documentation)
└── README.md (kept at root)
```

**Benefits**:
- Cleaner root directory
- Clear separation of concerns
- Easier to find files
- Better for version control
- Standard Python project layout
