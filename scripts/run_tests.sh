#!/bin/bash
# ATMUX Test Runner
# Runs all unit and integration tests

set -e

echo "============================================================"
echo "ATMUX Test Suite"
echo "============================================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track results
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

run_test() {
    local test_name="$1"
    local test_command="$2"

    echo -n "Running $test_name... "
    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    if eval "$test_command" > /tmp/atmux_test_$$.log 2>&1; then
        echo -e "${GREEN}✓ PASSED${NC}"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo -e "${RED}✗ FAILED${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo "  Log: /tmp/atmux_test_$$.log"
        tail -n 20 /tmp/atmux_test_$$.log | sed 's/^/    /'
    fi
}

# Change to atmux root directory
cd "$(dirname "$0")/.."

echo "Phase 1: Unit Tests"
echo "------------------------------------------------------------"
run_test "Unit tests (pytest)" "python -m pytest -q tests/"
echo ""

echo "Phase 2: Integration Tests"
echo "------------------------------------------------------------"

# Clean workspace before integration tests
rm -rf workspace
mkdir -p workspace

# Ensure tmux session exists
python -m atmux init --ensure-session > /dev/null 2>&1 || true

run_test "MCP server live test" "python tests/integration/test_mcp_live.py"
run_test "Task lease expiry test" "python tests/integration/test_lease_expiry.py"
run_test "Full orchestration cycle" "python tests/integration/test_full_cycle.py"
run_test "Agent lifecycle test" "python tests/integration/test_agent_lifecycle.py"
run_test "Orchestrator crash recovery" "python tests/integration/test_orchestrator_recovery.py"

echo ""
echo "Phase 3: CLI Commands"
echo "------------------------------------------------------------"

run_test "atmux init" "python -m atmux --workspace-root /tmp/atmux-test-cli init"
run_test "atmux session ensure" "python -m atmux --workspace-root /tmp/atmux-test-cli --session-name atmux-test-cli session ensure"
run_test "atmux agent spawn" "python -m atmux --session-name atmux-test-cli agent spawn test-cli-agent 'bash'"
run_test "atmux agent list" "python -m atmux --session-name atmux-test-cli agent list"
run_test "atmux agent send" "python -m atmux --session-name atmux-test-cli agent send test-cli-agent 'echo test'"
run_test "atmux agent capture" "python -m atmux --session-name atmux-test-cli agent capture test-cli-agent --lines 5"
run_test "atmux agent kill" "python -m atmux --session-name atmux-test-cli agent kill test-cli-agent"

# Cleanup CLI test session
tmux kill-session -t atmux-test-cli 2>/dev/null || true
rm -rf /tmp/atmux-test-cli

echo ""
echo "============================================================"
echo "Test Results"
echo "============================================================"
echo ""
echo "Total tests: $TOTAL_TESTS"
echo -e "Passed: ${GREEN}$PASSED_TESTS${NC}"
echo -e "Failed: ${RED}$FAILED_TESTS${NC}"
echo ""

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ Some tests failed${NC}"
    exit 1
fi
