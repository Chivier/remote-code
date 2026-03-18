use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{ConnectInfo, State};
use axum::http::{Request, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use serde::Deserialize;
use tokio::sync::RwLock;
use tracing::{info, warn};

use crate::server::AppState;

// ─── Token file schema ───

#[derive(Debug, Deserialize)]
pub struct TokenEntry {
    pub token: String,
    #[allow(dead_code)]
    pub label: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct TokensFile {
    pub tokens: Vec<TokenEntry>,
}

// ─── Token store ───

#[derive(Clone)]
pub struct TokenStore {
    tokens: Arc<RwLock<Vec<String>>>,
    file_path: PathBuf,
}

impl TokenStore {
    /// Create a new TokenStore, loading tokens from the given path.
    /// If the file doesn't exist, the store starts empty (all non-localhost requests will be rejected).
    pub fn new(path: PathBuf) -> Self {
        let store = Self {
            tokens: Arc::new(RwLock::new(Vec::new())),
            file_path: path,
        };
        // Load synchronously at startup
        store.reload_sync();
        store
    }

    /// Reload tokens from the YAML file (sync version for startup).
    fn reload_sync(&self) {
        match std::fs::read_to_string(&self.file_path) {
            Ok(contents) => match serde_yaml::from_str::<TokensFile>(&contents) {
                Ok(tf) => {
                    let count = tf.tokens.len();
                    let tokens: Vec<String> = tf.tokens.into_iter().map(|e| e.token).collect();
                    // Use try_write since we're the only user at startup
                    if let Ok(mut guard) = self.tokens.try_write() {
                        *guard = tokens;
                    }
                    info!(
                        "[Auth] Loaded {} token(s) from {}",
                        count,
                        self.file_path.display()
                    );
                }
                Err(e) => {
                    warn!("[Auth] Failed to parse {}: {}", self.file_path.display(), e);
                }
            },
            Err(_) => {
                info!(
                    "[Auth] No tokens file at {}, auth will reject non-localhost requests",
                    self.file_path.display()
                );
            }
        }
    }

    /// Reload tokens from the YAML file.
    pub async fn reload(&self) {
        match tokio::fs::read_to_string(&self.file_path).await {
            Ok(contents) => match serde_yaml::from_str::<TokensFile>(&contents) {
                Ok(tf) => {
                    let count = tf.tokens.len();
                    let tokens: Vec<String> = tf.tokens.into_iter().map(|e| e.token).collect();
                    let mut guard = self.tokens.write().await;
                    *guard = tokens;
                    info!(
                        "[Auth] Reloaded {} token(s) from {}",
                        count,
                        self.file_path.display()
                    );
                }
                Err(e) => {
                    warn!("[Auth] Failed to parse {}: {}", self.file_path.display(), e);
                }
            },
            Err(e) => {
                warn!("[Auth] Failed to read {}: {}", self.file_path.display(), e);
            }
        }
    }

    /// Check if a token is valid.
    pub async fn validate(&self, token: &str) -> bool {
        let guard = self.tokens.read().await;
        guard.iter().any(|t| t == token)
    }

    /// Returns true if no tokens are loaded.
    pub async fn is_empty(&self) -> bool {
        let guard = self.tokens.read().await;
        guard.is_empty()
    }
}

// ─── Auth middleware ───

/// Axum middleware that validates Bearer tokens.
/// - Skips auth for loopback (localhost) connections.
/// - Skips auth when config.requires_auth() is false.
/// - Extracts "Authorization: Bearer <token>" header and validates against TokenStore.
/// - Returns 401 Unauthorized on failure.
pub async fn auth_middleware(
    State(state): State<Arc<AppState>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    req: Request<Body>,
    next: Next,
) -> Response {
    // Skip auth for loopback connections
    if addr.ip().is_loopback() {
        return next.run(req).await;
    }

    // Skip auth if config doesn't require it (bound to localhost)
    if !state.config.requires_auth() {
        return next.run(req).await;
    }

    // Extract Bearer token from Authorization header
    let auth_header = req
        .headers()
        .get("authorization")
        .and_then(|v| v.to_str().ok());

    let token = match auth_header {
        Some(h) if h.starts_with("Bearer ") => &h[7..],
        _ => {
            warn!(
                "[Auth] Rejected request from {} — missing or malformed Authorization header",
                addr
            );
            return (
                StatusCode::UNAUTHORIZED,
                "Unauthorized: missing Bearer token",
            )
                .into_response();
        }
    };

    // Validate the token
    if !state.token_store.validate(token).await {
        warn!("[Auth] Rejected request from {} — invalid token", addr);
        return (StatusCode::UNAUTHORIZED, "Unauthorized: invalid token").into_response();
    }

    next.run(req).await
}
