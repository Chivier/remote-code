use std::sync::Arc;
use std::time::Instant;

use axum::extract::State;
use axum::http::{HeaderValue, StatusCode};
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Json};
use axum::routing::post;
use axum::Router;
use serde_json::json;
use tokio::sync::Notify;
use tokio_stream::wrappers::ReceiverStream;
use tracing::info;

use crate::session_pool::SessionPool;
use crate::skill_manager::SkillManager;
use crate::types::{PermissionMode, RpcRequest, RpcResponse};

/// Shared application state
pub struct AppState {
    pub session_pool: SessionPool,
    pub skill_manager: SkillManager,
    pub start_time: Instant,
    pub shutdown: Arc<Notify>,
}

/// Build the axum router
pub fn build_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/rpc", post(handle_rpc))
        .with_state(state)
}

/// Main RPC handler — dispatches to method-specific handlers
async fn handle_rpc(
    State(state): State<Arc<AppState>>,
    Json(req): Json<RpcRequest>,
) -> impl IntoResponse {
    let method = match &req.method {
        Some(m) => m.clone(),
        None => {
            return RpcJsonResponse(RpcResponse::error(
                -32600,
                "Invalid request: missing method",
                req.id,
            ))
            .into_response();
        }
    };

    let result = match method.as_str() {
        "session.create" => handle_create_session(&state, &req).await,
        "session.send" => {
            // SSE response — handled differently
            return handle_send_message(state, &req).await;
        }
        "session.resume" => handle_resume_session(&state, &req).await,
        "session.destroy" => handle_destroy_session(&state, &req).await,
        "session.list" => handle_list_sessions(&state, &req).await,
        "session.set_mode" => handle_set_mode(&state, &req).await,
        "session.interrupt" => handle_interrupt_session(&state, &req).await,
        "session.queue_stats" => handle_queue_stats(&state, &req).await,
        "session.reconnect" => handle_reconnect(&state, &req).await,
        "health.check" => handle_health_check(&state, &req).await,
        "monitor.sessions" => handle_monitor_sessions(&state, &req).await,
        _ => Ok(RpcResponse::error(
            -32601,
            format!("Method not found: {}", method),
            req.id.clone(),
        )),
    };

    match result {
        Ok(resp) => RpcJsonResponse(resp).into_response(),
        Err(e) => RpcJsonResponse(RpcResponse::error(-32000, e, req.id.clone())).into_response(),
    }
}

// ─── Method Handlers ───

async fn handle_create_session(
    state: &AppState,
    req: &RpcRequest,
) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or("Missing required param: path")?;

    let mode: PermissionMode = params
        .get("mode")
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();

    // Expand ~ to home directory
    let project_path = expand_tilde(path);

    // Sync skills before creating session
    let skill_result = state
        .skill_manager
        .sync_to_project(std::path::Path::new(&project_path));
    info!("[RPC] Skills synced: {} files", skill_result.synced.len());

    let session_id = state
        .session_pool
        .create(&project_path, mode)
        .await
        .map_err(|e| e.to_string())?;

    Ok(RpcResponse::success(
        json!({ "sessionId": session_id }),
        req.id.clone(),
    ))
}

