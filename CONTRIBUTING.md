# Contributing to AIP

Thanks for your interest in contributing! AIP is early-stage and there's plenty of room to shape it.

## Ways to Contribute

- **Add a CLI backend adapter** — the most impactful and approachable contribution
- **Write an example scenario** — end-to-end scripts in `examples/`
- **Improve test coverage** — especially integration tests for hook configs
- **Report bugs or design issues** — [open a discussion](https://github.com/dev-boz/agent-interface-protocol/discussions) for design questions, an issue for bugs
- **Documentation** — better explanations, diagrams, tutorials

## Development Setup

```bash
git clone https://github.com/dev-boz/agent-interface-protocol.git
cd agent-interface-protocol
pip install -e '.[dev]'
python -m pytest tests/ -q     # should show 195 passing
```

Requirements: Python 3.10+, tmux.

## Adding a New CLI Backend

This is the most common contribution path. A backend adapter touches 3-4 files:

### 1. Register the backend profile (`aip/aip_shim.py`)

Add an entry to `BUILTIN_PROFILES` with the backend's tier, CLI name, and capabilities:

```python
"my-cli": ShimProfile(
    name="my-cli",
    tier="native",           # or "mcp-only" or "shim"
    cli_command="my-cli",
    # ... see existing entries for the full shape
),
```

### 2. Add hook config generation (`aip/hook_configs.py`)

If the CLI supports file-based hook configuration, add a generator in `generate_hook_config()` and an installer in `install_hook_config()`. Look at the existing Gemini or Codex implementations as templates.

### 3. Register in the MCP server (`aip/mcp_server.py`)

Add the backend to these dicts as applicable:

- `INJECTION_COMMANDS` — how to inject messages mid-stream (if supported)
- `ELICITATION_SUPPORTED` — whether the CLI supports elicitation prompts
- `BACKEND_LAUNCH_COMMANDS` — the shell command to start the CLI

### 4. Add tests (`tests/test_all_backends.py`)

The test file validates all backends across registries. Your new backend should automatically be picked up by the parametrized tests, but verify:

```bash
python -m pytest tests/test_all_backends.py -q
```

### Example PR structure

```
aip/aip_shim.py        # add ShimProfile entry
aip/hook_configs.py     # add config generation (if applicable)
aip/mcp_server.py       # register in INJECTION_COMMANDS etc.
tests/test_all_backends.py  # verify tests pass
```

## Adding a New MCP Tool

Tools live in `aip/mcp_server.py` inside the `create_mcp_server()` function. Each tool is a decorated async function that receives parameters and interacts with the `AipToolRuntime`.

1. Add the tool function with `@server.tool()`
2. Add it to the relevant tool profiles in `TOOL_PROFILES`
3. Add tests in `tests/test_mcp.py`

## Running Tests

```bash
# All tests
python -m pytest tests/ -q

# Specific module
python -m pytest tests/test_mcp.py -q

# With verbose output on failure
python -m pytest tests/ -x --tb=short
```

Tests don't require tmux or any external services — everything is mocked.

## Code Style

- No linter is enforced yet — just be consistent with the existing code
- Type hints are appreciated but not required
- Tests for new functionality are required
- Keep dependencies minimal — stdlib is preferred over new packages

## Commit Messages

Use clear, descriptive commit messages. Include `Co-authored-by` trailers when pairing.

## Questions?

Open a [discussion](https://github.com/dev-boz/agent-interface-protocol/discussions) — lower barrier than an issue, better for design conversations.
