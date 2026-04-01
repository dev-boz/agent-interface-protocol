from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable


class TmuxError(RuntimeError):
    """Raised when a tmux command fails."""


@dataclass
class WindowInfo:
    index: int
    name: str
    command: str
    active: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


Runner = Callable[..., subprocess.CompletedProcess[str]]


class TmuxController:
    def __init__(self, session_name: str = "aip", runner: Runner | None = None) -> None:
        self.session_name = session_name
        self.runner = runner or subprocess.run

    def session_exists(self) -> bool:
        result = self._run("has-session", "-t", self.session_name, check=False)
        return result.returncode == 0

    def ensure_session(
        self,
        *,
        window_name: str = "orchestrator",
        command: str | None = None,
        start_directory: str | None = None,
    ) -> bool:
        if self.session_exists():
            return False
        args = ["new-session", "-d", "-s", self.session_name, "-n", window_name]
        if start_directory:
            args.extend(["-c", start_directory])
        args.append(command or os.environ.get("SHELL", "/bin/bash"))
        self._run(*args)
        return True

    def spawn_window(
        self,
        window_name: str,
        command: str,
        *,
        start_directory: str | None = None,
    ) -> None:
        args = ["new-window", "-t", self.session_name, "-n", window_name]
        if start_directory:
            args.extend(["-c", start_directory])
        args.append(command)
        self._run(*args)

    def list_windows(self) -> list[WindowInfo]:
        result = self._run(
            "list-windows",
            "-t",
            self.session_name,
            "-F",
            "#{window_index}\t#{window_name}\t#{pane_current_command}\t#{window_active}",
        )
        windows: list[WindowInfo] = []
        for line in result.stdout.splitlines():
            index, name, command, active = line.split("\t", 3)
            windows.append(
                WindowInfo(
                    index=int(index),
                    name=name,
                    command=command,
                    active=active == "1",
                )
            )
        return windows

    def capture_pane(
        self,
        target: str,
        *,
        lines: int | None = None,
        include_escape: bool = False,
    ) -> str:
        args = ["capture-pane", "-p", "-t", self._target(target)]
        if include_escape:
            args.append("-e")
        if lines:
            args.extend(["-S", f"-{lines}"])
        result = self._run(*args)
        return result.stdout

    def send_keys(self, target: str, text: str, *, press_enter: bool = True) -> None:
        args = ["send-keys", "-t", self._target(target), text]
        if press_enter:
            args.append("Enter")
        self._run(*args)

    def kill_window(self, target: str) -> None:
        self._run("kill-window", "-t", self._target(target))

    def _target(self, target: str) -> str:
        return target if ":" in target else f"{self.session_name}:{target}"

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["tmux", *args]
        result = self.runner(command, capture_output=True, text=True)
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "tmux command failed"
            raise TmuxError(f"{detail}: {' '.join(command)}")
        return result
