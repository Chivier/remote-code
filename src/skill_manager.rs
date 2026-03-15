use std::fs;
use std::path::{Path, PathBuf};
use tracing::info;

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
            skills_source_dir: home.join(".remote-claude").join("skills"),
        }
    }

    pub fn source_dir(&self) -> &Path {
        &self.skills_source_dir
    }

    /// Sync skills from the shared source to a project directory
    pub fn sync_to_project(&self, project_path: &Path) -> SyncResult {
        let mut synced = Vec::new();
        let mut skipped = Vec::new();

        if !self.skills_source_dir.exists() {
            info!(
                "Skills source dir not found: {}",
                self.skills_source_dir.display()
            );
            return SyncResult { synced, skipped };
        }

        // Sync CLAUDE.md
        let source_claude_md = self.skills_source_dir.join("CLAUDE.md");
        let target_claude_md = project_path.join("CLAUDE.md");

        if source_claude_md.exists() {
            if !target_claude_md.exists() {
                if let Err(e) = fs::copy(&source_claude_md, &target_claude_md) {
                    info!("Failed to copy CLAUDE.md: {}", e);
                } else {
                    synced.push("CLAUDE.md".to_string());
                }
            } else {
                skipped.push("CLAUDE.md (already exists)".to_string());
            }
        }

        // Sync .claude/skills/ directory
        let source_skills_dir = self.skills_source_dir.join(".claude").join("skills");
        let target_skills_dir = project_path.join(".claude").join("skills");

        if source_skills_dir.exists() {
            // Ensure target directory exists
            if let Err(e) = fs::create_dir_all(&target_skills_dir) {
                info!("Failed to create skills dir: {}", e);
                return SyncResult { synced, skipped };
            }

            // Copy each skill file, skip if already exists
            self.copy_dir_recursive(&source_skills_dir, &target_skills_dir, &mut synced, &mut skipped);
        }

        info!(
            "Synced to {}: {} files synced, {} skipped",
            project_path.display(),
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
