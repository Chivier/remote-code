#![allow(dead_code)]

mod auth;
mod config;
mod message_queue;
mod server;
mod session_pool;
mod skill_manager;
mod tls;
mod types;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Instant;

use tokio::net::TcpListener;
use tokio::sync::Notify;
use tracing::{error, info, warn};

use auth::TokenStore;
use config::DaemonConfig;
use server::AppState;
use session_pool::SessionPool;
use skill_manager::SkillManager;

#[tokio::main]
async fn main() {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    // Load config from ~/.codecast/daemon.yaml with env var overrides
    let config = DaemonConfig::load();
    let port = config.port;
    let host = config.bind.clone();

    let shutdown = Arc::new(Notify::new());
    let skill_manager = SkillManager::new();

    info!(
        "[Daemon] Skills source: {}",
        skill_manager.source_dir().display()
    );

    // Initialize token store for auth middleware
    let tokens_path = config.tokens_file.clone().unwrap_or_else(|| {
        dirs::home_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join(".codecast")
            .join("tokens.yaml")
    });
    let token_store = TokenStore::new(tokens_path);

    let state = Arc::new(AppState {
        session_pool: SessionPool::new(),
        skill_manager,
        start_time: Instant::now(),
        shutdown: shutdown.clone(),
        config,
        token_store,
    });

    let app = server::build_router(state.clone());

    // Try binding to port, incrementing on collision (up to port+100)
    let mut actual_port = port;
    let listener = loop {
        let addr: SocketAddr = format!("{}:{}", host, actual_port).parse().unwrap();
        match TcpListener::bind(addr).await {
            Ok(l) => break l,
            Err(e) => {
                warn!(
                    "Port {} in use ({}), trying {}",
                    actual_port,
                    e,
                    actual_port + 1
                );
                actual_port += 1;
                if actual_port > port + 100 {
                    error!("No available port in range {}..{}", port, port + 100);
                    std::process::exit(1);
                }
            }
        }
    };

    // Write actual port to file so the head node can discover it
    if let Some(home) = dirs::home_dir() {
        let port_file = home.join(".codecast").join("daemon.port");
        if let Some(parent) = port_file.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(&port_file, actual_port.to_string()).ok();
    }

    // Print to stdout for head node to parse during startup
    println!("DAEMON_PORT={}", actual_port);

    info!(
        "[Daemon] Codecast Daemon listening on {}:{}",
        host, actual_port
    );

    // Graceful shutdown on SIGTERM/SIGINT
    let state_for_shutdown = state.clone();
    let shutdown_signal = async move {
        let ctrl_c = tokio::signal::ctrl_c();

        #[cfg(unix)]
        {
            let mut sigterm =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                    .expect("Failed to install SIGTERM handler");

            tokio::select! {
                _ = ctrl_c => {
                    info!("\n[Daemon] Received SIGINT, shutting down gracefully...");
                }
                _ = sigterm.recv() => {
                    info!("\n[Daemon] Received SIGTERM, shutting down gracefully...");
                }
            }
        }

        #[cfg(not(unix))]
        {
            ctrl_c.await.ok();
            info!("\n[Daemon] Received SIGINT, shutting down gracefully...");
        }

        // Destroy all sessions
        state_for_shutdown.session_pool.destroy_all().await;
        info!("[Daemon] All sessions destroyed");

        // Clean up port file
        if let Some(home) = dirs::home_dir() {
            let port_file = home.join(".codecast").join("daemon.port");
            if std::fs::remove_file(&port_file).is_ok() {
                info!("[Daemon] Removed port file: {}", port_file.display());
            }
        }
    };

    if state.config.requires_auth() {
        // TLS mode: generate/load certificate and serve HTTPS
        let home = dirs::home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
        let codecast_dir = home.join(".codecast");
        let cert_path = state
            .config
            .tls_cert
            .clone()
            .unwrap_or_else(|| codecast_dir.join("tls-cert.pem"));
        let key_path = state
            .config
            .tls_key
            .clone()
            .unwrap_or_else(|| codecast_dir.join("tls-key.pem"));

        if let Err(e) = tls::ensure_tls_cert(&cert_path, &key_path) {
            error!("[TLS] Failed to ensure TLS certificate: {}", e);
            std::process::exit(1);
        }

        // Drop the plain TCP listener to free the port for axum-server
        let addr = listener.local_addr().unwrap();
        drop(listener);

        let tls_config =
            axum_server::tls_rustls::RustlsConfig::from_pem_file(&cert_path, &key_path)
                .await
                .unwrap_or_else(|e| {
                    error!("[TLS] Failed to load TLS config: {}", e);
                    std::process::exit(1);
                });

        info!("[Daemon] Serving HTTPS on {}", addr);

        let handle = axum_server::Handle::new();
        let handle_for_shutdown = handle.clone();

        // Spawn a task that triggers graceful shutdown when signal is received
        tokio::spawn(async move {
            shutdown_signal.await;
            handle_for_shutdown.graceful_shutdown(Some(std::time::Duration::from_secs(5)));
        });

        axum_server::bind_rustls(addr, tls_config)
            .handle(handle)
            .serve(app.into_make_service_with_connect_info::<SocketAddr>())
            .await
            .unwrap();
    } else {
        // Plain HTTP mode for localhost
        axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(shutdown_signal)
        .await
        .unwrap();
    }
}
