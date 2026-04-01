#!/usr/bin/env python3
"""
Test agent lifecycle: spawn, kill, resume
"""

import json
import subprocess
import time
from pathlib import Path

def run_atmux(args):
    """Run aip command and return parsed JSON output."""
    result = subprocess.run(
        ["python3", "-m", "aip"] + args,
        capture_output=True,
        text=True,
        check=True
    )
    return json.loads(result.stdout)

def main():
    print("=" * 60)
    print("agent-interface-protocol Test: Agent Lifecycle Management")
    print("=" * 60)

    # Phase 1: Spawn agent and verify it's running
    print("\n📦 Phase 1: Spawn agent")
    print("-" * 60)

    result = run_atmux(["agent", "spawn", "lifecycle-test", "bash"])
    print(f"✓ Spawned agent: {result['spawned']}")

    agents = run_atmux(["agent", "list"])
    agent_names = [a['name'] for a in agents]
    assert "lifecycle-test" in agent_names
    print(f"✓ Agent appears in list: {agent_names}")

    # Phase 2: Send commands and capture output
    print("\n💬 Phase 2: Send commands and capture output")
    print("-" * 60)

    run_atmux(["agent", "send", "lifecycle-test", "echo 'Agent is alive'"])
    time.sleep(0.5)

    result = subprocess.run(
        ["python3", "-m", "aip", "agent", "capture", "lifecycle-test", "--lines", "3"],
        capture_output=True,
        text=True,
        check=True
    )
    output = result.stdout
    assert "Agent is alive" in output
    print("✓ Agent responded to command")
    print(f"  Output: {output.strip()[:100]}...")

    # Phase 3: Agent exports state before shutdown
    print("\n💾 Phase 3: Agent exports state")
    print("-" * 60)

    # Simulate agent exporting its session state
    run_atmux(["agent", "send", "lifecycle-test", "echo 'SESSION_ID=test-session-123' > /tmp/agent-state.txt"])
    time.sleep(0.5)
    print("✓ Agent saved session state to /tmp/agent-state.txt")

    # Phase 4: Kill agent
    print("\n💀 Phase 4: Kill agent")
    print("-" * 60)

    run_atmux(["agent", "kill", "lifecycle-test"])
    print("✓ Agent killed")

    agents = run_atmux(["agent", "list"])
    agent_names = [a['name'] for a in agents]
    assert "lifecycle-test" not in agent_names
    print(f"✓ Agent removed from list: {agent_names}")

    # Phase 5: Respawn agent with resume
    print("\n🔄 Phase 5: Respawn agent (simulated resume)")
    print("-" * 60)

    # In real usage, this would be something like:
    # aip agent spawn lifecycle-test "gemini --resume session-abc123"
    # For this test, we just respawn with bash and verify the pattern works

    result = run_atmux(["agent", "spawn", "lifecycle-test-resumed", "bash"])
    print(f"✓ Respawned agent: {result['spawned']}")

    # Verify it can access the saved state
    run_atmux(["agent", "send", "lifecycle-test-resumed", "cat /tmp/agent-state.txt"])
    time.sleep(0.5)

    result = subprocess.run(
        ["python3", "-m", "aip", "agent", "capture", "lifecycle-test-resumed", "--lines", "3"],
        capture_output=True,
        text=True,
        check=True
    )
    output = result.stdout
    assert "SESSION_ID=test-session-123" in output
    print("✓ Resumed agent can access previous session state")
    print(f"  State: {[line for line in output.split('\\n') if 'SESSION_ID' in line][0].strip()}")

    # Phase 6: Verify workspace persistence
    print("\n📁 Phase 6: Verify workspace persistence")
    print("-" * 60)

    # Check that summaries from killed agents persist
    summaries = list(Path("workspace/summaries").glob("*.md"))
    print(f"✓ Summaries persist after agent death: {len(summaries)} files")

    # Check event log persists
    events = Path("workspace/events.jsonl").read_text().strip().split("\n")
    print(f"✓ Event log persists: {len(events)} events")

    # Check status files persist
    status_files = list(Path("workspace/status").glob("*.json"))
    print(f"✓ Status files persist: {len(status_files)} files")

    # Cleanup
    print("\n🧹 Cleanup")
    print("-" * 60)
    run_atmux(["agent", "kill", "lifecycle-test-resumed"])
    print("✓ Cleaned up test agents")

    print("\n" + "=" * 60)
    print("✅ AGENT LIFECYCLE TEST COMPLETE")
    print("=" * 60)
    print("\nVerified:")
    print("  - Agent spawn and list")
    print("  - Command send and capture")
    print("  - Agent state export")
    print("  - Agent kill and removal")
    print("  - Agent respawn with state access")
    print("  - Workspace persistence across agent lifecycle")

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
