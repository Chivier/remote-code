import express, { Request, Response } from "express";
import { homedir } from "os";
import { SessionPool } from "./session-pool";
import { SkillManager } from "./skill-manager";
import {
  RpcRequest,
  RpcResponse,
  CreateSessionParams,
  SendMessageParams,
  ResumeSessionParams,
  DestroySessionParams,
  SetModeParams,
  InterruptSessionParams,
  PermissionMode,
} from "./types";

const PORT = parseInt(process.env.DAEMON_PORT || "9100", 10);
const HOST = "127.0.0.1"; // Only bind to localhost (accessed via SSH tunnel)

const app = express();
app.use(express.json());

const sessionPool = new SessionPool();
const skillManager = new SkillManager();

const startTime = Date.now();

// ─── JSON-RPC Handler ───

app.post("/rpc", async (req: Request, res: Response) => {
  const rpcReq = req.body as RpcRequest;

  if (!rpcReq.method) {
    res.json(rpcError(-32600, "Invalid request: missing method", rpcReq.id));
    return;
  }

  try {
    switch (rpcReq.method) {
      case "session.create":
        await handleCreateSession(rpcReq, res);
        break;
      case "session.send":
        await handleSendMessage(rpcReq, res);
        break;
      case "session.resume":
        await handleResumeSession(rpcReq, res);
        break;
      case "session.destroy":
        await handleDestroySession(rpcReq, res);
        break;
      case "session.list":
        handleListSessions(rpcReq, res);
        break;
      case "session.set_mode":
        await handleSetMode(rpcReq, res);
        break;
      case "session.interrupt":
        handleInterruptSession(rpcReq, res);
        break;
      case "session.queue_stats":
        handleQueueStats(rpcReq, res);
        break;
      case "session.reconnect":
        handleReconnect(rpcReq, res);
        break;
      case "health.check":
        handleHealthCheck(rpcReq, res);
        break;
      case "monitor.sessions":
        handleMonitorSessions(rpcReq, res);
        break;
      default:
        res.json(rpcError(-32601, `Method not found: ${rpcReq.method}`, rpcReq.id));
    }
  } catch (err: any) {
    console.error(`[RPC] Error handling ${rpcReq.method}:`, err);
    res.json(rpcError(-32000, err.message || "Internal error", rpcReq.id));
  }
});

// ─── Method Handlers ───

async function handleCreateSession(rpcReq: RpcRequest, res: Response): Promise<void> {
  const params = rpcReq.params as unknown as CreateSessionParams;
  if (!params?.path) {
    res.json(rpcError(-32602, "Missing required param: path", rpcReq.id));
    return;
  }

  // Expand ~ to home directory
  let projectPath = params.path;
  if (projectPath.startsWith("~/") || projectPath === "~") {
    projectPath = projectPath.replace("~", homedir());
  }

  const mode = (params.mode || "auto") as PermissionMode;

  // Sync skills before creating session
  const skillResult = skillManager.syncToProject(projectPath);
  console.log(`[RPC] Skills synced: ${skillResult.synced.length} files`);

  const sessionId = await sessionPool.create(projectPath, mode);

  res.json(rpcSuccess({ sessionId }, rpcReq.id));
}

async function handleSendMessage(rpcReq: RpcRequest, res: Response): Promise<void> {
  const params = rpcReq.params as unknown as SendMessageParams;
  if (!params?.sessionId || !params?.message) {
    res.json(rpcError(-32602, "Missing required params: sessionId, message", rpcReq.id));
    return;
  }

  // Use SSE (Server-Sent Events) for streaming response
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no"); // Disable nginx buffering

  // Track if client disconnects mid-stream
  let clientDisconnected = false;
  res.on("close", () => {
    if (!clientDisconnected) {
      clientDisconnected = true;
      console.log(`[RPC] Client disconnected from SSE stream for session ${params.sessionId}`);
      sessionPool.clientDisconnect(params.sessionId);
    }
  });

  // Send keepalive pings every 30s to prevent idle timeouts
  const keepaliveInterval = setInterval(() => {
    if (clientDisconnected) {
      clearInterval(keepaliveInterval);
      return;
    }
    try {
      res.write(`data: ${JSON.stringify({ type: "ping" })}\n\n`);
      if (typeof (res as any).flush === "function") {
        (res as any).flush();
      }
    } catch {
      clearInterval(keepaliveInterval);
    }
  }, 30000);

  try {
    const stream = sessionPool.send(params.sessionId, params.message);

    for await (const event of stream) {
      if (clientDisconnected) {
        // Client is gone - buffer remaining events for reconnect
        sessionPool.bufferEvent(params.sessionId, event);
        continue;
      }

      try {
        res.write(`data: ${JSON.stringify(event)}\n\n`);

        // Flush if possible
        if (typeof (res as any).flush === "function") {
          (res as any).flush();
        }
      } catch {
        // Write failed - client likely disconnected
        clientDisconnected = true;
        sessionPool.clientDisconnect(params.sessionId);
        sessionPool.bufferEvent(params.sessionId, event);
      }
    }

    // End the SSE stream (only if client still connected)
    if (!clientDisconnected) {
      res.write("data: [DONE]\n\n");
      res.end();
    }
  } catch (err: any) {
    if (!clientDisconnected) {
      res.write(`data: ${JSON.stringify({ type: "error", message: err.message })}\n\n`);
      res.write("data: [DONE]\n\n");
      res.end();
    }
  } finally {
    clearInterval(keepaliveInterval);
  }
}

