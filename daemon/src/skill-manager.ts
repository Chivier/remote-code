import { existsSync, mkdirSync, copyFileSync, readdirSync, statSync } from "fs";
import { join, relative } from "path";

/**
 * SkillManager handles syncing CLAUDE.md and .claude/skills/ to project directories.
 *
 * Skills are managed via a "skillshare" model:
 * - A central skills directory (synced from Head via scp)
 * - Skills are copied to project paths on session creation
 * - Existing project-specific skills are NOT overwritten
 */
export class SkillManager {
  private skillsSourceDir: string;

  constructor(skillsSourceDir: string = join(process.env.HOME || "~", ".remote-code", "skills")) {
    this.skillsSourceDir = skillsSourceDir;
  }

  /**
   * Sync skills from the shared source to a project directory
   */
  syncToProject(projectPath: string): { synced: string[]; skipped: string[] } {
    const synced: string[] = [];
    const skipped: string[] = [];

    if (!existsSync(this.skillsSourceDir)) {
      console.log(`[SkillManager] Skills source dir not found: ${this.skillsSourceDir}`);
      return { synced, skipped };
    }

    // Sync CLAUDE.md
    const sourceClaudeMd = join(this.skillsSourceDir, "CLAUDE.md");
    const targetClaudeMd = join(projectPath, "CLAUDE.md");

    if (existsSync(sourceClaudeMd)) {
      if (!existsSync(targetClaudeMd)) {
        copyFileSync(sourceClaudeMd, targetClaudeMd);
        synced.push("CLAUDE.md");
      } else {
        skipped.push("CLAUDE.md (already exists)");
      }
    }

    // Sync .claude/skills/ directory
    const sourceSkillsDir = join(this.skillsSourceDir, ".claude", "skills");
    const targetSkillsDir = join(projectPath, ".claude", "skills");

    if (existsSync(sourceSkillsDir)) {
      // Ensure target directory exists
      mkdirSync(targetSkillsDir, { recursive: true });

      // Copy each skill file, skip if already exists
      this.copyDirRecursive(sourceSkillsDir, targetSkillsDir, synced, skipped);
    }

    console.log(`[SkillManager] Synced to ${projectPath}: ${synced.length} files synced, ${skipped.length} skipped`);
    return { synced, skipped };
  }

  /**
   * List skills available in the shared source
   */
  listSharedSkills(): string[] {
    const skills: string[] = [];

    if (!existsSync(this.skillsSourceDir)) {
      return skills;
    }

    const claudeMd = join(this.skillsSourceDir, "CLAUDE.md");
    if (existsSync(claudeMd)) {
      skills.push("CLAUDE.md");
    }

    const skillsDir = join(this.skillsSourceDir, ".claude", "skills");
    if (existsSync(skillsDir)) {
      this.listFilesRecursive(skillsDir, skillsDir, skills);
    }

    return skills;
  }

  /**
   * List skills present in a project directory
   */
  listProjectSkills(projectPath: string): string[] {
    const skills: string[] = [];

    const claudeMd = join(projectPath, "CLAUDE.md");
    if (existsSync(claudeMd)) {
      skills.push("CLAUDE.md");
    }

    const skillsDir = join(projectPath, ".claude", "skills");
    if (existsSync(skillsDir)) {
      this.listFilesRecursive(skillsDir, skillsDir, skills);
    }

    return skills;
  }

  /**
   * Recursively copy directory contents, skipping existing files
   */
  private copyDirRecursive(
    sourceDir: string,
    targetDir: string,
    synced: string[],
    skipped: string[]
  ): void {
    const entries = readdirSync(sourceDir);

    for (const entry of entries) {
      const sourcePath = join(sourceDir, entry);
      const targetPath = join(targetDir, entry);
      const stat = statSync(sourcePath);

      if (stat.isDirectory()) {
        mkdirSync(targetPath, { recursive: true });
        this.copyDirRecursive(sourcePath, targetPath, synced, skipped);
      } else {
        const relativePath = relative(this.skillsSourceDir, sourcePath);
        if (!existsSync(targetPath)) {
          copyFileSync(sourcePath, targetPath);
          synced.push(relativePath);
        } else {
          skipped.push(`${relativePath} (already exists)`);
        }
      }
    }
  }

  /**
   * Recursively list files in a directory
   */
  private listFilesRecursive(dir: string, baseDir: string, result: string[]): void {
    const entries = readdirSync(dir);

    for (const entry of entries) {
      const fullPath = join(dir, entry);
      const stat = statSync(fullPath);

      if (stat.isDirectory()) {
        this.listFilesRecursive(fullPath, baseDir, result);
      } else {
        result.push(`.claude/skills/${relative(baseDir, fullPath)}`);
      }
    }
  }
}
