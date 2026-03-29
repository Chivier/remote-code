# Skill Manager

Skills sync is a two-stage process that spans both the Head Node and the Daemon. Understanding which component does what prevents confusion when debugging skill sync issues.

## Overview

Skills are shared instruction files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) and skill documents (`.claude/skills/`, etc.) that provide Claude and other CLIs with reusable context for your projects.

The sync pipeline works as follows:

```
Local machine                     Remote machine
─────────────────────             ─────────────────────────────────────
~/.codecast/skills/          SCP  ~/.codecast/skills/
  CLAUDE.md              ──────▶    CLAUDE.md
  .claude/skills/                   .claude/skills/
    coding-standards.md               coding-standards.md

                                     ▼ (on session.create)

                                  /home/user/project/
                                    CLAUDE.md        (if not already present)
                                    .claude/skills/
                                      coding-standards.md
```

## Stage 1: Head Node to Remote Machine (SSHManager)

**File:** `src/head/ssh_manager.py`
**Method:** `sync_skills(machine_id)`

When `cmd_start()` or `cmd_resume()` is called, the BotEngine calls `ssh.sync_skills()` to populate the remote machine's shared skills directory.

The SSHManager copies from the local `skills.shared_dir` (configured in `config.yaml`, defaults to `~/.codecast/skills`) to `~/.codecast/skills` on the remote machine via SCP (asyncssh's `scp` support).

This stage happens **once per session creation** (or on every connection, depending on configuration). It ensures the daemon's source directory is populated before any session starts.

## Stage 2: Remote Skills Dir to Project (SkillManager in Rust)

**File:** `src/daemon/skill_manager.rs`
**Struct:** `SkillManager`

When `session.create` is called, `server.rs` calls `skill_manager.sync_to_project(project_path, cli_type)`. This copies from `~/.codecast/skills` to the specific project directory.

```rust
pub struct SkillManager {
    skills_source_dir: PathBuf,  // Default: ~/.codecast/skills
}
```

### `sync_to_project(project_path, cli_type) -> SyncResult`

Uses the `CliAdapter` to determine the correct file names for the target CLI:

```rust
let adapter = create_adapter(cli_type);
let instructions_file = adapter.instructions_file();  // "CLAUDE.md", "AGENTS.md", "GEMINI.md"
let skills_dir = adapter.skills_dir();                // Some(".claude/skills/"), or None
```

Then:
1. Copies `{source}/{instructions_file}` to `{project}/{instructions_file}` — **only if the target does not already exist**
2. If `skills_dir` is `Some`, recursively copies `{source}/{skills_dir}` to `{project}/{skills_dir}` — **skipping any file that already exists in the target**

Returns `SyncResult { synced: Vec<String>, skipped: Vec<String> }`.

### No-Overwrite Policy

The SkillManager never overwrites existing files. This is a deliberate design choice:

- Projects may have their own `CLAUDE.md` with project-specific instructions that should take precedence
- Shared skills provide a baseline; project-specific files override them
- After initial sync, the project's customized files are preserved across new sessions

### Supported CLI Adapters

| CLI type | Instructions file | Skills dir |
|---|---|---|
| `claude` | `CLAUDE.md` | `.claude/skills/` |
| `codex` | `AGENTS.md` | (none) |
| `gemini` | `GEMINI.md` | (none) |
| `opencode` | `AGENTS.md` | (none) |

### Example

Given this source structure:

```
~/.codecast/skills/
├── CLAUDE.md
└── .claude/
    └── skills/
        ├── coding-standards.md
        └── review-checklist.md
```

And this project state:

```
/home/user/project/
├── CLAUDE.md                    # Already exists — not overwritten
└── .claude/
    └── skills/
        └── coding-standards.md  # Already exists — not overwritten
```

Result:
- `synced`: `[".claude/skills/review-checklist.md"]`
- `skipped`: `["CLAUDE.md (already exists)", ".claude/skills/coding-standards.md (already exists)"]`

## Debugging Skill Sync

If skills are not appearing in a project:

1. Confirm the local `~/.codecast/skills/` directory exists and contains the expected files
2. After running `/start`, SSH into the remote machine and check `~/.codecast/skills/` — if empty, Stage 1 (SCP) failed
3. If Stage 1 succeeded but the project directory is missing files, check the daemon logs for `sync_to_project` output — the files may already exist in the project (no-overwrite policy)
4. To force-resync a specific file, delete it from the project directory on the remote machine and run `/start` again

## Connection to Other Modules

- **ssh_manager.py** (Head Node) is responsible for populating `~/.codecast/skills` on the remote machine
- **server.rs** calls `skill_manager.sync_to_project()` during `session.create`
- **cli_adapter/mod.rs** provides `instructions_file()` and `skills_dir()` per CLI type
