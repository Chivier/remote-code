#![allow(dead_code)]

mod config;
mod message_queue;
mod server;
mod session_pool;
mod skill_manager;
mod types;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Instant;

use tokio::net::TcpListener;
use tokio::sync::Notify;
use tracing::{error, info, warn};

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

    let state = Arc::new(AppState {
        session_pool: SessionPool::new(),
        skill_manager,
        start_time: Instant::now(),
        shutdown: shutdown.clone(),
        config,
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
    };

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal)
        .await
        .unwrap();
}
