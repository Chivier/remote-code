use std::path::PathBuf;

use serde::Deserialize;
use tracing::info;

/// Daemon configuration loaded from ~/.codecast/daemon.yaml with env var overrides.
#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct DaemonConfig {
    /// TCP port to listen on (default: 9100, override: DAEMON_PORT)
    pub port: u16,
    /// Bind address (default: "127.0.0.1", override: DAEMON_BIND)
    pub bind: String,
    /// Path to the tokens file for bearer-token auth
    pub tokens_file: Option<PathBuf>,
    /// Path to TLS certificate (PEM)
    pub tls_cert: Option<PathBuf>,
    /// Path to TLS private key (PEM)
    pub tls_key: Option<PathBuf>,
}

impl Default for DaemonConfig {
    fn default() -> Self {
        Self {
            port: 9100,
            bind: "127.0.0.1".to_string(),
            tokens_file: None,
            tls_cert: None,
            tls_key: None,
        }
    }
}

impl DaemonConfig {
    /// Load config from ~/.codecast/daemon.yaml, falling back to defaults.
    /// Environment variables DAEMON_PORT and DAEMON_BIND override file values.
    pub fn load() -> Self {
        let mut config = Self::load_from_file();

        // Environment variable overrides
        if let Some(port) = std::env::var("DAEMON_PORT")
            .ok()
            .and_then(|p| p.parse::<u16>().ok())
        {
            config.port = port;
        }

        if let Ok(bind) = std::env::var("DAEMON_BIND") {
            if !bind.is_empty() {
                config.bind = bind;
            }
        }

        info!(
            "[Config] bind={}, port={}, auth={}",
            config.bind,
            config.port,
            config.requires_auth()
        );

        config
    }

    /// Load from the YAML file, returning defaults if the file doesn't exist or is invalid.
    fn load_from_file() -> Self {
        let path = Self::config_path();
        match std::fs::read_to_string(&path) {
            Ok(contents) => match serde_yaml::from_str::<DaemonConfig>(&contents) {
                Ok(config) => {
                    info!("[Config] Loaded from {}", path.display());
                    config
                }
                Err(e) => {
                    tracing::warn!(
                        "[Config] Failed to parse {}: {}, using defaults",
                        path.display(),
                        e
                    );
                    Self::default()
                }
            },
            Err(_) => {
                info!(
                    "[Config] No config file at {}, using defaults",
                    path.display()
                );
                Self::default()
            }
        }
    }

    /// Returns true when the daemon is exposed beyond localhost and should require auth.
    pub fn requires_auth(&self) -> bool {
        self.bind != "127.0.0.1" && self.bind != "localhost" && self.bind != "::1"
    }

    /// Path to the config file: ~/.codecast/daemon.yaml
    fn config_path() -> PathBuf {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".codecast")
            .join("daemon.yaml")
    }
}
