import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import express, { Request, Response } from "express";
import { MessageQueue } from "../src/message-queue";
import {
  RpcRequest,
  RpcResponse,
  SessionInfo,
  HealthCheckResult,
  MonitorSessionsResult,
  MonitorSessionDetail,
} from "../src/types";

// ─── Test helpers ───

/**
 * A minimal in-memory session store for testing the RPC handlers
 * without spawning actual Claude CLI processes.
 */
interface FakeSession {
  sessionId: string;
  path: string;
  status: string;
  mode: string;
  model: string | null;
  sdkSessionId: string | null;
  createdAt: string;
  lastActivityAt: string;
  queue: MessageQueue;
}

function createFakeSession(overrides: Partial<FakeSession> = {}): FakeSession {
  return {
    sessionId: overrides.sessionId ?? "test-session-1",
    path: overrides.path ?? "/tmp/test-project",
    status: overrides.status ?? "idle",
    mode: overrides.mode ?? "auto",
    model: overrides.model ?? "claude-sonnet-4-20250514",
    sdkSessionId: overrides.sdkSessionId ?? "sdk-abc-123",
    createdAt: overrides.createdAt ?? new Date().toISOString(),
    lastActivityAt: overrides.lastActivityAt ?? new Date().toISOString(),
    queue: overrides.queue ?? new MessageQueue(),
  };
}

/**
 * Build a test Express app that mimics the daemon server's RPC handlers
 * for health.check and monitor.sessions, without spawning Claude CLI processes.
 */
function buildTestApp(sessions: FakeSession[] = []) {
  const app = express();
  app.use(express.json());

  const startTime = Date.now();

  app.post("/rpc", (req: Request, res: Response) => {
    const rpcReq = req.body as RpcRequest;

    if (!rpcReq.method) {
      res.json(rpcError(-32600, "Invalid request: missing method", rpcReq.id));
      return;
    }

    switch (rpcReq.method) {
      case "health.check": {
        const sessionInfos: SessionInfo[] = sessions.map((s) => ({
          sessionId: s.sessionId,
          path: s.path,
          status: s.status as any,
          mode: s.mode as any,
          sdkSessionId: s.sdkSessionId,
          model: s.model,
          createdAt: s.createdAt,
          lastActivityAt: s.lastActivityAt,
        }));

        const statusCounts: Record<string, number> = {};
        for (const s of sessionInfos) {
          statusCounts[s.status] = (statusCounts[s.status] || 0) + 1;
        }

        const memUsage = process.memoryUsage();

        res.json(
          rpcSuccess(
            {
              ok: true,
              sessions: sessionInfos.length,
              sessionsByStatus: statusCounts,
              uptime: Math.floor((Date.now() - startTime) / 1000),
              memory: {
                rss: Math.round(memUsage.rss / 1024 / 1024),
                heapUsed: Math.round(memUsage.heapUsed / 1024 / 1024),
                heapTotal: Math.round(memUsage.heapTotal / 1024 / 1024),
              },
              nodeVersion: process.version,
              pid: process.pid,
            },
            rpcReq.id
          )
        );
        break;
      }

      case "monitor.sessions": {
        const detailed: MonitorSessionDetail[] = sessions.map((s) => {
          const queueStats = s.queue.stats();
          return {
            sessionId: s.sessionId,
            path: s.path,
            status: s.status as any,
            mode: s.mode as any,
            model: s.model,
            sdkSessionId: s.sdkSessionId,
            createdAt: s.createdAt,
            lastActivityAt: s.lastActivityAt,
            queue: queueStats,
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
        break;
      }

      case "session.list": {
        const sessionInfos: SessionInfo[] = sessions.map((s) => ({
          sessionId: s.sessionId,
          path: s.path,
          status: s.status as any,
          mode: s.mode as any,
          sdkSessionId: s.sdkSessionId,
          model: s.model,
          createdAt: s.createdAt,
          lastActivityAt: s.lastActivityAt,
        }));
        res.json(rpcSuccess({ sessions: sessionInfos }, rpcReq.id));
        break;
      }

      default:
        res.json(
          rpcError(-32601, `Method not found: ${rpcReq.method}`, rpcReq.id)
        );
    }
  });

  return app;
}

function rpcSuccess(result: unknown, id?: string): RpcResponse {
  return { result, id };
}

function rpcError(code: number, message: string, id?: string): RpcResponse {
  return { error: { code, message }, id };
}

/**
 * Make an RPC request to the test server
 */
function rpcCall(
  port: number,
  method: string,
  params?: Record<string, unknown>,
  id?: string
): Promise<RpcResponse> {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ method, params, id });
    const options: http.RequestOptions = {
      hostname: "127.0.0.1",
      port,
      path: "/rpc",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    };

    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(data));
        } catch (err) {
          reject(new Error(`Failed to parse response: ${data}`));
        }
      });
    });

    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

// ─── Tests ───

