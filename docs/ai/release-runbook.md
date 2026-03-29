# Release Runbook

Step-by-step guide for releasing a new version of Codecast.

## Pre-Release

### 1. Ensure clean state
```bash
git status              # no uncommitted changes
git pull origin main    # up to date with remote
```

### 2. Bump version
```bash
./scripts/bump-version.sh X.Y.Z
```
This updates: `pyproject.toml`, `src/head/__version__.py`, `Cargo.toml`

### 3. Run full validation
```bash
# Lint
./scripts/lint.sh

# Tests
python -m pytest tests/ -v --ignore=tests/integration

# Build check
pip install build && python -m build --sdist
cargo build --release
```

### 4. Optional: Deploy to test environment
```bash
DEPLOY_DAEMON=1 ./scripts/deploy-test.sh
ssh artoria 'source ~/.venvs/default/bin/activate && python -m pytest tests/ -v'
```

## Release

### 5. Commit and tag
```bash
git add -A
git commit -m "chore: release vX.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

### 6. Monitor CI
- GitHub Actions → **Build & Release** workflow
- Verify: 6 daemon binaries uploaded to GitHub Release
- Verify: sdist published to PyPI

## Post-Release

### 7. Verify PyPI
```bash
pip install codecast==X.Y.Z
python -c "from head import __version__; print(__version__)"
```

### 8. Verify daemon downloads
```bash
# Check GitHub Release assets
gh release view vX.Y.Z
```

### 9. Update production
```bash
# Via Discord bot (admin only)
/update    # git pull + restart
```

## Rollback

If a release is broken:

```bash
# Remove the tag
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z

# Delete the GitHub Release (if needed)
gh release delete vX.Y.Z --yes

# Fix, re-bump, re-release
./scripts/bump-version.sh X.Y.(Z+1)
```

## CI Build Matrix

The release workflow (`.github/workflows/release.yml`) builds:

| Platform | Asset | Notes |
|----------|-------|-------|
| Linux x64 | `codecast-daemon-linux-x64` | musl static |
| Linux arm64 | `codecast-daemon-linux-arm64` | musl static, cross-compiled |
| macOS arm64 | `codecast-daemon-macos-arm64` | Apple Silicon |
| macOS x64 | `codecast-daemon-macos-x64` | Intel |
| Windows x64 | `codecast-daemon-windows-x64.exe` | MSVC |
| Windows arm64 | `codecast-daemon-windows-arm64.exe` | MSVC |
