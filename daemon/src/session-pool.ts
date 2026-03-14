import { ChildProcess, spawn } from "child_process";
import { createInterface } from "readline";
import { v4 as uuidv4 } from "uuid";
import { existsSync } from "fs";
import {
  ManagedSession,
  SessionStatus,
  PermissionMode,
  StreamEvent,
  SessionInfo,
  ClaudeStdoutMessage,
  modeToCliFlag,
} from "./types";
import { MessageQueue } from "./message-queue";

/**
 * Session state — no longer holds a long-lived process.
 * Instead, each send() spawns a new `claude --print` process
 * and uses --resume to maintain conversation context.
 */
interface InternalSession extends ManagedSession {
  /** Currently running Claude process (only during message processing) */
  process: ChildProcess | null;
  queue: MessageQueue;
  /** Whether we're currently processing a message */
  processing: boolean;
  /** Model name reported by Claude CLI (extracted from init message) */
  model: string | null;
}

/**
 * SessionPool manages Claude CLI sessions using per-message spawn.
 *
 * Architecture change (2026-03-14):
 * - Claude CLI 2.1.76 does not support --input-format stream-json without --print
 * - We now spawn a fresh `claude --print <msg> --output-format stream-json` per message
 * - Session continuity is maintained via --resume <sdkSessionId>
 * - Each process lives only for the duration of one message exchange
 */
export class SessionPool {
  private sessions: Map<string, InternalSession> = new Map();

  /**
   * Create a new session (lightweight — just registers session state).
   * No Claude CLI process is spawned until a message is sent.
   */
  async create(path: string, mode: PermissionMode = "auto"): Promise<string> {
    // Validate path exists
    if (!existsSync(path)) {
      throw new Error(`Path does not exist: ${path}`);
    }

    const sessionId = uuidv4();

    console.log(`[SessionPool] Creating session ${sessionId} at ${path} (mode=${mode})`);

    const session: InternalSession = {
      sessionId,
      path,
      mode,
      status: "idle",
      sdkSessionId: null,
      createdAt: new Date(),
      lastActivityAt: new Date(),
      process: null,
      queue: new MessageQueue(),
      processing: false,
      model: null,
    };

    this.sessions.set(sessionId, session);
    return sessionId;
  }

  /**
   * Send a message to a session.
   * Spawns a new Claude CLI process for this message.
   * Returns an async iterable of stream events.
   */
  async *send(
    sessionId: string,
    message: string
  ): AsyncGenerator<StreamEvent, void, unknown> {
    const session = this.getSession(sessionId);

    // If Claude is busy, queue the message
    if (session.processing) {
      const position = session.queue.enqueueUser(message);
      yield { type: "queued", position };
      return;
    }

    // Process this message
    yield* this.processMessage(session, message);
  }

  /**
   * Internal: process a single message by spawning a Claude CLI process.
   *
   * Invocation: claude --print <message> --output-format stream-json --verbose
   *             [--resume <sdkSessionId>] [--dangerously-skip-permissions]
   */
  private async *processMessage(
    session: InternalSession,
    message: string
  ): AsyncGenerator<StreamEvent, void, unknown> {
    session.processing = true;
    session.status = "busy";
    session.lastActivityAt = new Date();

    // Build CLI arguments
    const args = [
      "--print",
      message,
      "--output-format",
      "stream-json",
      "--verbose",
      ...modeToCliFlag(session.mode),
    ];

    // Resume from previous conversation if we have a session ID
    if (session.sdkSessionId) {
      args.push("--resume", session.sdkSessionId);
    }

    console.log(`[SessionPool] Spawning claude for session ${session.sessionId}`);
    console.log(`[SessionPool] Command: claude ${args.map(a => a.includes(' ') ? `"${a}"` : a).join(" ")}`);
    console.log(`[SessionPool] CWD: ${session.path}`);

    // Spawn Claude CLI process for this single message
    const child = spawn("claude", args, {
      cwd: session.path,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        TERM: "dumb",
      },
    });

    session.process = child;

    // Event queue for streaming events from stdout
    const eventQueue: StreamEvent[] = [];
    let resolveWait: (() => void) | null = null;
    let done = false;

    const pushEvent = (event: StreamEvent) => {
      eventQueue.push(event);
      if (resolveWait) {
        resolveWait();
        resolveWait = null;
      }
    };

    // Set up stdout line reader (JSON-lines from Claude CLI)
    if (child.stdout) {
      const stdoutReader = createInterface({
        input: child.stdout,
        crlfDelay: Infinity,
      });

      stdoutReader.on("line", (line: string) => {
        let parsed: ClaudeStdoutMessage;
        try {
          parsed = JSON.parse(line);
        } catch {
          console.log(`[Session ${session.sessionId}] non-JSON stdout: ${line}`);
          return;
        }

        // Extract model name from system init message
        if (parsed.type === "system" && parsed.subtype === "init") {
          const raw = parsed as any;
          if (raw.model) {
            session.model = raw.model;
            console.log(`[Session ${session.sessionId}] Model: ${raw.model}`);
          }
        }

        const event = this.convertToStreamEvent(parsed);
        // Capture SDK session ID
        if (event.session_id) {
          session.sdkSessionId = event.session_id;
        }

        pushEvent(event);
      });
    }

