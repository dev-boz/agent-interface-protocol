#!/usr/bin/env python3
"""
Integration test: Full orchestration cycle
Orchestrator → Coder → Reviewer with MCP coordination
"""

import json
import subprocess
import time
from pathlib import Path

def run_atmux(args):
    """Run atmux command and return parsed JSON output."""
    result = subprocess.run(
        ["python3", "-m", "atmux"] + args,
        capture_output=True,
        text=True,
        check=True
    )
    return json.loads(result.stdout)

def send_mcp_tool_call(agent_name, tool_name, arguments):
    """Send a tool call to an agent's MCP server."""
    proc = subprocess.Popen(
        ["atmux-mcp", "--workspace", "workspace", "--agent-name", agent_name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    # Initialize
    init_req = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"}
        }
    }) + "\n"
    proc.stdin.write(init_req)
    proc.stdin.flush()
    proc.stdout.readline()

    # Call tool
    tool_req = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }) + "\n"
    proc.stdin.write(tool_req)
    proc.stdin.flush()
    proc.stdout.readline()

    proc.terminate()
    proc.wait()

def main():
    print("=" * 60)
    print("ATMUX Integration Test: Full Orchestration Cycle")
    print("=" * 60)

    # Phase 1: Orchestrator delegates to coder
    print("\n📋 Phase 1: Orchestrator delegates coding task")
    print("-" * 60)

    # Orchestrator creates task
    send_mcp_tool_call("orchestrator", "request_task", {
        "task_description": "implement authentication module",
        "target_role": "coder",
        "priority": "high",
        "context": "Use JWT tokens with 1-hour expiry"
    })
    print("✓ Orchestrator created task-003")

    # List pending tasks
    pending = run_atmux(["task", "list", "--stage", "pending"])
    print(f"✓ Pending tasks: {[t['task_id'] for t in pending]}")

    # Phase 2: Coder claims and works on task
    print("\n👨‍💻 Phase 2: Coder claims and executes task")
    print("-" * 60)

    # Coder claims task
    result = run_atmux(["task", "claim", "task-003", "coder"])
    print(f"✓ Coder claimed task-003 (lease until {result['lease_expires']})")

    # Coder reports status
    send_mcp_tool_call("coder", "report_status", {
        "status": "working",
        "message": "implementing JWT authentication"
    })
    print("✓ Coder status: working")

    # Coder reports progress
    send_mcp_tool_call("coder", "report_progress", {
        "progress": "2 of 3 files done",
        "percentage": 67
    })
    print("✓ Coder progress: 67%")

    # Coder completes and exports summary
    send_mcp_tool_call("coder", "export_summary", {
        "content": """## Authentication Module

Implemented JWT-based authentication with the following features:

- Login endpoint: `/api/auth/login`
- Token refresh: `/api/auth/refresh`
- Token expiry: 1 hour (configurable)
- Password hashing: bcrypt with salt rounds=12

### Files Modified
- `auth/jwt.py` - JWT token generation and validation
- `auth/endpoints.py` - Login and refresh endpoints
- `tests/test_auth.py` - 15 tests, all passing

### Security Notes
- Tokens are signed with HS256
- Refresh tokens stored in httpOnly cookies
- Rate limiting: 5 login attempts per minute per IP
""",
        "task_id": "task-003"
    })
    print("✓ Coder exported summary")

    # Coder marks task complete
    run_atmux(["task", "complete", "task-003", "--agent-name", "coder"])
    send_mcp_tool_call("coder", "report_status", {
        "status": "finished",
        "message": "auth module complete"
    })
    print("✓ Coder marked task complete")

    # Phase 3: Orchestrator reads result and delegates review
    print("\n🎯 Phase 3: Orchestrator delegates review task")
    print("-" * 60)

    # Check event log
    events = Path("workspace/events.jsonl").read_text().strip().split("\n")
    recent_events = [json.loads(e) for e in events[-5:]]
    print(f"✓ Recent events: {len(recent_events)} entries")
    for evt in recent_events:
        print(f"  - {evt['agent']}: {evt['event']}")

    # Find coder's summary
    summaries = list(Path("workspace/summaries").glob("coder-*.md"))
    latest_summary = max(summaries, key=lambda p: p.stat().st_mtime)
    print(f"✓ Found coder summary: {latest_summary.name}")

    # Orchestrator creates review task
    send_mcp_tool_call("orchestrator", "request_task", {
        "task_description": "review authentication module implementation",
        "target_role": "reviewer",
        "priority": "high",
        "context": f"See {latest_summary.relative_to('workspace')}"
    })
    print("✓ Orchestrator created review task-004")

    # Phase 4: Reviewer claims and reviews
    print("\n🔍 Phase 4: Reviewer claims and executes review")
    print("-" * 60)

    # Reviewer claims task
    result = run_atmux(["task", "claim", "task-004", "reviewer"])
    print(f"✓ Reviewer claimed task-004")

    # Reviewer reads coder's summary
    coder_summary = latest_summary.read_text()
    print(f"✓ Reviewer read coder's summary ({len(coder_summary)} chars)")

    # Reviewer reports status
    send_mcp_tool_call("reviewer", "report_status", {
        "status": "working",
        "message": "reviewing auth implementation"
    })
    print("✓ Reviewer status: working")

    # Reviewer exports review
    send_mcp_tool_call("reviewer", "export_summary", {
        "content": """## Authentication Module Review

### Summary
Code review completed for JWT authentication implementation.

### Findings

**Strengths:**
- ✅ Proper use of bcrypt for password hashing
- ✅ httpOnly cookies for refresh tokens (prevents XSS)
- ✅ Rate limiting implemented
- ✅ Good test coverage (15 tests)

**Issues:**
- ⚠️ Consider using RS256 instead of HS256 for better key rotation
- ⚠️ Token expiry should be configurable via environment variable
- ⚠️ Add logging for failed login attempts (security audit trail)

**Security:**
- ✅ No obvious vulnerabilities
- ✅ Follows OWASP best practices
- ⚠️ Recommend adding CSRF protection for state-changing endpoints

### Recommendation
**APPROVED** with minor improvements suggested above.
""",
        "task_id": "task-004"
    })
    print("✓ Reviewer exported review")

    # Reviewer completes task
    run_atmux(["task", "complete", "task-004", "--agent-name", "reviewer"])
    send_mcp_tool_call("reviewer", "report_status", {
        "status": "finished",
        "message": "review complete - approved with suggestions"
    })
    print("✓ Reviewer marked task complete")

    # Phase 5: Verify final state
    print("\n📊 Phase 5: Verify final state")
    print("-" * 60)

    # Check all tasks are done
    done_tasks = run_atmux(["task", "list", "--stage", "done"])
    print(f"✓ Completed tasks: {[t['task_id'] for t in done_tasks]}")

    # Check summaries exist
    summaries = list(Path("workspace/summaries").glob("*.md"))
    print(f"✓ Total summaries: {len(summaries)}")
    for s in sorted(summaries):
        print(f"  - {s.name}")

    # Check event log
    events = Path("workspace/events.jsonl").read_text().strip().split("\n")
    print(f"✓ Total events logged: {len(events)}")

    # Check status files
    status_files = list(Path("workspace/status").glob("*.json"))
    print(f"✓ Agent status files: {len(status_files)}")
    for sf in sorted(status_files):
        status = json.loads(sf.read_text())
        print(f"  - {status['agent']}: {status['status']}")

    print("\n" + "=" * 60)
    print("✅ FULL ORCHESTRATION CYCLE COMPLETE")
    print("=" * 60)
    print("\nWorkflow:")
    print("  Orchestrator → Coder (task-003)")
    print("  Coder → Summary → Orchestrator")
    print("  Orchestrator → Reviewer (task-004)")
    print("  Reviewer → Review → Orchestrator")
    print("\nAll coordination via:")
    print("  - MCP tools (status, progress, summaries, tasks)")
    print("  - Shared workspace (events.jsonl, status/, summaries/, tasks/)")
    print("  - Atomic task claiming (filesystem-based queue)")

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
