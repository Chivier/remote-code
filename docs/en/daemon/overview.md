# Daemon Overview

The Daemon is the remote agent component of Remote Code. It runs on each remote machine (GPU server, cloud VM, etc.) and provides a JSON-RPC interface for managing Claude CLI sessions.

## Technology Stack

- **Language:** TypeScript
- **Runtime:** Node.js 18+
- **HTTP Server:** Express.js
- **Process Management:** Node.js `child_process.spawn()`
- **UUID Generation:** `uuid` package (v4)

## Module Map

```
daemon/src/
├── server.ts         # Express JSON-RPC server & HTTP endpoint
├── session-pool.ts   # Claude CLI process lifecycle management
├── message-queue.ts  # Per-session message & response buffering
├── skill-manager.ts  # Skills file sync to project directories
└── types.ts          # TypeScript types for RPC protocol & events
```

## Module Dependencies

```
server.ts
  ├── session-pool.ts  (SessionPool)
  ├── skill-manager.ts (SkillManager)
  └── types.ts         (RPC types)

session-pool.ts
  ├── message-queue.ts (MessageQueue)
  └── types.ts         (session types, stream events)

message-queue.ts
  └── types.ts         (QueuedUserMessage, QueuedResponse, StreamEvent)

skill-manager.ts
  └── (standalone, uses fs)

types.ts
  └── (standalone, type definitions only)
```

## Architecture

The daemon uses a **per-message spawn** architecture rather than maintaining long-running Claude CLI processes:

1. A `session.create` call registers session metadata (path, mode) but does **not** spawn a process.
2. A `session.send` call spawns a `claude --print <message> --output-format stream-json` process for the duration of one message exchange.
3. The process exits after producing its output. The SDK session ID is captured from the `result` event.
4. The next `session.send` call spawns a new process with `--resume <sdkSessionId>` to continue the conversation.

This design provides:
- Clean process isolation per message
- Automatic memory cleanup between messages
- No zombie process management
- Natural recovery from crashes

## Security

The daemon binds exclusively to `127.0.0.1` (localhost). It is never accessible from the network directly. All access is through SSH port forwarding established by the Head Node.

```typescript
const HOST = "127.0.0.1";
app.listen(PORT, HOST, () => { ... });
```

## Lifecycle

1. **Start**: The daemon is started by the Head Node's SSHManager via `nohup node dist/server.js`. The `DAEMON_PORT` environment variable controls the listen port (default: 9100).
2. **Running**: The daemon accepts JSON-RPC requests on `POST /rpc`. Each request is routed to the appropriate handler.
3. **Shutdown**: On SIGTERM/SIGINT, all sessions are destroyed (processes killed, queues cleared), then the process exits.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DAEMON_PORT` | `9100` | Port to listen on |
| `HOME` | System default | Used to locate the skills source directory (`~/.remote-code/skills`) |
