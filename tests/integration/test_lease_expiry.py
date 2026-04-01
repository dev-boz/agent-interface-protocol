#!/usr/bin/env python3
"""Test task lease expiry and reclaim functionality."""

import json
import subprocess
import time
from pathlib import Path

def run_aip(args):
    """Run aip command and return parsed JSON output."""
    result = subprocess.run(
        ["python3", "-m", "aip"] + args,
        capture_output=True,
        text=True,
        check=True
    )
    return json.loads(result.stdout)

def main():
    workspace = Path("workspace")

    # Create a task via MCP
    print("1. Creating task via MCP...")
    proc = subprocess.Popen(
        ["aip-mcp", "--workspace", "workspace", "--agent-name", "test-agent"],
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
    proc.stdout.readline()  # skip response

    # Create task
    task_req = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "request_task",
            "arguments": {
                "task_description": "test lease expiry",
                "priority": "high"
            }
        }
    }) + "\n"
    proc.stdin.write(task_req)
    proc.stdin.flush()
    proc.stdout.readline()  # skip response
    proc.terminate()
    proc.wait()

    print("   ✓ Task created")

    # Claim with very short lease (2 seconds)
    print("\n2. Claiming task with 2-second lease...")
    result = run_aip(["task", "claim", "task-002", "short-lived-agent", "--lease-seconds", "2"])
    print(f"   ✓ Claimed by {result['claimed_by']}")

    # Wait for lease to expire
    print("\n3. Waiting 3 seconds for lease to expire...")
    time.sleep(3)
    print("   ✓ Lease should be expired now")

    # Reclaim expired tasks
    print("\n4. Reclaiming expired tasks...")
    result = run_aip(["task", "reclaim-expired"])
    print(f"   ✓ Reclaimed {len(result['reclaimed'])} task(s): {result['reclaimed']}")

    # Verify task is back in pending
    print("\n5. Verifying task is back in pending...")
    pending = run_aip(["task", "list", "--stage", "pending"])
    task_ids = [t["task_id"] for t in pending]
    assert "task-002" in task_ids
    print(f"   ✓ Task-002 is back in pending")

    print("\n✅ Lease expiry and reclaim working correctly!")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