async function handleResumeSession(rpcReq: RpcRequest, res: Response): Promise<void> {
  const params = rpcReq.params as unknown as ResumeSessionParams;
  if (!params?.sessionId) {
    res.json(rpcError(-32602, "Missing required param: sessionId", rpcReq.id));
    return;
  }

  const result = await sessionPool.resume(params.sessionId, params.sdkSessionId);
  res.json(rpcSuccess(result, rpcReq.id));
}

async function handleDestroySession(rpcReq: RpcRequest, res: Response): Promise<void> {
  const params = rpcReq.params as unknown as DestroySessionParams;
  if (!params?.sessionId) {
    res.json(rpcError(-32602, "Missing required param: sessionId", rpcReq.id));
    return;
  }

  const ok = await sessionPool.destroy(params.sessionId);
  res.json(rpcSuccess({ ok }, rpcReq.id));
}

function handleListSessions(rpcReq: RpcRequest, res: Response): void {
  const sessions = sessionPool.listSessions();
  res.json(rpcSuccess({ sessions }, rpcReq.id));
}

async function handleSetMode(rpcReq: RpcRequest, res: Response): Promise<void> {
  const params = rpcReq.params as unknown as SetModeParams;
  if (!params?.sessionId || !params?.mode) {
    res.json(rpcError(-32602, "Missing required params: sessionId, mode", rpcReq.id));
    return;
  }

  const ok = await sessionPool.setMode(params.sessionId, params.mode);
  res.json(rpcSuccess({ ok }, rpcReq.id));
}

function handleInterruptSession(rpcReq: RpcRequest, res: Response): void {
  const params = rpcReq.params as unknown as InterruptSessionParams;
  if (!params?.sessionId) {
    res.json(rpcError(-32602, "Missing required param: sessionId", rpcReq.id));
    return;
  }

  const interrupted = sessionPool.interrupt(params.sessionId);
  res.json(rpcSuccess({ ok: true, interrupted }, rpcReq.id));
}

function handleQueueStats(rpcReq: RpcRequest, res: Response): void {
  const params = rpcReq.params as { sessionId: string } | undefined;
  if (!params?.sessionId) {
    res.json(rpcError(-32602, "Missing required param: sessionId", rpcReq.id));
    return;
  }

  const stats = sessionPool.getQueueStats(params.sessionId);
  if (!stats) {
    res.json(rpcError(-32000, "Session not found", rpcReq.id));
    return;
  }
  res.json(rpcSuccess(stats, rpcReq.id));
}

function handleReconnect(rpcReq: RpcRequest, res: Response): void {
  const params = rpcReq.params as { sessionId: string } | undefined;
  if (!params?.sessionId) {
    res.json(rpcError(-32602, "Missing required param: sessionId", rpcReq.id));
    return;
  }

  const buffered = sessionPool.clientReconnect(params.sessionId);
  res.json(rpcSuccess({ bufferedEvents: buffered }, rpcReq.id));
}

function handleHealthCheck(rpcReq: RpcRequest, res: Response): void {
  const sessions = sessionPool.listSessions();
  const memUsage = process.memoryUsage();

  // Summarize session states
  const statusCounts: Record<string, number> = {};
  for (const s of sessions) {
    statusCounts[s.status] = (statusCounts[s.status] || 0) + 1;
  }

  res.json(
    rpcSuccess(
      {
        ok: true,
        sessions: sessions.length,
        sessionsByStatus: statusCounts,
        uptime: Math.floor((Date.now() - startTime) / 1000),
        memory: {
          rss: Math.round(memUsage.rss / 1024 / 1024),         // MB
          heapUsed: Math.round(memUsage.heapUsed / 1024 / 1024), // MB
          heapTotal: Math.round(memUsage.heapTotal / 1024 / 1024), // MB
        },
        nodeVersion: process.version,
        pid: process.pid,
      },
      rpcReq.id
    )
  );
}

function handleMonitorSessions(rpcReq: RpcRequest, res: Response): void {
  const sessions = sessionPool.listSessions();
  const detailed = sessions.map((s) => {
    const queueStats = sessionPool.getQueueStats(s.sessionId);
    return {
      sessionId: s.sessionId,
      path: s.path,
      status: s.status,
      mode: s.mode,
      model: s.model,
      sdkSessionId: s.sdkSessionId,
      createdAt: s.createdAt,
      lastActivityAt: s.lastActivityAt,
      queue: queueStats ?? { userPending: 0, responsePending: 0, clientConnected: false },
    };
  });

  res.json(
    rpcSuccess(
      {
        sessions: detailed,
        totalSessions: sessions.length,
        uptime: Math.floor((Date.now() - startTime) / 1000),
      },
      rpcReq.id
    )
  );
}

// ─── JSON-RPC Helpers ───

function rpcSuccess(result: unknown, id?: string): RpcResponse {
  return { result, id };
}

function rpcError(code: number, message: string, id?: string): RpcResponse {
  return { error: { code, message }, id };
}

// ─── Graceful Shutdown ───

async function shutdown(signal: string): Promise<void> {
  console.log(`\n[Daemon] Received ${signal}, shutting down gracefully...`);

  try {
    await sessionPool.destroyAll();
    console.log("[Daemon] All sessions destroyed");
  } catch (err) {
    console.error("[Daemon] Error during shutdown:", err);
  }

  process.exit(0);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

// ─── Start Server ───

app.listen(PORT, HOST, () => {
  console.log(`[Daemon] Remote Code Daemon listening on ${HOST}:${PORT}`);
  console.log(`[Daemon] Skills source: ${skillManager["skillsSourceDir"]}`);
});