    // Handle stderr
    if (child.stderr) {
      const stderrReader = createInterface({
        input: child.stderr,
        crlfDelay: Infinity,
      });
      stderrReader.on("line", (line: string) => {
        console.error(`[Session ${session.sessionId}] stderr: ${line}`);
      });
    }

    // Handle process exit
    child.on("exit", (code, signal) => {
      console.log(
        `[Session ${session.sessionId}] Process exited: code=${code}, signal=${signal}`
      );

      // Normal exit (code 0) after --print is expected — it means processing is done.
      // Only emit error for abnormal exits.
      if (code !== 0 && code !== null) {
        pushEvent({
          type: "error",
          message: `Claude process exited abnormally (code=${code}, signal=${signal})`,
        });
      }

      done = true;
      // Wake up the yield loop if it's waiting
      if (resolveWait) {
        resolveWait();
        resolveWait = null;
      }
    });

    child.on("error", (err) => {
      console.error(`[Session ${session.sessionId}] Process error:`, err);
      pushEvent({
        type: "error",
        message: `Claude process error: ${err.message}`,
      });
      done = true;
      if (resolveWait) {
        resolveWait();
        resolveWait = null;
      }
    });

    // Close stdin immediately — --print mode reads the prompt from args, not stdin
    if (child.stdin) {
      child.stdin.end();
    }

