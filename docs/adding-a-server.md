# Adding a Server

This guide covers how to add a remote machine to Remote Claude's `config.yaml`. Four scenarios are covered:

1. [Direct SSH connection](#scenario-1-direct-ssh-connection) — standard setup
2. [Jump host / Bastion](#scenario-2-jump-host--bastion-server) — target behind a gateway
3. [Password authentication](#scenario-3-password-authentication) — no SSH key available
4. [Custom Node.js path](#scenario-4-custom-nodejs-path) — Node not in the default PATH

---

## Configuration Structure

All machines are defined under the `machines:` key in `config.yaml`. Each entry has a short ID (used in bot commands) and a set of connection parameters:

```yaml
machines:
  <machine-id>:
    host: <hostname or IP>
    user: <ssh username>
    # ... other options
```

The `<machine-id>` is what you type in `/start my-server /path/to/project`. Keep it short and memorable.

---

## Scenario 1: Direct SSH Connection

The most common setup. You can SSH directly to the target machine.

```yaml
machines:
  gpu-server:
    host: 10.0.1.50          # IP address or hostname
    user: alice              # SSH username
    ssh_key: ~/.ssh/id_ed25519  # path to your private key (optional if using ssh-agent)
    port: 22                 # SSH port (default: 22, can omit)
    daemon_port: 9100        # port the daemon listens on (default: 9100, can omit)
    default_paths:           # suggested paths for autocomplete in /start
      - /home/alice/project-alpha
      - /home/alice/project-beta
```

**Minimal version (if SSH agent is running):**

```yaml
machines:
  gpu-server:
    host: 10.0.1.50
    user: alice
```

**Verify SSH access before adding to config:**

```bash
ssh alice@10.0.1.50 "echo OK"
```

If this asks for a passphrase, either add the key to your SSH agent (`ssh-add ~/.ssh/id_ed25519`) or specify `ssh_key:` in the config.

---

## Scenario 2: Jump Host / Bastion Server

Use this when the target machine is only reachable through a gateway (bastion) host.

Remote Claude implements proxy-jump natively using asyncssh — it does not depend on your local `~/.ssh/config`.

**Step 1:** Add the jump host as its own machine entry:

```yaml
machines:
  bastion:
    host: bastion.example.com
    user: alice
    ssh_key: ~/.ssh/id_ed25519
    # No daemon_port needed — bastion is only a relay
    # No default_paths — won't appear in /start autocomplete
```

**Step 2:** Add the target machine with `proxy_jump:` pointing to the bastion ID:

```yaml
machines:
  gpu-lab:
    host: 10.100.0.5         # internal IP, only reachable from bastion
    user: alice
    ssh_key: ~/.ssh/id_ed25519
    proxy_jump: bastion      # must match the machine ID above
    daemon_port: 9100
    default_paths:
      - /home/alice/research
```

**Full example:**

```yaml
machines:
  bastion:
    host: gateway.mylab.edu
    user: alice
    ssh_key: ~/.ssh/id_ed25519

  gpu-node-1:
    host: 192.168.10.11
    user: alice
    ssh_key: ~/.ssh/id_ed25519
    proxy_jump: bastion
    daemon_port: 9100
    default_paths:
      - /home/alice/experiments

  gpu-node-2:
    host: 192.168.10.12
    user: alice
    ssh_key: ~/.ssh/id_ed25519
    proxy_jump: bastion
    daemon_port: 9100
    default_paths:
      - /home/alice/training-runs
```

> **Note:** Remote Claude treats machines that appear only as `proxy_jump` targets (and have no `default_paths`) as pure jump hosts — they are hidden from the `/ls machine` and `/start` autocomplete output.

---

## Scenario 3: Password Authentication

If SSH key auth is not available, you can use a password. Two methods are supported.

### Method A: Inline password (not recommended for shared configs)

```yaml
machines:
  legacy-server:
    host: 10.0.2.99
    user: bob
    password: "my-ssh-password"
```

### Method B: Password from file (recommended)

Store the password in a file readable only by you, then reference it with the `file:` prefix:

```bash
# Create the password file
echo "my-ssh-password" > ~/.ssh/remote-claude-pw
chmod 600 ~/.ssh/remote-claude-pw
```

```yaml
machines:
  legacy-server:
    host: 10.0.2.99
    user: bob
    password: "file:~/.ssh/remote-claude-pw"
```

The `~` in the file path is expanded automatically.

> **Security note:** Plain-text passwords are less secure than key-based auth. If possible, set up SSH keys instead. If you must use passwords, the `file:` method keeps the secret out of the main config file so it doesn't end up in version control.

---

## Scenario 4: Custom Node.js Path

By default, Remote Claude calls `node` on the remote machine. If Node.js is installed in a non-standard location (e.g. via `nvm`, `fnm`, or a custom prefix), specify the full path with `node_path:`.

```yaml
machines:
  nvm-server:
    host: 10.0.1.77
    user: carol
    node_path: /home/carol/.nvm/versions/node/v20.11.0/bin/node
    daemon_port: 9100
    default_paths:
      - /home/carol/webapp
```

When `node_path` is set, Remote Claude:
- Uses that binary to start the daemon (`nohup /path/to/node dist/server.js`)
- Adds the node binary's parent directory to `PATH` when spawning the daemon, so `npm` and other node tools work correctly
- Adds `~/.local/bin` to `PATH` so the Claude CLI (typically installed there) is also available

**Finding the Node path on the remote:**

```bash
ssh carol@10.0.1.77 "which node || command -v node"
# or if using nvm:
ssh carol@10.0.1.77 "source ~/.nvm/nvm.sh && which node"
```

---

## Config Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | *(required)* | Hostname or IP address |
| `user` | string | `$USER` | SSH username |
| `port` | int | `22` | SSH port |
| `ssh_key` | path | ssh-agent | Path to private key file |
| `password` | string | — | SSH password or `file:/path` |
| `proxy_jump` | machine-id | — | Jump host machine ID |
| `proxy_command` | string | — | Raw SSH ProxyCommand string |
| `daemon_port` | int | `9100` | Port for the daemon RPC server |
| `node_path` | path | `node` | Full path to the `node` binary on remote |
| `default_paths` | list | `[]` | Project paths shown in `/start` autocomplete |

---

## After Adding a Machine

Restart the Head Node to pick up the new config:

```bash
# If running manually
Ctrl+C
python -m head.main

# If running as a systemd service
sudo systemctl restart remote-claude
```

Verify the machine is reachable:

```
/ls machine
```

The output shows each machine's SSH and daemon status. A new machine will show `daemon: stopped` until you run `/start` on it for the first time (which triggers auto-deploy if `daemon.auto_deploy: true`).

---

## Daemon Port Conflicts

Each machine can use a different `daemon_port`. If multiple machines share the same port (e.g. both use 9100), that's fine — the Head Node creates a separate local tunnel for each machine, so there's no conflict locally.

If a machine already has a service running on port 9100, pick a different port:

```yaml
machines:
  my-server:
    host: 10.0.1.50
    user: alice
    daemon_port: 9200    # use an unused port
```
