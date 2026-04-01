#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# two-agent-review.sh — AIP Two-Agent Code-Review Demo
#
# Demonstrates the Agent Interface Protocol (AIP) task lifecycle with two
# agents: a "coder" that implements a feature and a "reviewer" that reviews
# the work.  Everything runs locally in tmux — no API keys required.
#
# What happens:
#   1. Initialize an AIP workspace and tmux session
#   2. Create a coding task in the pending queue
#   3. Spawn a "coder" agent (bash shell) and have it claim + complete the task
#   4. Create a review task and spawn a "reviewer" agent to claim it
#   5. Display the final agent list and completed tasks
#   6. Clean up the tmux session
#
# Prerequisites: bash, tmux, Python ≥ 3.10, and the `aip` CLI installed
#                (pip install -e '.[dev]')
#
# Usage:
#   ./examples/two-agent-review.sh
###############################################################################

# -- Configurable knobs -----------------------------------------------------
SESSION_NAME="aip-demo"
WORKSPACE_ROOT="workspace"

# Global flags must precede the subcommand in every `aip` invocation
AIP="aip --session-name ${SESSION_NAME} --workspace-root ${WORKSPACE_ROOT}"

# -- Colour helpers ----------------------------------------------------------
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
RESET='\033[0m'

banner()  { echo -e "\n${CYAN}${BOLD}==> $1${RESET}"; }
info()    { echo -e "    ${GREEN}✓${RESET} $1"; }
warn()    { echo -e "    ${YELLOW}⚠${RESET} $1"; }

# -- Pre-flight check --------------------------------------------------------
if ! command -v tmux &>/dev/null; then
  echo "ERROR: tmux is required but not installed." >&2
  exit 1
fi
if ! command -v aip &>/dev/null; then
  echo "ERROR: aip CLI not found. Install with: pip install -e '.[dev]'" >&2
  exit 1
fi

# -- Cleanup on exit ---------------------------------------------------------
cleanup() {
  banner "Cleaning up"
  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux kill-session -t "$SESSION_NAME"
    info "Killed tmux session '${SESSION_NAME}'"
  fi
  if [ -d "$WORKSPACE_ROOT" ]; then
    rm -rf "$WORKSPACE_ROOT"
    info "Removed workspace directory"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 1 — Initialise workspace + tmux session
# ---------------------------------------------------------------------------
banner "Step 1: Initialising AIP workspace"
$AIP init --ensure-session > /dev/null
info "Workspace created at ${WORKSPACE_ROOT}/"
info "Tmux session '${SESSION_NAME}' started"

# ---------------------------------------------------------------------------
# Step 2 — Create a coding task (write a markdown file to pending/)
# ---------------------------------------------------------------------------
banner "Step 2: Creating coding task"
TASK_ID="task-001"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "${WORKSPACE_ROOT}/tasks/pending/${TASK_ID}.md" <<EOF
# ${TASK_ID}
type: coding
priority: high
target_role: coder
description: implement auth module with JWT login endpoint
context: greenfield service — no existing code
created_at: ${TIMESTAMP}

## Requirements

- POST /api/login endpoint
- Return JWT token with 1-hour expiry
- Hash passwords with bcrypt
EOF
info "Created task '${TASK_ID}' in pending queue"

# ---------------------------------------------------------------------------
# Step 3 — Spawn two agents (bash shells)
# ---------------------------------------------------------------------------
banner "Step 3: Spawning agents"
$AIP agent spawn coder "bash" > /dev/null
info "Spawned 'coder' agent"

$AIP agent spawn reviewer "bash" > /dev/null
info "Spawned 'reviewer' agent"

# Give tmux a moment to settle
sleep 1

# ---------------------------------------------------------------------------
# Step 4 — Coder claims the task, simulates work, completes it
# ---------------------------------------------------------------------------
banner "Step 4: Coder works on ${TASK_ID}"

# Send a simulated work command to the coder's pane
$AIP agent send coder 'echo "implementing auth module..."' > /dev/null
sleep 0.5

$AIP task claim "$TASK_ID" coder > /dev/null
info "Coder claimed '${TASK_ID}'"

# Simulate some coding time
$AIP agent send coder 'echo "writing tests... done ✓"' > /dev/null
sleep 0.5

$AIP task complete "$TASK_ID" --agent-name coder > /dev/null
info "Coder completed '${TASK_ID}'"

# ---------------------------------------------------------------------------
# Step 5 — Create a review task and have the reviewer claim it
# ---------------------------------------------------------------------------
banner "Step 5: Creating review task"
REVIEW_TASK_ID="task-002"

cat > "${WORKSPACE_ROOT}/tasks/pending/${REVIEW_TASK_ID}.md" <<EOF
# ${REVIEW_TASK_ID}
type: review
priority: high
target_role: reviewer
description: review auth module implementation
context: see task-001 output
created_at: ${TIMESTAMP}

## Review Checklist

- [ ] JWT expiry is set correctly
- [ ] Passwords are hashed, not stored in plaintext
- [ ] Error responses do not leak internal details
EOF
info "Created review task '${REVIEW_TASK_ID}'"

$AIP task claim "$REVIEW_TASK_ID" reviewer > /dev/null
info "Reviewer claimed '${REVIEW_TASK_ID}'"

$AIP agent send reviewer 'echo "reviewing auth module... looks good ✓"' > /dev/null
sleep 0.5

$AIP task complete "$REVIEW_TASK_ID" --agent-name reviewer > /dev/null
info "Reviewer completed '${REVIEW_TASK_ID}'"

# ---------------------------------------------------------------------------
# Step 6 — Show final state
# ---------------------------------------------------------------------------
banner "Step 6: Final state"

echo -e "\n${BOLD}Active agents:${RESET}"
$AIP agent list

echo -e "\n${BOLD}Completed tasks:${RESET}"
$AIP task list --stage done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║            ✓  Demo complete!                    ║${RESET}"
echo -e "${GREEN}${BOLD}╠══════════════════════════════════════════════════╣${RESET}"
echo -e "${GREEN}${BOLD}║  Two agents collaborated on a code review:      ║${RESET}"
echo -e "${GREEN}${BOLD}║                                                 ║${RESET}"
echo -e "${GREEN}${BOLD}║   • coder    → implemented auth module          ║${RESET}"
echo -e "${GREEN}${BOLD}║   • reviewer → reviewed the implementation      ║${RESET}"
echo -e "${GREEN}${BOLD}║                                                 ║${RESET}"
echo -e "${GREEN}${BOLD}║  Both tasks moved:  pending → claimed → done    ║${RESET}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