    try {
      // Yield events as they arrive
      while (true) {
        if (eventQueue.length > 0) {
          const event = eventQueue.shift()!;
          yield event;

          // Terminal events
          if (event.type === "result" || event.type === "error" || event.type === "interrupted") {
            break;
          }
        } else if (done) {
          // Process exited and queue is empty
          break;
        } else {
          // Wait for next event or process exit
          await new Promise<void>((resolve) => {
            resolveWait = resolve;
          });
        }
      }
    } finally {
      // Cleanup
      session.process = null;
      session.processing = false;
      session.status = "idle";

      // Kill process if still alive (e.g. on error/interrupt)
      if (child && !child.killed) {
        try {
          child.kill("SIGTERM");
          setTimeout(() => {
            if (!child.killed) child.kill("SIGKILL");
          }, 3000);
        } catch {
          // ignore
        }
      }

      // Process next queued message if any
      if (session.queue.hasUserPending() && session.status === "idle") {
        const next = session.queue.dequeueUser();
        if (next) {
          this.processQueuedMessage(session, next.message);
        }
      }
    }
  }

  /**
   * Process a queued message in the background.
   */
  private async processQueuedMessage(
    session: InternalSession,
    message: string
  ): Promise<void> {
    try {
      const gen = this.processMessage(session, message);
      for await (const event of gen) {
        if (!session.queue.clientConnected) {
          session.queue.bufferResponse(event);
        }
        // Otherwise events go to stream listeners (if any are attached via server SSE)
      }
    } catch (err) {
      console.error(
        `[Session ${session.sessionId}] Error processing queued message:`,
        err
      );
    }
  }

  /**
   * Convert Claude CLI stdout JSON to our StreamEvent format
   */
  private convertToStreamEvent(msg: ClaudeStdoutMessage): StreamEvent {
    switch (msg.type) {
      case "system":
        return {
          type: "system",
          subtype: msg.subtype,
          session_id: msg.session_id,
          model: (msg as any).model,
          raw: msg,
        };

      case "assistant":
        // Extract text content from assistant message
        if (msg.message?.content) {
          const textBlocks = msg.message.content.filter(
            (b) => b.type === "text"
          );
          const toolBlocks = msg.message.content.filter(
            (b) => b.type === "tool_use"
          );

          if (toolBlocks.length > 0) {
            return {
              type: "tool_use",
              tool: toolBlocks[0].name,
              input: toolBlocks[0].input,
              raw: msg,
            };
          }

          if (textBlocks.length > 0) {
            return {
              type: "text",
              content: textBlocks.map((b) => b.text).join(""),
              raw: msg,
            };
          }
        }
        return { type: "text", content: "", raw: msg };

      case "stream_event":
        // Handle streaming deltas
        if (msg.event?.type === "content_block_delta") {
          if (msg.event.delta?.text) {
            return {
              type: "partial",
              content: msg.event.delta.text,
            };
          }
          if (msg.event.delta?.partial_json) {
            return {
              type: "partial",
              content: msg.event.delta.partial_json,
            };
          }
        }
        if (msg.event?.type === "content_block_start") {
          if (msg.event.content_block?.type === "tool_use") {
            return {
              type: "tool_use",
              tool: msg.event.content_block.name,
              raw: msg,
            };
          }
        }
        return { type: "partial", content: "", raw: msg };

      case "tool_progress":
        return {
          type: "tool_use",
          tool: msg.tool_name,
          message: msg.status,
          raw: msg,
        };

      case "result":
        return {
          type: "result",
          session_id: msg.session_id,
          raw: msg,
        };

      default:
        return { type: "system", raw: msg };
    }
  }

  /**
   * Resume a session — in per-message mode, just update the sdkSessionId.
   * The next send() will use --resume with this ID.
   */
  async resume(
    sessionId: string,
    sdkSessionId?: string
  ): Promise<{ ok: boolean; fallback: boolean; newSessionId?: string }> {
    const session = this.sessions.get(sessionId);

    if (session) {
      if (sdkSessionId) {
        session.sdkSessionId = sdkSessionId;
      }
      session.queue.onClientReconnect();
      return { ok: true, fallback: false };
    }

    return { ok: false, fallback: false };
  }

  /**
   * Destroy a session: kill any running Claude process and clean up
   */
  async destroy(sessionId: string): Promise<boolean> {
    const session = this.sessions.get(sessionId);
    if (!session) return false;

    // Kill any running process
    if (session.process && !session.process.killed) {
      session.process.kill("SIGTERM");
      setTimeout(() => {
        if (session.process && !session.process.killed) {
          session.process.kill("SIGKILL");
        }
      }, 5000);
    }

    session.status = "destroyed";
    session.queue.clear();

    this.sessions.delete(sessionId);
    console.log(`[SessionPool] Destroyed session ${sessionId}`);
    return true;
  }

  /**
   * Set the permission mode for a session.
   * In per-message mode, just update the mode — next spawn will use it.
   */
  async setMode(
    sessionId: string,
    mode: PermissionMode
  ): Promise<boolean> {
    const session = this.getSession(sessionId);
    session.mode = mode;
    console.log(`[SessionPool] Mode changed to ${mode} for session ${sessionId}`);
    return true;
  }

  /**
   * Interrupt the current Claude operation for a session.
   * Sends SIGINT to the running Claude CLI process.
   */
  interrupt(sessionId: string): boolean {
    const session = this.sessions.get(sessionId);
    if (!session) {
      throw new Error(`Session not found: ${sessionId}`);
    }

    if (!session.processing || !session.process || session.process.killed) {
      return false;
    }

    console.log(`[SessionPool] Interrupting session ${sessionId}`);
    session.process.kill("SIGTERM"); // Kill the --print process
    session.queue.clear();

    return true;
  }

  /**
   * Get session info
   */
  getSessionInfo(sessionId: string): SessionInfo {
    const session = this.getSession(sessionId);
    return {
      sessionId: session.sessionId,
      path: session.path,
      status: session.status,
      mode: session.mode,
      sdkSessionId: session.sdkSessionId,
      model: session.model,
      createdAt: session.createdAt.toISOString(),
      lastActivityAt: session.lastActivityAt.toISOString(),
    };
  }

  /**
   * List all sessions
   */
  listSessions(): SessionInfo[] {
    return Array.from(this.sessions.values()).map((s) => ({
      sessionId: s.sessionId,
      path: s.path,
      status: s.status,
      mode: s.mode,
      sdkSessionId: s.sdkSessionId,
      model: s.model,
      createdAt: s.createdAt.toISOString(),
      lastActivityAt: s.lastActivityAt.toISOString(),
    }));
  }

  /**
   * Mark client as disconnected for a session (for MQ buffering)
   */
  clientDisconnect(sessionId: string): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.queue.onClientDisconnect();
    }
  }

  /**
   * Buffer a single event for a session
   */
  bufferEvent(sessionId: string, event: StreamEvent): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.queue.bufferResponse(event, true);
    }
  }

  /**
   * Mark client as reconnected, return buffered events
   */
  clientReconnect(sessionId: string): StreamEvent[] {
    const session = this.sessions.get(sessionId);
    if (session) {
      return session.queue.onClientReconnect();
    }
    return [];
  }

  /**
   * Get queue stats for a session
   */
  getQueueStats(
    sessionId: string
  ): { userPending: number; responsePending: number; clientConnected: boolean } | null {
    const session = this.sessions.get(sessionId);
    if (!session) return null;
    return session.queue.stats();
  }

  /**
   * Destroy all sessions (cleanup on shutdown)
   */
  async destroyAll(): Promise<void> {
    const sessionIds = Array.from(this.sessions.keys());
    await Promise.all(sessionIds.map((id) => this.destroy(id)));
  }

  /**
   * Get a session by ID, throwing if not found
   */
  private getSession(sessionId: string): InternalSession {
    const session = this.sessions.get(sessionId);
    if (!session) {
      throw new Error(`Session not found: ${sessionId}`);
    }
    return session;
  }
}
