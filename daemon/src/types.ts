// ─── RPC Protocol Types ───

export interface RpcRequest {
  method: string;
  params?: Record<string, unknown>;
  id?: string;
}

export interface RpcResponse {
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
  id?: string;
}

// ─── Session Types ───

export type SessionStatus = "idle" | "busy" | "error" | "destroyed";

export type PermissionMode = "auto" | "code" | "plan" | "ask";

/**
 * Map our mode names to Claude CLI permission flags
 * auto → bypassPermissions (--dangerously-skip-permissions)
 * code → acceptEdits
 * plan → plan (read-only)
 * ask  → default (all tools need confirmation)
 */
export function modeToCliFlag(mode: PermissionMode): string[] {
  switch (mode) {
    case "auto":
      return ["--dangerously-skip-permissions"];
    case "code":
      return []; // acceptEdits is SDK-level, CLI doesn't have direct flag
    case "plan":
      return []; // plan mode also SDK-level
    case "ask":
      return [];
    default:
      return ["--dangerously-skip-permissions"];
  }
}

export interface ManagedSession {
  sessionId: string;
  path: string;
  mode: PermissionMode;
  status: SessionStatus;
  sdkSessionId: string | null;
  createdAt: Date;
  lastActivityAt: Date;
}

export interface SessionInfo {
  sessionId: string;
  path: string;
  status: SessionStatus;
  mode: PermissionMode;
  sdkSessionId: string | null;
  model: string | null;
  createdAt: string;
  lastActivityAt: string;
}

// ─── Stream Event Types ───

export type StreamEventType =
  | "text"
  | "tool_use"
  | "tool_result"
  | "result"
  | "queued"
  | "error"
  | "system"
  | "partial"
  | "ping"
  | "interrupted";

export interface StreamEvent {
  type: StreamEventType;
  content?: string;
  tool?: string;
  input?: unknown;
  output?: unknown;
  session_id?: string;
  position?: number;
  message?: string;
  subtype?: string;
  model?: string;
  // Raw data from Claude CLI for passthrough
  raw?: unknown;
}

// ─── Message Queue Types ───

export interface QueuedUserMessage {
  message: string;
  timestamp: number;
}

export interface QueuedResponse {
  event: StreamEvent;
  timestamp: number;
}

// ─── RPC Method Params & Results ───

export interface CreateSessionParams {
  path: string;
  mode?: PermissionMode;
}

export interface CreateSessionResult {
  sessionId: string;
}

export interface SendMessageParams {
  sessionId: string;
  message: string;
}

export interface ResumeSessionParams {
  sessionId: string;
  sdkSessionId?: string;
}

export interface ResumeSessionResult {
  ok: boolean;
  fallback?: boolean;
  newSdkSessionId?: string;
}

export interface DestroySessionParams {
  sessionId: string;
}

export interface SetModeParams {
  sessionId: string;
  mode: PermissionMode;
}

export interface InterruptSessionParams {
  sessionId: string;
}

export interface HealthCheckResult {
  ok: boolean;
  sessions: number;
  sessionsByStatus: Record<string, number>;
  uptime: number;
  memory: {
    rss: number;
    heapUsed: number;
    heapTotal: number;
  };
  nodeVersion: string;
  pid: number;
}

export interface MonitorSessionDetail {
  sessionId: string;
  path: string;
  status: SessionStatus;
  mode: PermissionMode;
  model: string | null;
  sdkSessionId: string | null;
  createdAt: string;
  lastActivityAt: string;
  queue: {
    userPending: number;
    responsePending: number;
    clientConnected: boolean;
  };
}

export interface MonitorSessionsResult {
  sessions: MonitorSessionDetail[];
  totalSessions: number;
  uptime: number;
}

// ─── Claude CLI JSON-lines Protocol Types ───

// Note: ClaudeStdinMessage removed — we now use --print mode (per-message spawn)
// instead of stdin JSON-lines. Messages are passed as CLI arguments.

/**
 * Raw message types from Claude CLI stdout (stream-json format)
 * These are parsed and converted to StreamEvent for clients
 */
export interface ClaudeStdoutMessage {
  type: string;
  subtype?: string;
  session_id?: string;
  // assistant message
  message?: {
    role: string;
    content: Array<{
      type: string;
      text?: string;
      name?: string;
      input?: unknown;
      id?: string;
    }>;
  };
  // stream event
  event?: {
    type: string;
    index?: number;
    delta?: {
      type?: string;
      text?: string;
      partial_json?: string;
    };
    content_block?: {
      type: string;
      text?: string;
      name?: string;
      id?: string;
    };
  };
  // result
  duration_ms?: number;
  usage?: {
    input_tokens: number;
    output_tokens: number;
  };
  // tool progress
  tool_name?: string;
  status?: string;
}
