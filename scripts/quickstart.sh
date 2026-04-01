#!/bin/bash
# AIP Quick Start Guide
# Get up and running with AIP in 5 minutes

set -e

echo "============================================================"
echo "AIP Quick Start"
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

# Install AIP
echo "Installing AIP..."
cd "$(dirname "$0")/.."
pip install -e . > /dev/null 2>&1
echo "✓ AIP installed"
echo ""

# Verify installation
echo "Verifying installation..."
if ! command -v aip &> /dev/null; then
    echo "❌ aip command not found"
    exit 1
fi
echo "✓ aip command available"

if ! command -v aip-mcp &> /dev/null; then
    echo "❌ aip-mcp command not found"
    exit 1
fi
echo "✓ aip-mcp command available"
echo ""

# Initialize workspace
echo "Initializing workspace..."
aip init --ensure-session > /dev/null
echo "✓ Workspace created at: workspace/"
echo "✓ tmux session 'aip' created"
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
echo "  aip init                    # Initialize workspace"
echo "  aip session ensure          # Create tmux session"
echo ""
echo "Agent Management:"
echo "  aip agent spawn <name> <cmd>    # Spawn agent"
echo "  aip agent list                  # List all agents"
echo "  aip agent capture <name>        # Read agent output"
echo "  aip agent send <name> <text>    # Send command to agent"
echo "  aip agent kill <name>           # Kill agent"
echo ""
echo "Task Queue:"
echo "  aip task list                   # List pending tasks"
echo "  aip task claim <id> <agent>     # Claim a task"
echo "  aip task complete <id>          # Mark task done"
echo "  aip task fail <id>              # Mark task failed"
echo "  aip task reclaim-expired        # Reclaim expired tasks"
echo ""
echo "MCP Server:"
echo "  aip-mcp --workspace <path> --agent-name <name>"
echo ""

# Show example workflow
echo "============================================================"
echo "Example Workflow"
echo "============================================================"
echo ""
echo "1. Spawn an agent:"
echo "   $ aip agent spawn coder 'bash'"
echo ""
echo "2. Send it a command:"
echo "   $ aip agent send coder 'echo Hello from agent'"
echo ""
echo "3. Read its output:"
echo "   $ aip agent capture coder --lines 5"
echo ""
echo "4. Watch agents in real-time:"
echo "   $ tmux attach -t aip"
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
echo "To use AIP with CLI agents that support MCP:"
echo ""
echo "1. Add to your agent's MCP config (e.g., ~/.gemini/settings.json):"
echo ""
cat << 'EOF'
{
  "mcpServers": {
    "aip": {
      "command": "aip-mcp",
      "args": ["--workspace", "/path/to/workspace", "--agent-name", "coder"]
    }
  }
}
EOF
echo ""
echo "2. The agent now has 5 AIP tools:"
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
echo "✓ AIP is ready to use!"
echo ""
echo "Try the demo:"
echo "  $ ./demo.sh"
echo ""
echo "Run the test suite:"
echo "  $ ./run_tests.sh"
echo ""
echo "Read the docs:"
echo "  - README.md           # Full documentation"
echo "  - agent-nexus.md            # Architecture deep-dive"
echo "  - TESTING_SUMMARY.md  # Test results"
echo ""
echo "For real-world usage:"
echo "  1. Install CLI agents (gemini, copilot, cursor, amp, etc.)"
echo "  2. Configure MCP servers for each agent"
echo "  3. Create an orchestrator prompt"
echo "  4. Start orchestrating!"
echo ""
