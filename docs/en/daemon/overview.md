# Daemon Overview

The Daemon is the remote agent component of Codecast. It runs on each remote machine (GPU server, cloud VM, etc.) and provides a JSON-RPC + SSE interface for managing CLI sessions.

## Technology Stack

- **Language:** Rust (2021 edition)
- **Async runtime:** tokio
- **HTTP framework:** Axum
- **Serialization:** serde / serde_json
- **Process management:** tokio::process
- **UUID generation:** uuid crate (v4)
- **Time:** chrono (UTC timestamps)
- **Logging:** tracing + tracing-subscriber

## Module Map

```
src/daemon/
├── main.rs              # Axum server entry: port binding, TLS, graceful shutdown
├── server.rs            # JSON-RPC router (POST /rpc), SSE streaming, AppState
├── session_pool.rs      # Session registry, per-message spawn, CliAdapter dispatch
├── message_queue.rs     # Per-session user message + response buffering
├── skill_manager.rs     # Skills sync from ~/.codecast/skills to project dirs
├── types.rs             # StreamEvent, SessionStatus, PermissionMode, RPC types
├── cli_adapter/
│   ├── mod.rs           # CliAdapter trait, create_adapter() factory, CLI_TYPES
│   ├── claude.rs        # Claude CLI adapter
│   ├── codex.rs         # OpenAI Codex adapter
│   ├── gemini.rs        # Google Gemini CLI adapter
│   └── opencode.rs      # OpenCode adapter
├── auth.rs              # Token-based auth middleware, TokenStore
├── config.rs            # DaemonConfig: load from ~/.codecast/daemon.yaml
└── tls.rs               # TLS certificate generation/loading for HTTPS mode
```

## Module Dependencies

```
main.rs
  ├── server.rs       (AppState, build_router)
  ├── session_pool.rs (SessionPool)
  ├── skill_manager.rs (SkillManager)
  ├── auth.rs         (TokenStore)
  ├── config.rs       (DaemonConfig)
  └── types.rs        (PermissionMode)

server.rs
  ├── session_pool.rs (SessionPool)
  ├── skill_manager.rs (SkillManager — for session.create skills sync)
  ├── auth.rs         (auth_middleware)
  └── types.rs        (RpcRequest, RpcResponse, PermissionMode)

session_pool.rs
  ├── cli_adapter/mod.rs (create_adapter, CliAdapter)
  ├── message_queue.rs   (MessageQueue)
  └── types.rs           (SessionStatus, PermissionMode, StreamEvent, SessionInfo, QueueStats)

message_queue.rs
  └── types.rs           (StreamEvent, QueueStats)

cli_adapter/
  └── types.rs           (PermissionMode, StreamEvent)

types.rs
  └── (standalone, type definitions only)
```

## Architecture: Per-Message Spawn

The daemon uses a **per-message spawn** architecture:

1. `session.create` registers session metadata (path, mode, CLI type) but does **not** spawn a process.
2. `session.send` selects the appropriate `CliAdapter`, builds the subprocess command, and spawns it for the duration of one message exchange.
3. The process exits after producing its full output. The SDK/thread session ID is captured from the `result` event.
4. The next `session.send` spawns a new process with `--resume <sdkSessionId>` (or the equivalent for non-Claude CLIs) to continue the conversation.

This design provides:
- Clean process isolation per message
- Automatic memory cleanup between messages
- No zombie process management
- Natural recovery from CLI crashes

## Security

The daemon binds exclusively to `127.0.0.1` by default. It is not network-reachable without SSH port forwarding.

```rust
let addr: SocketAddr = format!("{}:{}", host, actual_port).parse().unwrap();
let listener = TcpListener::bind(addr).await?;
```

In HTTP mode (the default for SSH-tunnel access), the daemon requires no credentials because access is controlled by SSH authentication on the tunnel.

In HTTPS/auth mode (when `config.requires_auth()` is true), the daemon uses Bearer token authentication via `auth_middleware` and serves over TLS. This mode is used when the daemon is accessed directly over a network rather than through SSH.

## Port Discovery

On startup, the daemon tries to bind to the configured port (default: 9100). If the port is in use, it increments and retries up to `port + 100`. The actual port is:

1. Printed to stdout as `DAEMON_PORT=<port>` for the Head Node to capture during startup
2. Written to `~/.codecast/daemon.port` for subsequent discovery

## Lifecycle

1. **Start**: The Head Node's SSHManager deploys the daemon binary via SCP and launches it over SSH. The `DAEMON_PORT` environment variable (or `~/.codecast/daemon.yaml`) controls the listen port.
2. **Running**: The daemon accepts JSON-RPC requests on `POST /rpc`. Each request is dispatched to the appropriate handler in `server.rs`.
3. **Shutdown**: SIGTERM or SIGINT triggers graceful shutdown. `session_pool.destroy_all()` is called to kill all running CLI processes and clear queues. The port file at `~/.codecast/daemon.port` is removed.

## Environment / Config

The daemon loads configuration from `~/.codecast/daemon.yaml`. Environment variables override config file values:

| Config key | Env override | Default | Description |
|---|---|---|---|
| `port` | `DAEMON_PORT` | `9100` | Port to listen on |
| `bind` | `DAEMON_BIND` | `127.0.0.1` | Bind address |
| `tokens_file` | — | `~/.codecast/tokens.yaml` | Token store path (auth mode) |
| `tls_cert` | — | `~/.codecast/tls-cert.pem` | TLS certificate path |
| `tls_key` | — | `~/.codecast/tls-key.pem` | TLS private key path |
