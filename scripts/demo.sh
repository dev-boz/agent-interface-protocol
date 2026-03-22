#!/bin/bash
# ATMUX Live Demo
# Demonstrates full orchestration cycle with real tmux agents

set -e

echo "============================================================"
echo "ATMUX Live Demo: Multi-Agent Orchestration"
echo "============================================================"
echo ""
echo "This demo shows:"
echo "  - Orchestrator delegating tasks to specialists"
echo "  - Agents coordinating via shared workspace"
echo "  - Task queue with atomic claiming"
echo "  - Event log for observability"
echo "  - Fault tolerance (orchestrator crash recovery)"
echo ""
echo "Press Enter to start..."
read

# Initialize
echo ""
echo "📦 Initializing ATMUX..."
python -m atmux init --ensure-session
echo "✓ Workspace created"
echo "✓ tmux session 'atmux' created"
echo ""
echo "Press Enter to continue..."
read

# Show workspace structure
echo ""
echo "📁 Workspace structure:"
tree workspace -L 2
echo ""
echo "Press Enter to continue..."
read

# Spawn agents
echo ""
echo "🤖 Spawning agents..."
python -m atmux agent spawn coder "bash"
python -m atmux agent spawn reviewer "bash"
python -m atmux agent spawn tester "bash"
echo "✓ Spawned 3 agents"
echo ""
echo "Current agents:"
python -m atmux agent list | jq -r '.[] | "  - \(.name) (window \(.index))"'
echo ""
echo "Press Enter to continue..."
read

# Orchestrator creates tasks
echo ""
echo "🎯 Orchestrator creating tasks..."
echo ""

# Task 1
echo "Creating task: implement login feature..."
atmux-mcp --workspace workspace --agent-name orchestrator <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"request_task","arguments":{"task_description":"implement login feature with JWT","target_role":"coder","priority":"high"}}}
EOF

# Task 2
echo "Creating task: write unit tests..."
atmux-mcp --workspace workspace --agent-name orchestrator <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"request_task","arguments":{"task_description":"write unit tests for login","target_role":"tester","priority":"medium"}}}
EOF

echo ""
echo "✓ Created 2 tasks"
echo ""
echo "Pending tasks:"
python -m atmux task list --stage pending | jq -r '.[] | "  - \(.task_id): \(.description)"'
echo ""
echo "Press Enter to continue..."
read

# Coder claims and works
echo ""
echo "👨‍💻 Coder claiming task..."
TASK_ID=$(python -m atmux task list --stage pending | jq -r '.[0].task_id')
python -m atmux task claim "$TASK_ID" coder
echo "✓ Coder claimed $TASK_ID"
echo ""

echo "Coder reporting status..."
atmux-mcp --workspace workspace --agent-name coder <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"report_status","arguments":{"status":"working","message":"implementing JWT login"}}}
EOF
echo "✓ Status: working"
echo ""

echo "Coder reporting progress..."
atmux-mcp --workspace workspace --agent-name coder <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"report_progress","arguments":{"progress":"2 of 3 files done","percentage":67}}}
EOF
echo "✓ Progress: 67%"
echo ""
echo "Press Enter to continue..."
read

# Coder completes
echo ""
echo "Coder exporting summary..."
atmux-mcp --workspace workspace --agent-name coder <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"export_summary","arguments":{"content":"## Login Feature\n\nImplemented JWT-based login:\n- POST /api/login endpoint\n- Token expiry: 1 hour\n- Refresh token support\n- Tests: 12/12 passing","task_id":"$TASK_ID"}}}
EOF
echo "✓ Summary exported"
echo ""

python -m atmux task complete "$TASK_ID" --agent-name coder
echo "✓ Task marked complete"
echo ""

atmux-mcp --workspace workspace --agent-name coder <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"report_status","arguments":{"status":"finished","message":"login feature complete"}}}
EOF
echo "✓ Status: finished"
echo ""
echo "Press Enter to continue..."
read

# Show event log
echo ""
echo "📊 Event log (orchestrator's dashboard):"
echo ""
tail -n 10 workspace/events.jsonl | jq -r '"[\(.ts | split("T")[1] | split(".")[0])] \(.agent): \(.event) - \(.status // .task // .progress // "N/A")"'
echo ""
echo "Press Enter to continue..."
read

# Show workspace state
echo ""
echo "📁 Workspace state:"
echo ""
echo "Summaries:"
ls -1 workspace/summaries/ | sed 's/^/  - /'
echo ""
echo "Status files:"
ls -1 workspace/status/ | sed 's/^/  - /'
echo ""
echo "Task queue:"
echo "  Pending: $(ls workspace/tasks/pending/ | wc -l)"
echo "  Claimed: $(ls workspace/tasks/claimed/ | wc -l)"
echo "  Done: $(ls workspace/tasks/done/ | wc -l)"
echo ""
echo "Press Enter to continue..."
read

# Orchestrator delegates review
echo ""
echo "🎯 Orchestrator delegating review..."
SUMMARY_FILE=$(ls -t workspace/summaries/coder-*.md | head -1)
atmux-mcp --workspace workspace --agent-name orchestrator <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"request_task","arguments":{"task_description":"review login implementation","target_role":"reviewer","priority":"high","context":"See $SUMMARY_FILE"}}}
EOF
echo "✓ Review task created"
echo ""
echo "Note: Task references file, not pasted content (30 tokens vs 500+)"
echo ""
echo "Press Enter to continue..."
read

# Show final state
echo ""
echo "============================================================"
echo "✅ Demo Complete"
echo "============================================================"
echo ""
echo "What we demonstrated:"
echo "  ✓ Orchestrator → Coder workflow"
echo "  ✓ Task queue with atomic claiming"
echo "  ✓ Status reporting and progress tracking"
echo "  ✓ Summary export for agent-to-agent communication"
echo "  ✓ Event log for observability"
echo "  ✓ File-based coordination (token-efficient)"
echo ""
echo "Key insight:"
echo "  Agents coordinate via shared workspace, not message passing."
echo "  The orchestrator reads files, not panes (token-efficient)."
echo "  The event log is the single source of truth."
echo ""
echo "To explore:"
echo "  - tmux attach -t atmux    # Watch agents in real-time"
echo "  - cat workspace/events.jsonl | jq    # View event log"
echo "  - cat workspace/summaries/coder-*.md    # Read agent output"
echo ""
echo "To cleanup:"
echo "  - tmux kill-session -t atmux"
echo "  - rm -rf workspace"
echo ""
