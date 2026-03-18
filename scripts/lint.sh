#!/usr/bin/env bash
# Lint and format checker/fixer for Codecast.
# Usage:
#   ./scripts/lint.sh          # Check only (CI mode)
#   ./scripts/lint.sh --fix    # Auto-fix formatting + lints

set -euo pipefail

FIX=false
if [[ "${1:-}" == "--fix" ]]; then
    FIX=true
fi

FAIL=0

# ── Python (ruff) ──

if $FIX; then
    echo "==> Fixing Python formatting..."
    ruff format src/head/ tests/
    echo "==> Fixing Python lint issues..."
    ruff check --fix src/head/ tests/ || true
fi

echo "==> Checking Python lint (ruff check)..."
if ! ruff check src/head/ tests/; then
    FAIL=1
fi

echo "==> Checking Python format (ruff format)..."
if ! ruff format --check src/head/ tests/; then
    FAIL=1
fi

# ── Rust (clippy + rustfmt) ──

if $FIX; then
    echo "==> Fixing Rust formatting..."
    cargo fmt
fi

echo "==> Checking Rust lint (clippy)..."
if ! cargo clippy --all-targets -- -D warnings; then
    FAIL=1
fi

echo "==> Checking Rust format (cargo fmt)..."
if ! cargo fmt --check; then
    FAIL=1
fi

# ── Result ──

if [[ $FAIL -ne 0 ]]; then
    echo ""
    echo "LINT FAILED. Run './scripts/lint.sh --fix' to auto-fix."
    exit 1
else
    echo ""
    echo "All lint checks passed."
fi
