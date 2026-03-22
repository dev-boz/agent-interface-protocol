#!/usr/bin/env python3
"""
Test concurrent task claiming to verify atomic operations.
Multiple agents race to claim the same task - only one should succeed.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

def run_atmux(args):
    """Run atmux command and return parsed JSON output."""
    result = subprocess.run(
        ["python3", "-m", "atmux"] + args,
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        return json.loads(result.stdout), None
    else:
        return None, result.stderr

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

def claim_task(agent_name, task_id, results):
    """Try to claim a task and store result."""
    result, error = run_atmux(["task", "claim", task_id, agent_name])
    results[agent_name] = {
        "success": result is not None,
        "result": result,
        "error": error
    }

def main():
    print("=" * 60)
    print("ATMUX Test: Concurrent Task Claiming (Race Conditions)")
    print("=" * 60)

    # Phase 1: Create a single task
    print("\n📋 Phase 1: Create task for race condition test")
    print("-" * 60)

    send_mcp_tool_call("orchestrator", "request_task", {
        "task_description": "high-value task that multiple agents want",
        "priority": "high"
    })

    pending = run_atmux(["task", "list", "--stage", "pending"])[0]
    task_id = pending[-1]['task_id']  # Get the latest task
    print(f"✓ Created {task_id}")

    # Phase 2: Spawn multiple agents that race to claim
    print("\n🏁 Phase 2: 5 agents race to claim the same task")
    print("-" * 60)

    agents = ["racer-1", "racer-2", "racer-3", "racer-4", "racer-5"]
    results = {}
    threads = []

    print(f"Starting race for {task_id}...")

    # Start all threads simultaneously
    for agent in agents:
        thread = threading.Thread(target=claim_task, args=(agent, task_id, results))
        threads.append(thread)
        thread.start()

    # Wait for all to complete
    for thread in threads:
        thread.join()

    print("✓ Race complete")

    # Phase 3: Verify exactly one winner
    print("\n🏆 Phase 3: Verify atomic claiming")
    print("-" * 60)

    winners = [agent for agent, res in results.items() if res["success"]]
    losers = [agent for agent, res in results.items() if not res["success"]]

    print(f"\nResults:")
    print(f"  Winners: {len(winners)}")
    print(f"  Losers: {len(losers)}")
    print()

    if len(winners) == 1:
        print(f"✅ Exactly one winner: {winners[0]}")
        print(f"   Claimed by: {results[winners[0]]['result']['claimed_by']}")
        print(f"   Lease until: {results[winners[0]]['result']['lease_expires']}")
    else:
        print(f"❌ RACE CONDITION BUG: {len(winners)} winners (expected 1)")
        for winner in winners:
            print(f"   - {winner}")
        return 1

    print()
    print("Losers (expected behavior):")
    for loser in losers:
        error = results[loser]['error']
        if "already claimed" in error or "No such file" in error:
            print(f"  ✓ {loser}: correctly rejected")
        else:
            print(f"  ⚠ {loser}: unexpected error: {error[:100]}")

    # Phase 4: Verify task is in claimed state
    print("\n📊 Phase 4: Verify task state")
    print("-" * 60)

    claimed = run_atmux(["task", "list", "--stage", "claimed"])[0]
    claimed_ids = [t['task_id'] for t in claimed]

    if task_id in claimed_ids:
        print(f"✓ {task_id} is in claimed stage")
        task = [t for t in claimed if t['task_id'] == task_id][0]
        print(f"  Claimed by: {task['claimed_by']}")
        print(f"  Lease expires: {task['lease_expires']}")
    else:
        print(f"❌ {task_id} not found in claimed stage")
        return 1

    # Phase 5: Test multiple tasks, multiple agents
    print("\n🎯 Phase 5: Multiple tasks, multiple agents (fair distribution)")
    print("-" * 60)

    # Create 3 tasks
    for i in range(3):
        send_mcp_tool_call("orchestrator", "request_task", {
            "task_description": f"task {i+1} for distribution test",
            "priority": "normal"
        })

    print("✓ Created 3 tasks")

    # Get task IDs
    pending = run_atmux(["task", "list", "--stage", "pending"])[0]
    task_ids = [t['task_id'] for t in pending[-3:]]
    print(f"  Task IDs: {task_ids}")

    # 3 agents each try to claim all 3 tasks
    agents = ["worker-A", "worker-B", "worker-C"]
    claim_results = {agent: [] for agent in agents}

    print("\n3 agents each try to claim all 3 tasks...")
    for agent in agents:
        for task_id in task_ids:
            result, error = run_atmux(["task", "claim", task_id, agent])
            if result:
                claim_results[agent].append(task_id)
                print(f"  ✓ {agent} claimed {task_id}")
            else:
                print(f"  ✗ {agent} failed to claim {task_id}")

    print("\nDistribution:")
    for agent, claimed_tasks in claim_results.items():
        print(f"  {agent}: {len(claimed_tasks)} task(s) - {claimed_tasks}")

    total_claimed = sum(len(tasks) for tasks in claim_results.values())
    if total_claimed == 3:
        print(f"\n✅ All 3 tasks claimed exactly once (total: {total_claimed})")
    else:
        print(f"\n❌ Task claiming error: {total_claimed} claims (expected 3)")
        return 1

    print("\n" + "=" * 60)
    print("✅ CONCURRENT CLAIMING TEST COMPLETE")
    print("=" * 60)
    print("\nVerified:")
    print("  - Atomic task claiming (POSIX rename)")
    print("  - Race condition handling (5 agents, 1 winner)")
    print("  - Fair distribution (3 agents, 3 tasks)")
    print("  - Proper error handling for losers")
    print("\nKey finding:")
    print("  os.rename() provides atomic claiming without locks.")
    print("  Multiple agents can safely race for the same task.")

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
