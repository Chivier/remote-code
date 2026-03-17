# Single-container integration test
# Builds Rust daemon + installs Python head + runs tests, all via localhost.
# This avoids the daemon's 127.0.0.1 binding restriction.
#
# Usage:
#   docker build -f tests/integration/Dockerfile -t codecast-integration .
#   docker run --rm codecast-integration

FROM rust:1.85-bookworm AS daemon-builder

WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
COPY src/ src/

RUN cargo build --release

# ---

FROM python:3.12-bookworm

# Install Rust (needed by pip install codecast for setuptools-rust)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy pre-built daemon binary
COPY --from=daemon-builder /build/target/release/codecast-daemon /usr/local/bin/codecast-daemon

WORKDIR /app
COPY . /app/

# Install mock Claude CLI
COPY tests/integration/mock-claude.sh /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude

# Install codecast from source (tests pip install path)
RUN pip install --no-cache-dir -e ".[test]" 2>/dev/null || pip install --no-cache-dir -e .

# Install test deps
RUN pip install --no-cache-dir pytest pytest-asyncio aiohttp

# Create test workspace
RUN mkdir -p /workspace/test-project

ENV DAEMON_PORT=9100
ENV PYTHONUNBUFFERED=1

# Entry: start daemon in background, run tests
COPY tests/integration/run-tests.sh /app/tests/integration/run-tests.sh
RUN chmod +x /app/tests/integration/run-tests.sh

CMD ["/app/tests/integration/run-tests.sh"]
