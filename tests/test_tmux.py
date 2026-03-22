import os
import subprocess

from atmux.tmux import TmuxController


class FakeRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, capture_output=True, text=True):
        self.calls.append(command)
        return self.responses.pop(0)


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def test_ensure_session_creates_session_when_missing():
    runner = FakeRunner(
        completed(["tmux"], returncode=1),
        completed(["tmux"]),
    )
    controller = TmuxController("atmux", runner=runner)

    created = controller.ensure_session(start_directory="/tmp/work")

    assert created is True
    assert runner.calls[1] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "atmux",
        "-n",
        "orchestrator",
        "-c",
        "/tmp/work",
        os.environ.get("SHELL", "/bin/bash"),
    ]


def test_list_windows_parses_tmux_output():
    runner = FakeRunner(
        completed(
            ["tmux"],
            stdout="0\torchestrator\tbash\t1\n1\tcoder\tpython\t0\n",
        )
    )
    controller = TmuxController("atmux", runner=runner)

    windows = controller.list_windows()

    assert [window.name for window in windows] == ["orchestrator", "coder"]
    assert windows[0].active is True
    assert windows[1].command == "python"


def test_capture_pane_uses_line_limit_and_session_target():
    runner = FakeRunner(completed(["tmux"], stdout="latest output"))
    controller = TmuxController("atmux", runner=runner)

    output = controller.capture_pane("coder", lines=50)

    assert output == "latest output"
    assert runner.calls[0] == [
        "tmux",
        "capture-pane",
        "-p",
        "-t",
        "atmux:coder",
        "-S",
        "-50",
    ]


def test_send_keys_appends_enter_by_default():
    runner = FakeRunner(completed(["tmux"]))
    controller = TmuxController("atmux", runner=runner)

    controller.send_keys("coder", "implement auth")

    assert runner.calls[0] == [
        "tmux",
        "send-keys",
        "-t",
        "atmux:coder",
        "implement auth",
        "Enter",
    ]
