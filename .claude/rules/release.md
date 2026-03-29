# Release Rules

## Version Management

**Single source of truth:** `pyproject.toml` `[project].version`

All version files must stay in sync:

| File | Format | Role |
|------|--------|------|
| `pyproject.toml` | `version = "X.Y.Z"` | Source of truth (Python package) |
| `src/head/__version__.py` | `__version__ = "X.Y.Z"` | Runtime version for Python code |
| `Cargo.toml` | `version = "X.Y.Z"` | Rust daemon version |

## Bump Version

```bash
./scripts/bump-version.sh 0.3.0    # Updates all three files
./scripts/bump-version.sh           # Shows current version
```

## Release Flow

1. `./scripts/bump-version.sh X.Y.Z`
2. `./scripts/lint.sh --fix`
3. Run full test suite: `python -m pytest tests/ -v` (must pass before proceeding)
4. Update `README.md` — ensure feature list, version references, and examples reflect the new release
5. Update `docs/` — ensure both `en/` and `zh/` user docs (commands, configuration, getting-started) match current features
6. Commit: `git add -A && git commit -m 'chore: release vX.Y.Z'`
7. Tag & push: `git tag vX.Y.Z && git push --tags`
8. CI builds daemon binaries for 6 platforms and publishes to GitHub Releases + PyPI

### Post-Version-Bump Checklist

After running `bump-version.sh`, **all three** of the following must be completed before committing:

- [ ] **Full test suite passes** — `python -m pytest tests/ -v` with zero failures
- [ ] **README.md updated** — feature list, supported platforms, CLI types, version badge all current
- [ ] **Docs updated** — `docs/en/` and `docs/zh/` user-facing pages (commands.md, configuration.md, getting-started.md) reflect any new or changed features

## CI Daemon Build Matrix

| Asset name | Platform | Arch | Linking |
|---|---|---|---|
| `codecast-daemon-linux-x64` | Linux | x86_64 | musl (static) |
| `codecast-daemon-linux-arm64` | Linux | aarch64 (cross) | musl (static) |
| `codecast-daemon-macos-arm64` | macOS | Apple Silicon | dynamic |
| `codecast-daemon-macos-x64` | macOS | Intel | dynamic |
| `codecast-daemon-windows-x64.exe` | Windows | x86_64 | MSVC |
| `codecast-daemon-windows-arm64.exe` | Windows | aarch64 | MSVC |

## Linting (Required Before Release)

```bash
./scripts/lint.sh          # Check only
./scripts/lint.sh --fix    # Auto-fix
```

Runs: ruff check + ruff format (Python), cargo clippy + cargo fmt (Rust).