describe("Health Monitor Integration Tests", () => {
  // ─── No Sessions ───

  describe("with no sessions", () => {
    let server: http.Server;
    let port: number;

    before(async () => {
      const app = buildTestApp([]);
      await new Promise<void>((resolve) => {
        server = app.listen(0, "127.0.0.1", () => {
          port = (server.address() as { port: number }).port;
          resolve();
        });
      });
    });

    after(async () => {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
    });

    it("health.check returns ok=true with zero sessions", async () => {
      const resp = await rpcCall(port, "health.check", {}, "hc-1");
      assert.equal(resp.error, undefined);
      assert.equal(resp.id, "hc-1");

      const result = resp.result as HealthCheckResult;
      assert.equal(result.ok, true);
      assert.equal(result.sessions, 0);
      assert.deepEqual(result.sessionsByStatus, {});
    });

    it("health.check returns memory info", async () => {
      const resp = await rpcCall(port, "health.check");
      const result = resp.result as HealthCheckResult;

      assert.ok(result.memory !== undefined);
      assert.ok(typeof result.memory.rss === "number");
      assert.ok(typeof result.memory.heapUsed === "number");
      assert.ok(typeof result.memory.heapTotal === "number");
      assert.ok(result.memory.rss > 0);
      assert.ok(result.memory.heapUsed > 0);
      assert.ok(result.memory.heapTotal > 0);
    });

    it("health.check returns nodeVersion and pid", async () => {
      const resp = await rpcCall(port, "health.check");
      const result = resp.result as HealthCheckResult;

      assert.equal(result.nodeVersion, process.version);
      assert.equal(result.pid, process.pid);
    });

    it("health.check returns uptime >= 0", async () => {
      const resp = await rpcCall(port, "health.check");
      const result = resp.result as HealthCheckResult;

      assert.ok(typeof result.uptime === "number");
      assert.ok(result.uptime >= 0);
    });

    it("monitor.sessions returns empty sessions array", async () => {
      const resp = await rpcCall(port, "monitor.sessions", {}, "ms-1");
      assert.equal(resp.error, undefined);
      assert.equal(resp.id, "ms-1");

      const result = resp.result as MonitorSessionsResult;
      assert.deepEqual(result.sessions, []);
      assert.equal(result.totalSessions, 0);
      assert.ok(typeof result.uptime === "number");
      assert.ok(result.uptime >= 0);
    });
  });

  // ─── With Sessions ───

  describe("with active sessions", () => {
    let server: http.Server;
    let port: number;
    let sessions: FakeSession[];

    before(async () => {
      const queue1 = new MessageQueue();
      queue1.enqueueUser("pending message 1");
      queue1.enqueueUser("pending message 2");

      const queue2 = new MessageQueue();
      queue2.onClientDisconnect();
      queue2.bufferResponse({ type: "text", content: "buffered" });

      sessions = [
        createFakeSession({
          sessionId: "session-idle",
          status: "idle",
          mode: "auto",
          queue: queue1,
        }),
        createFakeSession({
          sessionId: "session-busy",
          status: "busy",
          mode: "code",
          model: "claude-opus-4-20250514",
          queue: queue2,
        }),
        createFakeSession({
          sessionId: "session-error",
          status: "error",
          mode: "plan",
          model: null,
        }),
      ];

      const app = buildTestApp(sessions);
      await new Promise<void>((resolve) => {
        server = app.listen(0, "127.0.0.1", () => {
          port = (server.address() as { port: number }).port;
          resolve();
        });
      });
    });

    after(async () => {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
    });

    it("health.check returns correct session count", async () => {
      const resp = await rpcCall(port, "health.check");
      const result = resp.result as HealthCheckResult;
      assert.equal(result.sessions, 3);
    });

    it("health.check returns correct sessionsByStatus", async () => {
      const resp = await rpcCall(port, "health.check");
      const result = resp.result as HealthCheckResult;

      assert.equal(result.sessionsByStatus["idle"], 1);
      assert.equal(result.sessionsByStatus["busy"], 1);
      assert.equal(result.sessionsByStatus["error"], 1);
    });

    it("monitor.sessions returns all sessions with queue stats", async () => {
      const resp = await rpcCall(port, "monitor.sessions");
      const result = resp.result as MonitorSessionsResult;

      assert.equal(result.totalSessions, 3);
      assert.equal(result.sessions.length, 3);

      // Find sessions by ID
      const idle = result.sessions.find(
        (s) => s.sessionId === "session-idle"
      )!;
      const busy = result.sessions.find(
        (s) => s.sessionId === "session-busy"
      )!;
      const error = result.sessions.find(
        (s) => s.sessionId === "session-error"
      )!;

      assert.ok(idle, "idle session should exist");
      assert.ok(busy, "busy session should exist");
      assert.ok(error, "error session should exist");
    });

    it("monitor.sessions includes correct queue stats for idle session", async () => {
      const resp = await rpcCall(port, "monitor.sessions");
      const result = resp.result as MonitorSessionsResult;
      const idle = result.sessions.find(
        (s) => s.sessionId === "session-idle"
      )!;

      assert.equal(idle.queue.userPending, 2);
      assert.equal(idle.queue.responsePending, 0);
      assert.equal(idle.queue.clientConnected, true);
    });

    it("monitor.sessions includes correct queue stats for busy session with disconnected client", async () => {
      const resp = await rpcCall(port, "monitor.sessions");
      const result = resp.result as MonitorSessionsResult;
      const busy = result.sessions.find(
        (s) => s.sessionId === "session-busy"
      )!;

      assert.equal(busy.queue.userPending, 0);
      assert.equal(busy.queue.responsePending, 1);
      assert.equal(busy.queue.clientConnected, false);
    });

    it("monitor.sessions contains correct session metadata", async () => {
      const resp = await rpcCall(port, "monitor.sessions");
      const result = resp.result as MonitorSessionsResult;
      const busy = result.sessions.find(
        (s) => s.sessionId === "session-busy"
      )!;

      assert.equal(busy.status, "busy");
      assert.equal(busy.mode, "code");
      assert.equal(busy.model, "claude-opus-4-20250514");
      assert.equal(busy.path, "/tmp/test-project");
    });

    it("session.list returns all sessions with correct info", async () => {
      const resp = await rpcCall(port, "session.list");
      const result = resp.result as { sessions: SessionInfo[] };

      assert.equal(result.sessions.length, 3);
      const sessionIds = result.sessions.map((s) => s.sessionId).sort();
      assert.deepEqual(sessionIds, [
        "session-busy",
        "session-error",
        "session-idle",
      ]);
    });
  });

  // ─── Error handling ───

  describe("error handling", () => {
    let server: http.Server;
    let port: number;

    before(async () => {
      const app = buildTestApp([]);
      await new Promise<void>((resolve) => {
        server = app.listen(0, "127.0.0.1", () => {
          port = (server.address() as { port: number }).port;
          resolve();
        });
      });
    });

    after(async () => {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
    });

    it("unknown RPC method returns error -32601", async () => {
      const resp = await rpcCall(
        port,
        "nonexistent.method",
        {},
        "err-1"
      );
      assert.ok(resp.error !== undefined);
      assert.equal(resp.error!.code, -32601);
      assert.ok(resp.error!.message.includes("Method not found"));
      assert.ok(resp.error!.message.includes("nonexistent.method"));
      assert.equal(resp.id, "err-1");
      assert.equal(resp.result, undefined);
    });

    it("missing method returns error -32600", async () => {
      // Send a request with no method field
      const resp = await new Promise<RpcResponse>((resolve, reject) => {
        const body = JSON.stringify({ params: {} });
        const options: http.RequestOptions = {
          hostname: "127.0.0.1",
          port,
          path: "/rpc",
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(body),
          },
        };
        const req = http.request(options, (res) => {
          let data = "";
          res.on("data", (chunk) => (data += chunk));
          res.on("end", () => {
            try {
              resolve(JSON.parse(data));
            } catch (err) {
              reject(new Error(`Failed to parse response: ${data}`));
            }
          });
        });
        req.on("error", reject);
        req.write(body);
        req.end();
      });

      assert.ok(resp.error !== undefined);
      assert.equal(resp.error!.code, -32600);
      assert.ok(resp.error!.message.includes("missing method"));
    });

    it("RPC request preserves the request id in the response", async () => {
      const resp = await rpcCall(
        port,
        "health.check",
        {},
        "my-request-id-42"
      );
      assert.equal(resp.id, "my-request-id-42");
    });

    it("RPC request without id returns undefined id", async () => {
      const resp = await rpcCall(port, "health.check");
      assert.equal(resp.id, undefined);
    });
  });

  // ─── Multiple status types in sessionsByStatus ───

  describe("sessionsByStatus counting", () => {
    let server: http.Server;
    let port: number;

    before(async () => {
      const sessions = [
        createFakeSession({ sessionId: "s1", status: "idle" }),
        createFakeSession({ sessionId: "s2", status: "idle" }),
        createFakeSession({ sessionId: "s3", status: "idle" }),
        createFakeSession({ sessionId: "s4", status: "busy" }),
        createFakeSession({ sessionId: "s5", status: "busy" }),
        createFakeSession({ sessionId: "s6", status: "error" }),
      ];

      const app = buildTestApp(sessions);
      await new Promise<void>((resolve) => {
        server = app.listen(0, "127.0.0.1", () => {
          port = (server.address() as { port: number }).port;
          resolve();
        });
      });
    });

    after(async () => {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
    });

    it("correctly counts multiple sessions by status", async () => {
      const resp = await rpcCall(port, "health.check");
      const result = resp.result as HealthCheckResult;

      assert.equal(result.sessions, 6);
      assert.equal(result.sessionsByStatus["idle"], 3);
      assert.equal(result.sessionsByStatus["busy"], 2);
      assert.equal(result.sessionsByStatus["error"], 1);
      assert.equal(result.sessionsByStatus["destroyed"], undefined);
    });
  });
});
