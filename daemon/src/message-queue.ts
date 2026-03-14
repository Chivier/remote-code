import { QueuedUserMessage, QueuedResponse, StreamEvent } from "./types";

/**
 * MessageQueue - per-session message queue with three responsibilities:
 * 1. Buffer user messages when Claude is busy
 * 2. Buffer responses when SSH connection is down
 * 3. Track queue state for scheduling
 */
export class MessageQueue {
  private userPending: QueuedUserMessage[] = [];
  private responsePending: QueuedResponse[] = [];
  private _clientConnected: boolean = true;

  // ─── User Message Buffering ───

  /**
   * Enqueue a user message (when Claude is busy processing)
   */
  enqueueUser(message: string): number {
    this.userPending.push({ message, timestamp: Date.now() });
    return this.userPending.length;
  }

  /**
   * Dequeue next user message to send to Claude
   */
  dequeueUser(): QueuedUserMessage | null {
    return this.userPending.shift() ?? null;
  }

  /**
   * Check if there are pending user messages
   */
  hasUserPending(): boolean {
    return this.userPending.length > 0;
  }

  /**
   * Number of pending user messages
   */
  get userQueueLength(): number {
    return this.userPending.length;
  }

  // ─── Response Buffering (for SSH disconnect recovery) ───

  /**
   * Buffer a response event when client is disconnected.
   * Can be called with force=true to always buffer (e.g., from server.ts
   * when it detects client disconnect mid-stream).
   */
  bufferResponse(event: StreamEvent, force: boolean = false): void {
    if (force || !this._clientConnected) {
      this.responsePending.push({ event, timestamp: Date.now() });
    }
  }

  /**
   * Replay all buffered responses on reconnect
   */
  replayResponses(): StreamEvent[] {
    const events = this.responsePending.map((r) => r.event);
    this.responsePending = [];
    return events;
  }

  /**
   * Check if there are buffered responses
   */
  hasResponsesPending(): boolean {
    return this.responsePending.length > 0;
  }

  // ─── Client Connection State ───

  get clientConnected(): boolean {
    return this._clientConnected;
  }

  /**
   * Mark client as disconnected - responses will be buffered
   */
  onClientDisconnect(): void {
    this._clientConnected = false;
  }

  /**
   * Mark client as reconnected - return buffered responses
   */
  onClientReconnect(): StreamEvent[] {
    this._clientConnected = true;
    return this.replayResponses();
  }

  // ─── Cleanup ───

  /**
   * Clear all queues
   */
  clear(): void {
    this.userPending = [];
    this.responsePending = [];
  }

  /**
   * Get queue stats for debugging
   */
  stats(): { userPending: number; responsePending: number; clientConnected: boolean } {
    return {
      userPending: this.userPending.length,
      responsePending: this.responsePending.length,
      clientConnected: this._clientConnected,
    };
  }
}
