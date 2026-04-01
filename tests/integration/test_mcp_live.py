#!/usr/bin/env python3
"""Live MCP server test - exercises all 5 tools via stdio."""

import json
import subprocess
import sys

def send_request(proc, method, params=None, msg_id=1):
    """Send JSON-RPC request and read response."""
    request = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
    }
    if params:
        request["params"] = params

    msg = json.dumps(request) + "\n"
    proc.stdin.write(msg)
    proc.stdin.flush()

    # Read response (skip log lines)
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        if line.startswith("{"):
            return json.loads(line)

def main():
    # Start MCP server
    proc = subprocess.Popen(
        ["aip-mcp", "--workspace", "workspace", "--agent-name", "test-agent"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    try:
        # 1. Initialize
        print("1. Testing initialize...")
        resp = send_request(proc, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"}
        })
        assert resp["result"]["serverInfo"]["name"] == "aip-mcp"
        print("   ✓ Initialize successful")

        # 2. List tools
        print("\n2. Testing tools/list...")
        resp = send_request(proc, "tools/list", {}, msg_id=2)
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert len(tools) == 5
        assert "report_status" in tool_names
        assert "export_summary" in tool_names
        assert "register_capabilities" in tool_names
        assert "request_task" in tool_names
        assert "report_progress" in tool_names
        print(f"   ✓ Found {len(tools)} tools: {', '.join(tool_names)}")

        # 3. Test report_status
        print("\n3. Testing report_status...")
        resp = send_request(proc, "tools/call", {
            "name": "report_status",
            "arguments": {
                "status": "working",
                "message": "testing the MCP server"
            }
        }, msg_id=3)
        assert "result" in resp
        print("   ✓ Status reported")

        # 4. Test register_capabilities
        print("\n4. Testing register_capabilities...")
        resp = send_request(proc, "tools/call", {
            "name": "register_capabilities",
            "arguments": {
                "capabilities": ["python3", "testing"],
                "interests": {
                    "agents": {"coder": "high"},
                    "events": {"status:failed": "high"},
                    "summaries": {"architect-*": "medium"}
                }
            }
        }, msg_id=4)
        assert "result" in resp
        print("   ✓ Capabilities registered")

        # 5. Test report_progress
        print("\n5. Testing report_progress...")
        resp = send_request(proc, "tools/call", {
            "name": "report_progress",
            "arguments": {
                "progress": "3 of 5 tests done",
                "percentage": 60
            }
        }, msg_id=5)
        assert "result" in resp
        print("   ✓ Progress reported")

        # 6. Test export_summary
        print("\n6. Testing export_summary...")
        resp = send_request(proc, "tools/call", {
            "name": "export_summary",
            "arguments": {
                "content": "## Test Summary\n\nAll MCP tools working correctly.",
                "task_id": "test-001"
            }
        }, msg_id=6)
        assert "result" in resp
        print("   ✓ Summary exported")

        # 7. Test request_task
        print("\n7. Testing request_task...")
        resp = send_request(proc, "tools/call", {
            "name": "request_task",
            "arguments": {
                "task_description": "review the test results",
                "target_role": "reviewer",
                "priority": "high"
            }
        }, msg_id=7)
        assert "result" in resp
        print("   ✓ Task requested")

        print("\n✅ All MCP tools working!")
        return 0

    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    sys.exit(main())
