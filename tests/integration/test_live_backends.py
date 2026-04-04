from __future__ import annotations

import shutil
import time

import pytest


BACKEND_COMMANDS = {
    "codex": 'codex --no-alt-screen -C /home/dinkum/projects/agent-nexus "Reply with exactly READY and nothing else."',
    "gemini": 'gemini "Reply with exactly READY and nothing else."',
    "qwen": 'qwen -i "Reply with exactly READY and nothing else." --approval-mode yolo',
    "vibe": 'vibe --agent default "Reply with exactly READY and nothing else."',
}

BACKEND_TIMEOUTS = {
    "codex": 60,
    "gemini": 90,
    "qwen": 60,
    "vibe": 60,
}


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} is not installed")


@pytest.mark.live_cli
@pytest.mark.parametrize("backend", ["codex", "gemini", "qwen", "vibe"])
def test_live_backend_ready_smoke(live_cli_env, backend: str) -> None:
    _require_binary(backend)

    pane_name = f"{backend}-live"
    live_cli_env.run_aip("agent", "spawn", pane_name, BACKEND_COMMANDS[backend])

    deadline = time.monotonic() + BACKEND_TIMEOUTS[backend]
    accepted_trust = False
    last_output = ""
    while time.monotonic() < deadline:
        last_output = live_cli_env.capture_pane(pane_name, lines=120)
        if "READY" in last_output:
            break
        if backend == "codex" and not accepted_trust and "Do you trust the contents of this directory?" in last_output:
            live_cli_env.run_aip("agent", "send", pane_name, "")
            accepted_trust = True
        time.sleep(1)

    assert "READY" in last_output, last_output