async fn handle_send_message(
    state: Arc<AppState>,
    req: &RpcRequest,
) -> axum::response::Response {
    let params = match req.params.as_ref() {
        Some(p) => p,
        None => {
            return RpcJsonResponse(RpcResponse::error(
                -32602,
                "Missing required params: sessionId, message",
                req.id.clone(),
            ))
            .into_response();
        }
    };

    let session_id = match params.get("sessionId").and_then(|v| v.as_str()) {
        Some(s) => s.to_string(),
        None => {
            return RpcJsonResponse(RpcResponse::error(
                -32602,
                "Missing required params: sessionId, message",
                req.id.clone(),
            ))
            .into_response();
        }
    };

    let message = match params.get("message").and_then(|v| v.as_str()) {
        Some(m) => m.to_string(),
        None => {
            return RpcJsonResponse(RpcResponse::error(
                -32602,
                "Missing required params: sessionId, message",
                req.id.clone(),
            ))
            .into_response();
        }
    };

    // Get the event receiver from session pool
    let rx = match state.session_pool.send(&session_id, &message).await {
        Ok(rx) => rx,
        Err(e) => {
            // Return error as SSE (matches TypeScript behavior)
            let (tx, rx) =
                tokio::sync::mpsc::channel::<Result<Event, std::convert::Infallible>>(2);
            let _ = tx
                .send(Ok(Event::default().data(
                    serde_json::to_string(&json!({"type": "error", "message": e})).unwrap(),
                )))
                .await;
            let _ = tx.send(Ok(Event::default().data("[DONE]"))).await;
            let stream = ReceiverStream::new(rx);
            return sse_response(Sse::new(stream));
        }
    };

    // Convert the event receiver into an SSE stream with client disconnect detection.
    // When the client disconnects, we buffer remaining events for reconnect.
    let session_id_for_stream = session_id.clone();
    let state_for_stream = state.clone();

    let (sse_tx, sse_rx) =
        tokio::sync::mpsc::channel::<Result<Event, std::convert::Infallible>>(256);

    // Spawn a task that reads events from the session pool and forwards to the SSE channel.
    // When the sse_tx.send() fails, it means the client disconnected — we buffer remaining events.
    tokio::spawn(async move {
        let mut rx = rx;
        let mut client_disconnected = false;

        // Send keepalive pings every 30s in a separate task
        let sse_tx_for_ping = sse_tx.clone();
        let ping_task = tokio::spawn(async move {
            let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
            loop {
                interval.tick().await;
                let ping_data =
                    serde_json::to_string(&json!({"type": "ping"})).unwrap();
                if sse_tx_for_ping
                    .send(Ok(Event::default().data(ping_data)))
                    .await
                    .is_err()
                {
                    break; // Client disconnected
                }
            }
        });

        while let Some(event) = rx.recv().await {
            if client_disconnected {
                // Client is gone — buffer remaining events for reconnect
                state_for_stream
                    .session_pool
                    .buffer_event(&session_id_for_stream, event)
                    .await;
                continue;
            }

            let data =
                serde_json::to_string(&event).unwrap_or_else(|_| "{}".to_string());
            if sse_tx.send(Ok(Event::default().data(data))).await.is_err() {
                // Client disconnected — mark and buffer this event
                client_disconnected = true;
                info!(
                    "[RPC] Client disconnected from SSE stream for session {}",
                    session_id_for_stream
                );
                state_for_stream
                    .session_pool
                    .client_disconnect(&session_id_for_stream)
                    .await;
                state_for_stream
                    .session_pool
                    .buffer_event(&session_id_for_stream, event)
                    .await;
            }
        }

        // Stream ended — send [DONE] if client still connected
        if !client_disconnected {
            let _ = sse_tx.send(Ok(Event::default().data("[DONE]"))).await;
        }

        // Stop the ping task
        ping_task.abort();
    });

    let stream = ReceiverStream::new(sse_rx);
    sse_response(Sse::new(stream))
}

/// Wrap an SSE response with required headers (X-Accel-Buffering, Cache-Control, Connection).
fn sse_response<S>(sse: Sse<S>) -> axum::response::Response
where
    S: futures_core::Stream<Item = Result<Event, std::convert::Infallible>> + Send + 'static,
{
    let mut response = sse.into_response();
    // Disable nginx buffering — critical for SSE streaming through reverse proxies
    response.headers_mut().insert(
        "X-Accel-Buffering",
        HeaderValue::from_static("no"),
    );
    response.headers_mut().insert(
        "Cache-Control",
        HeaderValue::from_static("no-cache"),
    );
    response
}

async fn handle_resume_session(
    state: &AppState,
    req: &RpcRequest,
) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let session_id = params
        .get("sessionId")
        .and_then(|v| v.as_str())
        .ok_or("Missing required param: sessionId")?;

    let sdk_session_id = params
        .get("sdkSessionId")
        .and_then(|v| v.as_str())
        .map(String::from);

    let result = state
        .session_pool
        .resume(session_id, sdk_session_id)
        .await;

    Ok(RpcResponse::success(result, req.id.clone()))
}

async fn handle_destroy_session(
    state: &AppState,
    req: &RpcRequest,
) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let session_id = params
        .get("sessionId")
        .and_then(|v| v.as_str())
        .ok_or("Missing required param: sessionId")?;

    let ok = state.session_pool.destroy(session_id).await;
    Ok(RpcResponse::success(json!({ "ok": ok }), req.id.clone()))
}

async fn handle_list_sessions(
    state: &AppState,
    req: &RpcRequest,
) -> Result<RpcResponse, String> {
    let sessions = state.session_pool.list_sessions().await;
    Ok(RpcResponse::success(
        json!({ "sessions": sessions }),
        req.id.clone(),
    ))
}

async fn handle_set_mode(state: &AppState, req: &RpcRequest) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let session_id = params
        .get("sessionId")
        .and_then(|v| v.as_str())
        .ok_or("Missing required params: sessionId, mode")?;

    let mode: PermissionMode = params
        .get("mode")
        .ok_or("Missing required params: sessionId, mode")
        .and_then(|v| serde_json::from_value(v.clone()).map_err(|_| "Invalid mode"))?;

    let ok = state.session_pool.set_mode(session_id, mode).await?;
    Ok(RpcResponse::success(json!({ "ok": ok }), req.id.clone()))
}

