from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_TMUX_ENV = "AIP_RUN_LIVE_TMUX"
LIVE_CLI_ENV = "AIP_RUN_LIVE_CLI"
TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUE_VALUES


def _skip_unless_live_tmux() -> None:
    if not _env_enabled(LIVE_TMUX_ENV):
        pytest.skip(f"set {LIVE_TMUX_ENV}=1 to run live tmux tests")
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


def _skip_unless_live_cli() -> None:
    _skip_unless_live_tmux()
    if not _env_enabled(LIVE_CLI_ENV):
        pytest.skip(f"set {LIVE_CLI_ENV}=1 to run live CLI smoke tests")


@dataclass
class McpResponse:
    raw: dict[str, Any]

    @property
    def result(self) -> dict[str, Any]:
        return self.raw["result"]


class LiveMcpClient:
    def __init__(
        self,
        *,
        workspace_root: Path,
        session_name: str,
        agent_name: str,
        tool_profile: str = "full",
    ) -> None:
        self.workspace_root = workspace_root
        self.session_name = session_name
        self.agent_name = agent_name
        self.tool_profile = tool_profile
        self.proc: subprocess.Popen[str] | None = None
        self._next_id = 1

    def __enter__(self) -> LiveMcpClient:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aip.mcp_server",
                "--workspace",
                str(self.workspace_root),
                "--agent-name",
                self.agent_name,
                "--session-name",
                self.session_name,
                "--tool-profile",
                self.tool_profile,
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-live", "version": "1.0"},
            },
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def request(self, method: str, params: dict[str, Any] | None = None) -> McpResponse:
        assert self.proc is not None
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        msg_id = self._next_id
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            request["params"] = params
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()

        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = ""
                if self.proc.stderr is not None:
                    stderr = self.proc.stderr.read()
                raise AssertionError(f"MCP server exited before responding to {method}: {stderr}")
            line = line.strip()
            if not line.startswith("{"):
                continue
            payload = json.loads(line)
            if payload.get("id") != msg_id:
                continue
            if "error" in payload:
                raise AssertionError(f"MCP error for {method}: {payload['error']}")
            return McpResponse(payload)

    def list_tools(self) -> list[dict[str, Any]]:
        response = self.request("tools/list", {})
        return response.result["tools"]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self.request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        content = response.result["content"]
        assert len(content) == 1, content
        assert content[0]["type"] == "text", content
        return json.loads(content[0]["text"])


@dataclass
class LiveAipEnv:
    workspace_root: Path
    session_name: str

    def run_aip(self, *args: str, expect_json: bool = True) -> Any:
        command = [
            sys.executable,
            "-m",
            "aip",
            "--workspace-root",
            str(self.workspace_root),
            "--session-name",
            self.session_name,
            *args,
        ]
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"Command failed ({result.returncode}): {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        if not expect_json:
            return result.stdout
        payload = result.stdout.strip()
        return json.loads(payload) if payload else None

    def capture_pane(self, target: str, *, lines: int = 80) -> str:
        return self.run_aip("agent", "capture", target, "--lines", str(lines), expect_json=False)

    def wait_for_pane_text(
        self,
        target: str,
        needle: str,
        *,
        timeout: float = 20.0,
        lines: int = 120,
    ) -> str:
        deadline = time.monotonic() + timeout
        last_output = ""
        while time.monotonic() < deadline:
            last_output = self.capture_pane(target, lines=lines)
            if needle in last_output:
                return last_output
            time.sleep(0.25)
        raise AssertionError(f"Did not find {needle!r} in pane {target} within {timeout}s.\n{last_output}")

    def mcp_client(self, agent_name: str, *, tool_profile: str = "full") -> LiveMcpClient:
        return LiveMcpClient(
            workspace_root=self.workspace_root,
            session_name=self.session_name,
            agent_name=agent_name,
            tool_profile=tool_profile,
        )


@pytest.fixture
def live_tmux_env(tmp_path: Path) -> LiveAipEnv:
    _skip_unless_live_tmux()

    workspace_root = tmp_path / "workspace"
    session_name = f"aip-live-{uuid.uuid4().hex[:8]}"
    env = LiveAipEnv(workspace_root=workspace_root, session_name=session_name)
    env.run_aip("init", "--ensure-session", "--start-directory", str(PROJECT_ROOT))

    try:
        yield env
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def live_cli_env(live_tmux_env: LiveAipEnv) -> LiveAipEnv:
    _skip_unless_live_cli()
    return live_tmux_env
