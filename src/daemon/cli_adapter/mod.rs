//! CLI Adapter trait and factory for multi-CLI backend support.
//!
//! Each CLI (Claude, Codex, Gemini, OpenCode) implements the `CliAdapter` trait.
//! A fresh adapter instance is created per `run_cli_process()` call via `create_adapter()`,
//! ensuring stateful adapters (cumulative text tracking) reset between turns.

pub mod claude;
pub mod codex;
pub mod gemini;
pub mod opencode;

use std::path::Path;

use tokio::process::Command;

use crate::types::{PermissionMode, StreamEvent};

/// CliAdapter is NOT stored per-session. A fresh instance is created at the
/// start of each `run_cli_process()` call via `create_adapter()`. This ensures
/// per-run state (e.g., cumulative text trackers) is always clean.
pub trait CliAdapter: Send + Sync {
    /// CLI name identifier ("claude", "codex", "gemini", "opencode")
    fn name(&self) -> &str;

    /// Build command for first execution
    fn build_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        model: Option<&str>,
    ) -> Command;

    /// Build command for session resume
    fn build_resume_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        session_id: &str,
        model: Option<&str>,
    ) -> Command;

    /// Parse one JSON-lines output line → Vec<StreamEvent>.
    /// Returns Vec because one line may produce multiple events (e.g., Claude
    /// assistant message with text + multiple tool_use blocks).
    /// For stateful adapters (e.g., cumulative text tracking),
    /// this method uses interior mutability (Mutex/Cell) to track per-run state.
    fn parse_output_line(&self, line: &str) -> Vec<StreamEvent>;

    /// Extract session/thread ID from output (called once on first message)
    fn extract_session_id(&self, line: &str) -> Option<String>;

    /// Instructions file name for skill sync
    fn instructions_file(&self) -> &str;

    /// Skills directory to sync (e.g., ".claude/skills/"), if any
    fn skills_dir(&self) -> Option<&str>;

    /// Log level for stderr output
    fn stderr_log_level(&self) -> tracing::Level;
}

/// Supported CLI types
pub const CLI_TYPES: &[&str] = &["claude", "codex", "gemini", "opencode"];

/// Validate a cli_type string
pub fn is_valid_cli_type(cli_type: &str) -> bool {
    CLI_TYPES.contains(&cli_type)
}

/// Create an adapter for the given CLI type.
/// Defaults to Claude if the type is unknown.
pub fn create_adapter(cli_type: &str) -> Box<dyn CliAdapter> {
    match cli_type {
        "codex" => Box::new(codex::CodexAdapter::new()),
        "gemini" => Box::new(gemini::GeminiAdapter::new()),
        "opencode" => Box::new(opencode::OpenCodeAdapter::new()),
        _ => Box::new(claude::ClaudeAdapter),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_adapter_factory() {
        assert_eq!(create_adapter("claude").name(), "claude");
        assert_eq!(create_adapter("codex").name(), "codex");
        assert_eq!(create_adapter("gemini").name(), "gemini");
        assert_eq!(create_adapter("opencode").name(), "opencode");
        // Unknown defaults to claude
        assert_eq!(create_adapter("unknown").name(), "claude");
        assert_eq!(create_adapter("").name(), "claude");
    }

    #[test]
    fn test_is_valid_cli_type() {
        assert!(is_valid_cli_type("claude"));
        assert!(is_valid_cli_type("codex"));
        assert!(is_valid_cli_type("gemini"));
        assert!(is_valid_cli_type("opencode"));
        assert!(!is_valid_cli_type("unknown"));
        assert!(!is_valid_cli_type(""));
    }

    #[test]
    fn test_instructions_files() {
        assert_eq!(create_adapter("claude").instructions_file(), "CLAUDE.md");
        assert_eq!(create_adapter("codex").instructions_file(), "AGENTS.md");
        assert_eq!(create_adapter("gemini").instructions_file(), "GEMINI.md");
        assert_eq!(create_adapter("opencode").instructions_file(), "AGENTS.md");
    }

    #[test]
    fn test_skills_dir() {
        assert_eq!(
            create_adapter("claude").skills_dir(),
            Some(".claude/skills/")
        );
        assert_eq!(create_adapter("codex").skills_dir(), None);
        assert_eq!(create_adapter("gemini").skills_dir(), None);
        assert_eq!(create_adapter("opencode").skills_dir(), None);
    }
}
