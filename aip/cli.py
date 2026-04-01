from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .aip_shim import AipShim, BlockRule, load_profile
from .hook_configs import generate_hook_config, install_hook_config
from .hooks import HookRuntime, parse_hook_payload
from .hooks import parse_codex_notification, parse_hook_stdin
from .tasks import TaskQueue
from .tmux import TmuxController, TmuxError
from .workspace import AipWorkspace, isoformat_z, utc_now


def _build_handoff_summary(agent_name: str, claimed_tasks: list, pane_output: str, reason: str) -> str:
    lines = [f"# Interrupted handoff for {agent_name}", "", f"Reason: {reason}"]
    if claimed_tasks:
        lines.extend(("", "## Requeued tasks"))
        for task in claimed_tasks:
            lines.append(f"- `{task.task_id}`: {task.description}")
            if task.context:
                lines.append(f"  - Context: {task.context}")
    if pane_output.strip():
        lines.extend(("", "## Recent pane output", "", "```text", pane_output.rstrip(), "```"))
    return "\n".join(lines).rstrip() + "\n"


def shutdown_agent_tree(
    workspace: AipWorkspace,
    queue: TaskQueue,
    tmux: TmuxController,
    target: str,
    *,
    reason: str = "manual shutdown",
    capture_lines: int = 20,
) -> list[dict[str, object]]:
    ordered_agents = workspace.agent_subtree_postorder(target)
    timestamp = isoformat_z(utc_now())
    results: list[dict[str, object]] = []

    for agent_name in ordered_agents:
        claimed_tasks = queue.list_claimed_tasks(agent_name)
        pane_output = ""
        capture_error = None
        try:
            pane_output = tmux.capture_pane(agent_name, lines=capture_lines)
        except TmuxError as exc:
            capture_error = str(exc)

        handoff_path = None
        if claimed_tasks or pane_output.strip():
            summary_path = workspace.export_summary(
                agent_name,
                _build_handoff_summary(agent_name, claimed_tasks, pane_output, reason),
            )
            handoff_path = summary_path.relative_to(workspace.root).as_posix()
            workspace.append_event(agent_name, "export", file=handoff_path, handoff=True, reason=reason)

        requeued = queue.requeue_tasks_for_agent(
            agent_name,
            reason=reason,
            handoff_summary=handoff_path,
            actor_name="system",
        )
        workspace.write_status(
            agent_name,
            active=False,
            terminated_at=timestamp,
            shutdown_reason=reason,
            handoff_summary=handoff_path,
        )
        workspace.append_event(agent_name, "shutdown", phase="requested", reason=reason)

        kill_error = None
        try:
            tmux.kill_window(agent_name)
        except TmuxError as exc:
            kill_error = str(exc)

        workspace.append_event(
            agent_name,
            "shutdown",
            phase="completed",
            reason=reason,
            kill_error=kill_error,
            capture_error=capture_error,
        )
        if kill_error is None:
            workspace.remove_agent_node(agent_name)
        results.append(
            {
                "agent": agent_name,
                "requeued_tasks": [task.task_id for task in requeued],
                "handoff_summary": handoff_path,
                "killed": kill_error is None,
                "kill_error": kill_error,
                "capture_error": capture_error,
            }
        )

    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aip")
    parser.add_argument("--workspace-root", default="workspace")
    parser.add_argument("--session-name", default="aip")

    subparsers = parser.add_subparsers(dest="command_group", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--ensure-session", action="store_true")
    init_parser.add_argument("--orchestrator-command")
    init_parser.add_argument("--start-directory")

    session_parser = subparsers.add_parser("session")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_ensure = session_subparsers.add_parser("ensure")
    session_ensure.add_argument("--window-name", default="orchestrator")
    session_ensure.add_argument("--command", dest="session_exec")
    session_ensure.add_argument("--start-directory")

    agent_parser = subparsers.add_parser("agent")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    spawn_parser = agent_subparsers.add_parser("spawn")
    spawn_parser.add_argument("name")
    spawn_parser.add_argument("agent_command_text")
    spawn_parser.add_argument("--start-directory")

    agent_subparsers.add_parser("list")

    capture_parser = agent_subparsers.add_parser("capture")
    capture_parser.add_argument("target")
    capture_parser.add_argument("--lines", type=int)
    capture_parser.add_argument("--include-escape", action="store_true")

    send_parser = agent_subparsers.add_parser("send")
    send_parser.add_argument("target")
    send_parser.add_argument("text")
    send_parser.add_argument("--no-enter", action="store_true")

    kill_parser = agent_subparsers.add_parser("kill")
    kill_parser.add_argument("target")

    task_parser = subparsers.add_parser("task")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

    task_list = task_subparsers.add_parser("list")
    task_list.add_argument("--stage", default="pending")
    task_list.add_argument("--claimable", action="store_true",
                           help="Only show tasks with no unresolved blocked_by dependencies")

    reclaim_parser = task_subparsers.add_parser("reclaim-expired")
    reclaim_parser.add_argument("--json", action="store_true")

    claim_parser = task_subparsers.add_parser("claim")
    claim_parser.add_argument("task_id")
    claim_parser.add_argument("agent_name")
    claim_parser.add_argument("--lease-seconds", type=int, default=1800)

    complete_parser = task_subparsers.add_parser("complete")
    complete_parser.add_argument("task_id")
    complete_parser.add_argument("--agent-name")

    fail_parser = task_subparsers.add_parser("fail")
    fail_parser.add_argument("task_id")
    fail_parser.add_argument("--agent-name")

    hook_parser = subparsers.add_parser("hook")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_command", required=True)

    hook_emit = hook_subparsers.add_parser("emit")
    hook_emit.add_argument("--agent-name", required=True)
    hook_emit.add_argument("--event", required=True)
    hook_emit.add_argument("--payload-json")
    hook_emit.add_argument("--payload-file")

    hook_proxy = hook_subparsers.add_parser("proxy")
    hook_proxy.add_argument("--agent-name", required=True)
    hook_proxy.add_argument("--output-mode", choices=("silent", "json-empty"), default="silent")

    hook_notify_proxy = hook_subparsers.add_parser("notify-proxy")
    hook_notify_proxy.add_argument("--agent-name", required=True)
    hook_notify_proxy.add_argument("notification_json", nargs="?")

    hook_print_config = hook_subparsers.add_parser("print-config")
    hook_print_config.add_argument("--cli", required=True, choices=("gemini", "kiro", "codex", "claude-code", "copilot", "cursor", "qwen"))
    hook_print_config.add_argument("--agent-name", required=True)
    hook_print_config.add_argument("--tool-profile", default="worker")

    hook_install = hook_subparsers.add_parser("install")
    hook_install.add_argument("--cli", required=True, choices=("gemini", "kiro", "codex", "claude-code", "copilot", "cursor", "qwen"))
    hook_install.add_argument("--agent-name", required=True)
    hook_install.add_argument("--tool-profile", default="worker")
    hook_install.add_argument("--config-root", default=".")

    shim_parser = subparsers.add_parser("shim")
    shim_subparsers = shim_parser.add_subparsers(dest="shim_command", required=True)

    shim_watch = shim_subparsers.add_parser("watch")
    shim_watch.add_argument("agent_name")
    shim_watch.add_argument("--cli", required=True)
    shim_watch.add_argument("--profiles-dir")
    shim_watch.add_argument("--auto-approve", action="store_true", default=True)
    shim_watch.add_argument("--no-auto-approve", dest="auto_approve", action="store_false")
    shim_watch.add_argument("--poll-interval", type=float, default=0.3)
    shim_watch.add_argument("--max-iterations", type=int)

    shim_check = shim_subparsers.add_parser("check")
    shim_check.add_argument("agent_name")
    shim_check.add_argument("--cli", required=True)
    shim_check.add_argument("--profiles-dir")
    shim_check.add_argument("--auto-approve", action="store_true", default=True)
    shim_check.add_argument("--no-auto-approve", dest="auto_approve", action="store_false")

    shim_list_profiles = shim_subparsers.add_parser("list-profiles")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = AipWorkspace(args.workspace_root)
    queue = TaskQueue(workspace)
    tmux = TmuxController(session_name=args.session_name)

    if args.command_group == "init":
        workspace.ensure()
        created = False
        if args.ensure_session:
            created = tmux.ensure_session(
                command=args.orchestrator_command,
                start_directory=args.start_directory,
            )
        print(
            json.dumps(
                {
                    "workspace": str(workspace.root),
                    "session": args.session_name,
                    "session_created": created,
                }
            )
        )
        return 0

    if args.command_group == "session" and args.session_command == "ensure":
        created = tmux.ensure_session(
            window_name=args.window_name,
            command=args.session_exec,
            start_directory=args.start_directory,
        )
        print(json.dumps({"session": args.session_name, "created": created}))
        return 0

    if args.command_group == "agent":
        if args.agent_command == "spawn":
            tmux.spawn_window(args.name, args.agent_command_text, start_directory=args.start_directory)
            print(json.dumps({"spawned": args.name, "session": args.session_name}))
            return 0
        if args.agent_command == "list":
            windows = [window.to_dict() for window in tmux.list_windows()]
            print(json.dumps(windows, indent=2))
            return 0
        if args.agent_command == "capture":
            sys.stdout.write(
                tmux.capture_pane(
                    args.target,
                    lines=args.lines,
                    include_escape=args.include_escape,
                )
            )
            return 0
        if args.agent_command == "send":
            tmux.send_keys(args.target, args.text, press_enter=not args.no_enter)
            print(json.dumps({"sent": args.target}))
            return 0
        if args.agent_command == "kill":
            stopped = shutdown_agent_tree(workspace, queue, tmux, args.target)
            print(json.dumps({"killed": stopped, "count": len(stopped)}, indent=2))
            return 0

    if args.command_group == "task":
        if args.task_command == "list":
            if args.claimable:
                tasks = [asdict(task) for task in queue.list_claimable_tasks()]
            else:
                tasks = [asdict(task) for task in queue.list_tasks(stage=args.stage)]
            print(json.dumps(tasks, indent=2))
            return 0
        if args.task_command == "reclaim-expired":
            reclaimed = queue.reclaim_expired()
            output = {"reclaimed": reclaimed, "count": len(reclaimed)}
            print(json.dumps(output, indent=2 if args.json else None))
            return 0
        if args.task_command == "claim":
            task = queue.claim_task(
                args.task_id,
                args.agent_name,
                lease_seconds=args.lease_seconds,
            )
            print(json.dumps(asdict(task), indent=2))
            return 0
        if args.task_command == "complete":
            task = queue.complete_task(args.task_id, agent_name=args.agent_name)
            print(json.dumps(asdict(task), indent=2))
            return 0
        if args.task_command == "fail":
            task = queue.fail_task(args.task_id, agent_name=args.agent_name)
            print(json.dumps(asdict(task), indent=2))
            return 0

    if args.command_group == "hook" and args.hook_command == "emit":
        runtime = HookRuntime(args.workspace_root, args.agent_name)
        payload = parse_hook_payload(args.payload_json, args.payload_file)
        result = runtime.emit(args.event, payload)
        print(json.dumps(result, indent=2))
        return 0

    if args.command_group == "hook" and args.hook_command == "proxy":
        runtime = HookRuntime(args.workspace_root, args.agent_name)
        event_name, payload = parse_hook_stdin(sys.stdin.read())
        runtime.emit(event_name, payload)
        if args.output_mode == "json-empty":
            sys.stdout.write("{}\n")
        return 0

    if args.command_group == "hook" and args.hook_command == "notify-proxy":
        runtime = HookRuntime(args.workspace_root, args.agent_name)
        notification_text = args.notification_json if args.notification_json is not None else sys.stdin.read()
        event_name, payload = parse_codex_notification(notification_text)
        if event_name is not None:
            runtime.emit(event_name, payload)
            print(json.dumps({"handled": True, "event": event_name}))
        else:
            print(json.dumps({"handled": False, "reason": "notification type not mapped to hook runtime"}))
        return 0

    if args.command_group == "hook" and args.hook_command == "print-config":
        config = generate_hook_config(
            cli_name=args.cli,
            workspace_root=args.workspace_root,
            agent_name=args.agent_name,
            session_name=args.session_name,
            tool_profile=args.tool_profile,
        )
        print(json.dumps(config, indent=2))
        return 0

    if args.command_group == "hook" and args.hook_command == "install":
        result = install_hook_config(
            cli_name=args.cli,
            config_root=args.config_root,
            workspace_root=args.workspace_root,
            agent_name=args.agent_name,
            session_name=args.session_name,
            tool_profile=args.tool_profile,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command_group == "shim":
        if args.shim_command == "list-profiles":
            from .aip_shim import BUILTIN_PROFILES
            profiles = {}
            for cli_name, data in sorted(BUILTIN_PROFILES.items()):
                profiles[cli_name] = {
                    "tier": data.get("tier", "intercept"),
                }
                if data.get("interactive_intercept"):
                    profiles[cli_name]["prompt_regex"] = data["interactive_intercept"]["prompt_regex"]
            print(json.dumps(profiles, indent=2))
            return 0

        if args.shim_command in ("watch", "check"):
            profile = load_profile(args.cli, profiles_dir=args.profiles_dir)
            if profile.tier == "native":
                print(json.dumps({
                    "error": f"{args.cli} uses native hooks (Tier 1), shim not needed",
                    "cli": args.cli,
                    "tier": "native",
                }))
                return 1

            shim = AipShim(
                args.workspace_root,
                session_name=args.session_name,
                tmux_controller=tmux,
                auto_approve=args.auto_approve,
            )
            shim.add_agent(args.agent_name, profile)

            if args.shim_command == "check":
                result = shim.check_once(args.agent_name)
                print(json.dumps(result or {"detected": False}))
                return 0

            # watch mode
            shim.run(max_iterations=args.max_iterations)
            return 0

    raise AssertionError("Unhandled command")


if __name__ == "__main__":
    raise SystemExit(main())
