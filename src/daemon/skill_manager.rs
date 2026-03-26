use std::fs;
use std::path::{Path, PathBuf};
use tracing::info;

use crate::cli_adapter::create_adapter;

/// SkillManager handles syncing CLAUDE.md and .claude/skills/ to project directories.
///
/// Skills are managed via a "skillshare" model:
/// - A central skills directory (synced from Head via scp)
/// - Skills are copied to project paths on session creation
/// - Existing project-specific skills are NOT overwritten
pub struct SkillManager {
    skills_source_dir: PathBuf,
}

#[derive(Debug)]
pub struct SyncResult {
    pub synced: Vec<String>,
    pub skipped: Vec<String>,
}

impl SkillManager {
    pub fn new() -> Self {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("~"));
        Self {
            skills_source_dir: home.join(".codecast").join("skills"),
        }
    }

    pub fn source_dir(&self) -> &Path {
        &self.skills_source_dir
    }

    /// Sync skills from the shared source to a project directory.
    /// Uses the CLI adapter to determine which instructions file and skills dir to sync.
    pub fn sync_to_project(&self, project_path: &Path, cli_type: &str) -> SyncResult {
        let mut synced = Vec::new();
        let mut skipped = Vec::new();

        if !self.skills_source_dir.exists() {
            info!(
                "Skills source dir not found: {}",
                self.skills_source_dir.display()
            );
            return SyncResult { synced, skipped };
        }

        let adapter = create_adapter(cli_type);
        let instructions_file = adapter.instructions_file();

        // Sync instructions file (CLAUDE.md, GEMINI.md, or AGENTS.md)
        let source_instructions = self.skills_source_dir.join(instructions_file);
        let target_instructions = project_path.join(instructions_file);

        if source_instructions.exists() {
            if !target_instructions.exists() {
                if let Err(e) = fs::copy(&source_instructions, &target_instructions) {
                    info!("Failed to copy {}: {}", instructions_file, e);
                } else {
                    synced.push(instructions_file.to_string());
                }
            } else {
                skipped.push(format!("{} (already exists)", instructions_file));
            }
        }

        // Sync skills directory if the adapter has one (currently only Claude)
        if let Some(skills_dir) = adapter.skills_dir() {
            let source_skills_dir = self.skills_source_dir.join(skills_dir);
            let target_skills_dir = project_path.join(skills_dir);

            if source_skills_dir.exists() {
                // Ensure target directory exists
                if let Err(e) = fs::create_dir_all(&target_skills_dir) {
                    info!("Failed to create skills dir: {}", e);
                    return SyncResult { synced, skipped };
                }

                // Copy each skill file, skip if already exists
                self.copy_dir_recursive(
                    &source_skills_dir,
                    &target_skills_dir,
                    &mut synced,
                    &mut skipped,
                );
            }
        }

        info!(
            "Synced to {} (cli={}): {} files synced, {} skipped",
            project_path.display(),
            cli_type,
            synced.len(),
            skipped.len()
        );

        SyncResult { synced, skipped }
    }

    /// Recursively copy directory contents, skipping existing files
    fn copy_dir_recursive(
        &self,
        source_dir: &Path,
        target_dir: &Path,
        synced: &mut Vec<String>,
        skipped: &mut Vec<String>,
    ) {
        let entries = match fs::read_dir(source_dir) {
            Ok(entries) => entries,
            Err(e) => {
                info!("Failed to read dir {}: {}", source_dir.display(), e);
                return;
            }
        };

        for entry in entries.flatten() {
            let source_path = entry.path();
            let file_name = entry.file_name();
            let target_path = target_dir.join(&file_name);

            if source_path.is_dir() {
                let _ = fs::create_dir_all(&target_path);
                self.copy_dir_recursive(&source_path, &target_path, synced, skipped);
            } else {
                let relative_path = source_path
                    .strip_prefix(&self.skills_source_dir)
                    .unwrap_or(&source_path)
                    .to_string_lossy()
                    .to_string();

                if !target_path.exists() {
                    if let Err(e) = fs::copy(&source_path, &target_path) {
                        info!("Failed to copy {}: {}", relative_path, e);
                    } else {
                        synced.push(relative_path);
                    }
                } else {
                    skipped.push(format!("{} (already exists)", relative_path));
                }
            }
        }
    }
}
