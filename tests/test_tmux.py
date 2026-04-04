import os
import subprocess

from aip.tmux import PaneMetrics, TmuxController


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
    controller = TmuxController("aip", runner=runner)

    created = controller.ensure_session(start_directory="/tmp/work")

    assert created is True
    assert runner.calls[1] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "aip",
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
    controller = TmuxController("aip", runner=runner)

    windows = controller.list_windows()

    assert [window.name for window in windows] == ["orchestrator", "coder"]
    assert windows[0].active is True
    assert windows[1].command == "python"


def test_capture_pane_uses_line_limit_and_session_target():
    runner = FakeRunner(completed(["tmux"], stdout="latest output"))
    controller = TmuxController("aip", runner=runner)

    output = controller.capture_pane("coder", lines=50)

    assert output == "latest output"
    assert runner.calls[0] == [
        "tmux",
        "capture-pane",
        "-p",
        "-t",
        "aip:coder",
        "-S",
        "-50",
    ]


def test_capture_pane_supports_explicit_range():
    runner = FakeRunner(completed(["tmux"], stdout="incremental output"))
    controller = TmuxController("aip", runner=runner)

    output = controller.capture_pane("coder", start_line=7, end_line=-1)

    assert output == "incremental output"
    assert runner.calls[0] == [
        "tmux",
        "capture-pane",
        "-p",
        "-t",
        "aip:coder",
        "-S",
        "7",
        "-E",
        "-1",
    ]


def test_pane_metrics_reads_history_and_height():
    runner = FakeRunner(completed(["tmux"], stdout="120\t24\n"))
    controller = TmuxController("aip", runner=runner)

    metrics = controller.pane_metrics("coder")

    assert metrics == PaneMetrics(history_size=120, pane_height=24)
    assert metrics.total_lines == 144
    assert runner.calls[0] == [
        "tmux",
        "display-message",
        "-p",
        "-t",
        "aip:coder",
        "#{history_size}\t#{pane_height}",
    ]


def test_send_keys_appends_enter_by_default():
    runner = FakeRunner(completed(["tmux"]))
    controller = TmuxController("aip", runner=runner)

    controller.send_keys("coder", "implement auth")

    assert runner.calls[0] == [
        "tmux",
        "send-keys",
        "-t",
        "aip:coder",
        "implement auth",
        "Enter",
    ]
