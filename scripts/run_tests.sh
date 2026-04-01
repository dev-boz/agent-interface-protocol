#!/bin/bash
# AIP Test Runner
# Runs all unit and integration tests

set -e

echo "============================================================"
echo "AIP Test Suite"
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

    if eval "$test_command" > /tmp/aip_test_$$.log 2>&1; then
        echo -e "${GREEN}✓ PASSED${NC}"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo -e "${RED}✗ FAILED${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo "  Log: /tmp/aip_test_$$.log"
        tail -n 20 /tmp/aip_test_$$.log | sed 's/^/    /'
    fi
}

# Change to aip root directory
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
python -m aip init --ensure-session > /dev/null 2>&1 || true

run_test "MCP server live test" "python tests/integration/test_mcp_live.py"
run_test "Task lease expiry test" "python tests/integration/test_lease_expiry.py"
run_test "Full orchestration cycle" "python tests/integration/test_full_cycle.py"
run_test "Agent lifecycle test" "python tests/integration/test_agent_lifecycle.py"
run_test "Orchestrator crash recovery" "python tests/integration/test_orchestrator_recovery.py"

echo ""
echo "Phase 3: CLI Commands"
echo "------------------------------------------------------------"

run_test "aip init" "python -m aip --workspace-root /tmp/aip-test-cli init"
run_test "aip session ensure" "python -m aip --workspace-root /tmp/aip-test-cli --session-name aip-test-cli session ensure"
run_test "aip agent spawn" "python -m aip --session-name aip-test-cli agent spawn test-cli-agent 'bash'"
run_test "aip agent list" "python -m aip --session-name aip-test-cli agent list"
run_test "aip agent send" "python -m aip --session-name aip-test-cli agent send test-cli-agent 'echo test'"
run_test "aip agent capture" "python -m aip --session-name aip-test-cli agent capture test-cli-agent --lines 5"
run_test "aip agent kill" "python -m aip --session-name aip-test-cli agent kill test-cli-agent"

# Cleanup CLI test session
tmux kill-session -t aip-test-cli 2>/dev/null || true
rm -rf /tmp/aip-test-cli

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
