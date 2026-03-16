#![allow(dead_code)]

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
use tracing::info;

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

    // Parse port from environment
    let port: u16 = std::env::var("DAEMON_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(9100);

    let host = "127.0.0.1"; // Only bind to localhost (accessed via SSH tunnel)

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
    });

    let app = server::build_router(state.clone());

    let addr: SocketAddr = format!("{}:{}", host, port).parse().unwrap();
    let listener = TcpListener::bind(addr).await.unwrap();

    info!("[Daemon] Remote Code Daemon listening on {}:{}", host, port);

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
