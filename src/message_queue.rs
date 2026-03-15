use std::collections::VecDeque;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::types::StreamEvent;

/// Queued user message waiting to be sent to Claude
#[derive(Debug)]
pub struct QueuedUserMessage {
    pub message: String,
    pub timestamp: u64,
}

/// Buffered response event (for client disconnect recovery)
#[derive(Debug)]
struct QueuedResponse {
    event: StreamEvent,
    #[allow(dead_code)]
    timestamp: u64,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

/// Per-session message queue with three responsibilities:
/// 1. Buffer user messages when Claude is busy
/// 2. Buffer responses when SSH connection is down
/// 3. Track queue state for scheduling
pub struct MessageQueue {
    user_pending: VecDeque<QueuedUserMessage>,
    response_pending: VecDeque<QueuedResponse>,
    client_connected: bool,
}

impl MessageQueue {
    pub fn new() -> Self {
        Self {
            user_pending: VecDeque::new(),
            response_pending: VecDeque::new(),
            client_connected: true,
        }
    }

    // ─── User Message Buffering ───

    /// Enqueue a user message (when Claude is busy processing).
    /// Returns the new queue length (used as position in queued event).
    pub fn enqueue_user(&mut self, message: String) -> usize {
        self.user_pending.push_back(QueuedUserMessage {
            message,
            timestamp: now_ms(),
        });
        self.user_pending.len()
    }

    /// Dequeue next user message to send to Claude
    pub fn dequeue_user(&mut self) -> Option<QueuedUserMessage> {
        self.user_pending.pop_front()
    }

    /// Check if there are pending user messages
    pub fn has_user_pending(&self) -> bool {
        !self.user_pending.is_empty()
    }

    // ─── Response Buffering (for SSH disconnect recovery) ───

    /// Buffer a response event when client is disconnected.
    /// Can be called with force=true to always buffer (e.g., when
    /// server detects client disconnect mid-stream).
    pub fn buffer_response(&mut self, event: StreamEvent, force: bool) {
        if force || !self.client_connected {
            self.response_pending.push_back(QueuedResponse {
                event,
                timestamp: now_ms(),
            });
        }
    }

    // ─── Client Connection State ───

    pub fn is_client_connected(&self) -> bool {
        self.client_connected
    }

    /// Mark client as disconnected - responses will be buffered
    pub fn on_client_disconnect(&mut self) {
        self.client_connected = false;
    }

    /// Mark client as reconnected - return buffered responses
    pub fn on_client_reconnect(&mut self) -> Vec<StreamEvent> {
        self.client_connected = true;
        self.replay_responses()
    }

    /// Drain and return all buffered response events
    fn replay_responses(&mut self) -> Vec<StreamEvent> {
        self.response_pending
            .drain(..)
            .map(|r| r.event)
            .collect()
    }

    // ─── Cleanup ───

    /// Clear all queues
    pub fn clear(&mut self) {
        self.user_pending.clear();
        self.response_pending.clear();
    }

    /// Get queue stats
    pub fn stats(&self) -> crate::types::QueueStats {
        crate::types::QueueStats {
            user_pending: self.user_pending.len(),
            response_pending: self.response_pending.len(),
            client_connected: self.client_connected,
        }
    }
}
