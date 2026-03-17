# Integration Tests

End-to-end tests that verify the full head↔daemon communication path using a real Rust daemon and a mock Claude CLI.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Docker Container                           │
│                                             │
│  ┌──────────────────┐   localhost:9100      │
│  │  codecast-daemon  │◄──────────────────┐  │
│  │  (Rust binary)    │                   │  │
│  └────────┬─────────┘                   │  │
│           │ spawns                       │  │
│  ┌────────▼─────────┐   ┌──────────────┐│  │
│  │  mock claude CLI  │   │ pytest tests  ││  │
│  │  (bash script)    │   │ (Python)      ││  │
│  └──────────────────┘   └──────────────┘│  │
└─────────────────────────────────────────────┘
```

The mock Claude CLI (`mock-claude.sh`) simulates `claude --print` behavior:
- Outputs valid `stream-json` format events
- Supports special messages: `echo:<text>`, `error`, `slow`, `tools`

## Running

From the project root:

```bash
# Build and run
docker build -f tests/integration/Dockerfile -t codecast-integration .
docker run --rm codecast-integration

# Or via docker compose
docker compose -f tests/integration/docker-compose.yml up --build
```

## Test Coverage

| Category | Tests | Description |
|----------|-------|-------------|
| Health Check | 5 | Daemon health endpoint |
| Session Lifecycle | 8 | Create, list, destroy sessions |
| Send Message | 5 | Message flow with mock Claude |
| Session Mode | 2 | Permission mode switching |
| Queue Stats | 1 | Message queue statistics |
| Monitor | 1 | Detailed session monitoring |
| Interrupt | 1 | Session interrupt handling |
| Reconnect | 1 | Client reconnect + buffering |
| Invalid RPC | 3 | Error handling for bad requests |
| DaemonClient | 6 | Python client against real daemon |
| SessionRouter | 2 | SQLite session registry |
| MessageFormatter | 3 | Message splitting + formatting |
| NameGenerator | 2 | Session name generation |
| Pip Install | 3 | Package import + entry point |
