#!/bin/bash
# ATMUX Quick Start Guide
# Get up and running with ATMUX in 5 minutes

set -e

echo "============================================================"
echo "ATMUX Quick Start"
echo "============================================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."
if ! command -v tmux &> /dev/null; then
    echo "❌ tmux not found. Install with: apt-get install tmux"
    exit 1
fi
echo "✓ tmux found"

if ! command -v python3 &> /dev/null; then
    echo "❌ python3 not found"
    exit 1
fi
echo "✓ python3 found"
echo ""

# Install ATMUX
echo "Installing ATMUX..."
cd "$(dirname "$0")/.."
pip install -e . > /dev/null 2>&1
echo "✓ ATMUX installed"
echo ""

# Verify installation
echo "Verifying installation..."
if ! command -v atmux &> /dev/null; then
    echo "❌ atmux command not found"
    exit 1
fi
echo "✓ atmux command available"

if ! command -v atmux-mcp &> /dev/null; then
    echo "❌ atmux-mcp command not found"
    exit 1
fi
echo "✓ atmux-mcp command available"
echo ""

# Initialize workspace
echo "Initializing workspace..."
atmux init --ensure-session > /dev/null
echo "✓ Workspace created at: workspace/"
echo "✓ tmux session 'atmux' created"
echo ""

# Show what was created
echo "Workspace structure:"
tree workspace -L 2 2>/dev/null || ls -R workspace
echo ""

# Show available commands
echo "============================================================"
echo "Available Commands"
echo "============================================================"
echo ""
echo "Session Management:"
echo "  atmux init                    # Initialize workspace"
echo "  atmux session ensure          # Create tmux session"
echo ""
echo "Agent Management:"
echo "  atmux agent spawn <name> <cmd>    # Spawn agent"
echo "  atmux agent list                  # List all agents"
echo "  atmux agent capture <name>        # Read agent output"
echo "  atmux agent send <name> <text>    # Send command to agent"
echo "  atmux agent kill <name>           # Kill agent"
echo ""
echo "Task Queue:"
echo "  atmux task list                   # List pending tasks"
echo "  atmux task claim <id> <agent>     # Claim a task"
echo "  atmux task complete <id>          # Mark task done"
echo "  atmux task fail <id>              # Mark task failed"
echo "  atmux task reclaim-expired        # Reclaim expired tasks"
echo ""
echo "MCP Server:"
echo "  atmux-mcp --workspace <path> --agent-name <name>"
echo ""

# Show example workflow
echo "============================================================"
echo "Example Workflow"
echo "============================================================"
echo ""
echo "1. Spawn an agent:"
echo "   $ atmux agent spawn coder 'bash'"
echo ""
echo "2. Send it a command:"
echo "   $ atmux agent send coder 'echo Hello from agent'"
echo ""
echo "3. Read its output:"
echo "   $ atmux agent capture coder --lines 5"
echo ""
echo "4. Watch agents in real-time:"
echo "   $ tmux attach -t atmux"
echo "   (Press Ctrl-b d to detach)"
echo ""
echo "5. Check the event log:"
echo "   $ tail -f workspace/events.jsonl | jq"
echo ""

# Show MCP integration
echo "============================================================"
echo "MCP Integration"
echo "============================================================"
echo ""
echo "To use ATMUX with CLI agents that support MCP:"
echo ""
echo "1. Add to your agent's MCP config (e.g., ~/.gemini/settings.json):"
echo ""
cat << 'EOF'
{
  "mcpServers": {
    "atmux": {
      "command": "atmux-mcp",
      "args": ["--workspace", "/path/to/workspace", "--agent-name", "coder"]
    }
  }
}
EOF
echo ""
echo "2. The agent now has 5 ATMUX tools:"
echo "   - report_status       # Update status"
echo "   - export_summary      # Save output for other agents"
echo "   - register_capabilities  # Declare skills"
echo "   - request_task        # Delegate work"
echo "   - report_progress     # Update progress"
echo ""

# Show next steps
echo "============================================================"
echo "Next Steps"
echo "============================================================"
echo ""
echo "✓ ATMUX is ready to use!"
echo ""
echo "Try the demo:"
echo "  $ ./demo.sh"
echo ""
echo "Run the test suite:"
echo "  $ ./run_tests.sh"
echo ""
echo "Read the docs:"
echo "  - README.md           # Full documentation"
echo "  - atmux.md            # Architecture deep-dive"
echo "  - TESTING_SUMMARY.md  # Test results"
echo ""
echo "For real-world usage:"
echo "  1. Install CLI agents (gemini, copilot, cursor, amp, etc.)"
echo "  2. Configure MCP servers for each agent"
echo "  3. Create an orchestrator prompt"
echo "  4. Start orchestrating!"
echo ""
