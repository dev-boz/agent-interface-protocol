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

    create_parser = task_subparsers.add_parser("create")
    create_parser.add_argument("--description", required=True)
    create_parser.add_argument("--task-type", default="general")
    create_parser.add_argument("--priority", default="normal")
    create_parser.add_argument("--target-role")
    create_parser.add_argument("--context")
    create_parser.add_argument("--blocked-by", nargs="+", default=[])
    create_parser.add_argument("--requested-by", default="system")
    create_parser.add_argument("--task-id")

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

    redact_parser = subparsers.add_parser(
        "redact", help="Scrub secrets/tokens/PII from files in place (aip-redact)"
    )
    redact_parser.add_argument("files", nargs="+", help="Files to redact in place")

    context_parser = subparsers.add_parser(
        "context", help="Build a bounded context pack for a task"
    )
    context_subparsers = context_parser.add_subparsers(dest="context_command", required=True)
    context_build = context_subparsers.add_parser("build")
    context_build.add_argument("--task-class", required=True)
    context_build.add_argument("--task-id", required=True)
    context_build.add_argument("--config-dir", default=".aip/context")
    context_build.add_argument("--repo-root", default=".")
    context_build.add_argument("--roles-dir", default=None)

    plan_parser = subparsers.add_parser(
        "plan", help="Project a task's plan.yaml DAG into the queue"
    )
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command", required=True)
    plan_status = plan_subparsers.add_parser("status")
    plan_status.add_argument("--task-id", required=True)
    plan_project = plan_subparsers.add_parser("project")
    plan_project.add_argument("--task-id", required=True)

    worktree_parser = subparsers.add_parser("worktree", help="Manage git worktrees for mutation tasks")
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command", required=True)

    wt_provision = worktree_subparsers.add_parser("provision")
    wt_provision.add_argument("task_id")
    wt_provision.add_argument("agent_id")
    wt_provision.add_argument("--repo-root", default=None)
    wt_provision.add_argument("--branch-prefix", default="task")

    wt_remove = worktree_subparsers.add_parser("remove")
    wt_remove.add_argument("task_id")
    wt_remove.add_argument("--repo-root", default=None)
    wt_remove.add_argument("--force", action="store_true")

    worktree_subparsers.add_parser("list")

    activity_parser = subparsers.add_parser("activity", help="Derive and write agent activity state")
    activity_subparsers = activity_parser.add_subparsers(dest="activity_command", required=True)
    activity_derive = activity_subparsers.add_parser("derive")
    activity_derive.add_argument("agent_name")
    activity_derive.add_argument("--staleness-threshold", type=float, default=120.0)
    activity_derive.add_argument("--write", action="store_true",
                                 help="Persist {agent}-activity.json alongside status files")

    imx_profile_parser = subparsers.add_parser(
        "imx-profile", help="Inspect IMX capability profiles in the catalog"
    )
    imx_profile_subparsers = imx_profile_parser.add_subparsers(
        dest="imx_profile_command", required=True
    )
    imx_list = imx_profile_subparsers.add_parser("list", help="List available IMX profiles")
    imx_list.add_argument("--catalog-dir", default=None,
                          help="IMX catalog directory (default: ~/.imx/catalog)")
    imx_check = imx_profile_subparsers.add_parser(
        "check-risk", help="Check whether a risk tier is permitted by an IMX profile"
    )
    imx_check.add_argument("profile")
    imx_check.add_argument("risk_tier")
    imx_check.add_argument("--catalog-dir", default=None,
                           help="IMX catalog directory (default: ~/.imx/catalog)")

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
        if args.task_command == "create":
            task = queue.create_task(
                description=args.description,
                task_type=args.task_type,
                priority=args.priority,
                target_role=args.target_role,
                context=args.context,
                requested_by=args.requested_by,
                blocked_by=args.blocked_by or None,
                task_id=args.task_id,
            )
            print(json.dumps(asdict(task), indent=2))
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

    if args.command_group == "redact":
        from .redact import redact_file

        results = {}
        total = 0
        for file_path in args.files:
            try:
                reasons = redact_file(file_path)
            except OSError as exc:
                # aip-redact runs from hooks over files that may not exist; skip
                # and report rather than aborting the whole batch.
                results[file_path] = {"error": str(exc)}
                continue
            results[file_path] = reasons
            total += sum(reasons.values())
        print(json.dumps({"files": results, "total_redactions": total}, indent=2))
        return 0

    if args.command_group == "context" and args.context_command == "build":
        from .context_adapter import build_and_write

        path = build_and_write(
            args.task_class,
            args.task_id,
            workspace_root=args.workspace_root,
            config_dir=args.config_dir,
            repo_root=args.repo_root,
            roles_dir=args.roles_dir,
        )
        print(json.dumps({"context_pack": str(path)}, indent=2))
        return 0

    if args.command_group == "plan":
        from .plan import load_plan, node_states, plan_path, project_plan, task_tree

        workspace = AipWorkspace(args.workspace_root)
        plan = load_plan(plan_path(workspace, args.task_id))

        if args.plan_command == "status":
            states = node_states(plan, task_tree(workspace, args.task_id))
            print(json.dumps({"task_id": plan.task_id, "states": states}, indent=2))
            return 0

        if args.plan_command == "project":
            queue = TaskQueue(workspace)
            projected = project_plan(plan, queue)
            states = node_states(plan, task_tree(workspace, args.task_id))
            print(json.dumps({"task_id": plan.task_id, "projected": projected, "states": states}, indent=2))
            return 0

    if args.command_group == "worktree":
        from pathlib import Path
        from .worktree import provision_worktree, remove_worktree, list_worktrees

        workspace = AipWorkspace(args.workspace_root)
        repo_root = Path(args.repo_root) if getattr(args, "repo_root", None) else None

        if args.worktree_command == "provision":
            info = provision_worktree(
                workspace,
                args.task_id,
                args.agent_id,
                repo_root=repo_root,
                branch_prefix=args.branch_prefix,
            )
            print(json.dumps({
                "task_id": info.task_id,
                "agent_id": info.agent_id,
                "worktree_path": info.worktree_path,
                "branch": info.branch,
                "created_at": info.created_at,
            }, indent=2))
            return 0
        if args.worktree_command == "remove":
            removed = remove_worktree(
                workspace,
                args.task_id,
                repo_root=repo_root,
                force=args.force,
            )
            print(json.dumps({"task_id": args.task_id, "removed": removed}, indent=2))
            return 0
        if args.worktree_command == "list":
            entries = list_worktrees(workspace)
            print(json.dumps(entries, indent=2))
            return 0

    if args.command_group == "activity" and args.activity_command == "derive":
        workspace = AipWorkspace(args.workspace_root)
        record = workspace.derive_activity(
            args.agent_name,
            staleness_threshold_seconds=args.staleness_threshold,
            write=args.write,
        )
        print(json.dumps(record, indent=2))
        return 0

    if args.command_group == "imx-profile":
        from pathlib import Path
        from .imx_profile import (
            check_risk_tier_allowed,
            list_available_profiles,
            load_imx_profile,
        )

        catalog_dir = Path(args.catalog_dir) if getattr(args, "catalog_dir", None) else None

        if args.imx_profile_command == "list":
            profiles = list_available_profiles(catalog_dir=catalog_dir)
            print(json.dumps({"profiles": profiles}, indent=2))
            return 0
        if args.imx_profile_command == "check-risk":
            constraints = load_imx_profile(args.profile, catalog_dir=catalog_dir)
            allowed, reason = check_risk_tier_allowed(args.risk_tier, constraints)
            print(json.dumps({
                "profile": args.profile,
                "risk_tier": args.risk_tier,
                "allowed": allowed,
                "reason": reason,
                "profile_loaded": constraints.loaded,
            }, indent=2))
            return 0 if allowed else 1

    raise AssertionError("Unhandled command")


if __name__ == "__main__":
    raise SystemExit(main())
