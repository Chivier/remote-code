# Skill Manager (skill-manager.ts)

**File:** `daemon/src/skill-manager.ts`

Handles syncing CLAUDE.md and `.claude/skills/` files from a shared source directory to project directories on the remote machine.

## Purpose

- Sync shared skills to project directories on session creation
- Follow a "skillshare" model where a central source provides common skills
- Avoid overwriting existing project-specific skills

## Architecture

Skills flow through the system in two stages:

1. **Head Node -> Remote Machine**: The SSHManager on the Head Node copies skills from the local `skills.shared_dir` to `~/.remote-code/skills` on the remote machine via SCP.
2. **Remote Skills Dir -> Project**: The SkillManager on the daemon copies from `~/.remote-code/skills` to the specific project directory when a session is created.

## Class: SkillManager

```typescript
class SkillManager {
    private skillsSourceDir: string;
    // Default: ~/.remote-code/skills
}
```

The source directory defaults to `~/.remote-code/skills` (based on the `HOME` environment variable).

## Key Methods

### `syncToProject(projectPath: string) -> { synced: string[], skipped: string[] }`

Syncs skills from the shared source to a project directory. Called by `server.ts` during `session.create`.

**Behavior:**

1. If the source directory doesn't exist, returns empty results (no error)
2. **CLAUDE.md**: Copies `CLAUDE.md` from source to project root, but **only if it does not already exist** in the project. Existing project-specific `CLAUDE.md` files are never overwritten.
3. **.claude/skills/**: Creates the target directory structure and recursively copies skill files, **skipping files that already exist** in the target.

**Return value:**
- `synced`: List of relative file paths that were copied
- `skipped`: List of relative file paths that were skipped (with "(already exists)" suffix)

### `listSharedSkills() -> string[]`

Lists all skills available in the shared source directory. Returns file paths relative to the source.

### `listProjectSkills(projectPath: string) -> string[]`

Lists all skills present in a specific project directory. Returns file paths like `CLAUDE.md` and `.claude/skills/...`.

## Private Methods

### `copyDirRecursive(sourceDir, targetDir, synced, skipped)`

Recursively copies files from source to target directory. Creates subdirectories as needed. For each file:
- If target does not exist: copies the file, adds to `synced`
- If target exists: skips, adds to `skipped`

### `listFilesRecursive(dir, baseDir, result)`

Recursively lists files in a directory, storing paths relative to the base directory.

## No-Overwrite Policy

The SkillManager never overwrites existing files. This is a deliberate design choice:

- Projects may have their own `CLAUDE.md` with project-specific instructions
- Shared skills provide a baseline that projects can customize
- After initial sync, the project's files take precedence

## Example

Given this source structure:

```
~/.remote-code/skills/
├── CLAUDE.md
└── .claude/
    └── skills/
        ├── coding-standards.md
        └── review-checklist.md
```

And this project state:

```
/home/user/project/
├── CLAUDE.md                    # Already exists -- won't be overwritten
└── .claude/
    └── skills/
        └── coding-standards.md  # Already exists -- won't be overwritten
```

Result:
- `synced`: `[".claude/skills/review-checklist.md"]`
- `skipped`: `["CLAUDE.md (already exists)", ".claude/skills/coding-standards.md (already exists)"]`

## Connection to Other Modules

- **server.ts** creates a single SkillManager instance and calls `syncToProject()` during `session.create`
- The Head Node's **ssh_manager.py** is responsible for populating the skills source directory on the remote machine
