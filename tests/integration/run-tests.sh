#!/usr/bin/env bash
set -euo pipefail

echo "=== Codecast Integration Test Runner ==="

# ── 1. Verify mock Claude CLI works ──
echo ""
echo "--- Verifying mock Claude CLI ---"
OUTPUT=$(claude --print "echo:hello world" --output-format stream-json --verbose 2>&1)
if echo "$OUTPUT" | grep -q '"type":"result"'; then
    echo "  [OK] Mock Claude CLI produces valid stream-json"
else
    echo "  [FAIL] Mock Claude CLI output unexpected:"
    echo "$OUTPUT"
    exit 1
fi

# ── 2. Verify daemon binary exists ──
echo ""
echo "--- Verifying daemon binary ---"
if command -v codecast-daemon &>/dev/null; then
    echo "  [OK] codecast-daemon binary found"
else
    echo "  [FAIL] codecast-daemon binary not found"
    exit 1
fi

# ── 3. Verify pip-installed codecast package ──
echo ""
echo "--- Verifying pip install ---"
if python -c "import head; print('head module OK')" 2>&1; then
    echo "  [OK] codecast Python package importable"
else
    echo "  [FAIL] Cannot import head module"
    exit 1
fi

# ── 4. Start daemon in background ──
echo ""
echo "--- Starting daemon on port ${DAEMON_PORT:-9100} ---"
codecast-daemon &
DAEMON_PID=$!

# Wait for daemon to be ready
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:${DAEMON_PORT:-9100}/rpc \
        -X POST -H 'Content-Type: application/json' \
        -d '{"method":"health.check"}' > /dev/null 2>&1; then
        echo "  [OK] Daemon is ready (took ${i}s)"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  [FAIL] Daemon did not start within 30s"
        kill $DAEMON_PID 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# ── 5. Run integration tests ──
echo ""
echo "--- Running integration tests ---"
TEST_EXIT=0
python -m pytest tests/integration/test_integration.py -v --tb=short || TEST_EXIT=$?

# ── 6. Cleanup ──
echo ""
echo "--- Cleanup ---"
kill $DAEMON_PID 2>/dev/null || true
wait $DAEMON_PID 2>/dev/null || true
echo "  Daemon stopped"

exit $TEST_EXIT