async fn handle_interrupt_session(
    state: &AppState,
    req: &RpcRequest,
) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let session_id = params
        .get("sessionId")
        .and_then(|v| v.as_str())
        .ok_or("Missing required param: sessionId")?;

    let interrupted = state.session_pool.interrupt(session_id).await?;
    Ok(RpcResponse::success(
        json!({ "ok": true, "interrupted": interrupted }),
        req.id.clone(),
    ))
}

async fn handle_queue_stats(state: &AppState, req: &RpcRequest) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let session_id = params
        .get("sessionId")
        .and_then(|v| v.as_str())
        .ok_or("Missing required param: sessionId")?;

    match state.session_pool.get_queue_stats(session_id).await {
        Some(stats) => Ok(RpcResponse::success(
            serde_json::to_value(stats).unwrap(),
            req.id.clone(),
        )),
        None => Ok(RpcResponse::error(
            -32000,
            "Session not found",
            req.id.clone(),
        )),
    }
}

async fn handle_reconnect(state: &AppState, req: &RpcRequest) -> Result<RpcResponse, String> {
    let params = req.params.as_ref().ok_or("Missing params")?;
    let session_id = params
        .get("sessionId")
        .and_then(|v| v.as_str())
        .ok_or("Missing required param: sessionId")?;

    let buffered = state.session_pool.client_reconnect(session_id).await;
    Ok(RpcResponse::success(
        json!({ "bufferedEvents": buffered }),
        req.id.clone(),
    ))
}

async fn handle_health_check(state: &AppState, req: &RpcRequest) -> Result<RpcResponse, String> {
    let sessions = state.session_pool.list_sessions().await;

    // Summarize session states
    let mut status_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    for s in &sessions {
        let status_str = serde_json::to_value(&s.status)
            .ok()
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or_else(|| "unknown".to_string());
        *status_counts.entry(status_str).or_insert(0) += 1;
    }

    let uptime = state.start_time.elapsed().as_secs();

    // Get memory info (RSS from /proc/self/statm on Linux)
    let (rss_mb, heap_used_mb, heap_total_mb) = get_memory_usage();

    Ok(RpcResponse::success(
        json!({
            "ok": true,
            "sessions": sessions.len(),
            "sessionsByStatus": status_counts,
            "uptime": uptime,
            "memory": {
                "rss": rss_mb,
                "heapUsed": heap_used_mb,
                "heapTotal": heap_total_mb,
            },
            "nodeVersion": "rust",
            "pid": std::process::id(),
        }),
        req.id.clone(),
    ))
}

async fn handle_monitor_sessions(
    state: &AppState,
    req: &RpcRequest,
) -> Result<RpcResponse, String> {
    let sessions = state.session_pool.list_sessions().await;
    let mut detailed = Vec::new();

    for s in &sessions {
        let queue_stats = state
            .session_pool
            .get_queue_stats(&s.session_id)
            .await
            .unwrap_or(crate::types::QueueStats {
                user_pending: 0,
                response_pending: 0,
                client_connected: false,
            });

        detailed.push(json!({
            "sessionId": s.session_id,
            "path": s.path,
            "status": s.status,
            "mode": s.mode,
            "model": s.model,
            "sdkSessionId": s.sdk_session_id,
            "createdAt": s.created_at,
            "lastActivityAt": s.last_activity_at,
            "queue": queue_stats,
        }));
    }

    let uptime = state.start_time.elapsed().as_secs();

    Ok(RpcResponse::success(
        json!({
            "sessions": detailed,
            "totalSessions": sessions.len(),
            "uptime": uptime,
        }),
        req.id.clone(),
    ))
}

// ─── Helpers ───

/// Wrapper to return RpcResponse as JSON with proper content-type
struct RpcJsonResponse(RpcResponse);

impl IntoResponse for RpcJsonResponse {
    fn into_response(self) -> axum::response::Response {
        (StatusCode::OK, Json(self.0)).into_response()
    }
}

/// Expand ~ to home directory
fn expand_tilde(path: &str) -> String {
    if path.starts_with("~/") || path == "~" {
        if let Some(home) = dirs::home_dir() {
            return path.replacen('~', &home.to_string_lossy(), 1);
        }
    }
    path.to_string()
}

/// Get memory usage in MB (rss, heap_used, heap_total)
fn get_memory_usage() -> (u64, u64, u64) {
    // On Linux, read from /proc/self/statm
    #[cfg(target_os = "linux")]
    {
        if let Ok(statm) = std::fs::read_to_string("/proc/self/statm") {
            let parts: Vec<&str> = statm.split_whitespace().collect();
            if parts.len() >= 2 {
                let page_size = 4096u64; // typical page size
                let rss_pages: u64 = parts[1].parse().unwrap_or(0);
                let rss_mb = (rss_pages * page_size) / (1024 * 1024);
                return (rss_mb, rss_mb / 2, rss_mb); // approximate heap from RSS
            }
        }
    }

    // Fallback for non-Linux
    (0, 0, 0)
}
