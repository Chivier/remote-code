# Adding a Discord Bot

This guide walks you through creating a Discord Application, configuring the bot, and wiring it into Remote Code.

---

## Step 1: Create a Discord Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and log in.
2. Click **New Application** (top-right).
3. Give it a name (e.g. `Remote Code`) and click **Create**.

---

## Step 2: Create the Bot User

1. In the left sidebar, click **Bot**.
2. Click **Add Bot** → **Yes, do it!**
3. Under the bot's username, you'll see a **Token** section. Click **Reset Token** and confirm.
4. **Copy the token** — you'll need it shortly. Treat it like a password; don't commit it to git.

Under **Privileged Gateway Intents**, enable:

- **Message Content Intent** — required so the bot can read messages in channels

Click **Save Changes**.

---

## Step 3: Configure OAuth2 Permissions

1. In the left sidebar, click **OAuth2** → **URL Generator**.
2. Under **Scopes**, check:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, check:
   - `Send Messages`
   - `Read Message History`
   - `Use Slash Commands`
   - `Read Messages / View Channels`
4. Copy the generated URL at the bottom of the page.

---

## Step 4: Invite the Bot to Your Server

1. Open the OAuth2 URL you copied in a browser.
2. Select the Discord server where you want the bot to operate.
3. Click **Authorize** and complete the CAPTCHA.

The bot should now appear in your server's member list (offline until you start the Head Node).

---

## Step 5: Get Your Channel ID

You need the channel ID to restrict the bot to specific channels.

1. In Discord, go to **User Settings** → **Advanced** → enable **Developer Mode**.
2. Right-click the channel where you want the bot to respond.
3. Click **Copy Channel ID**.

---

## Step 6: Configure Remote Code

Open `config.yaml` and add (or update) the `bot.discord` section:

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}       # loaded from environment variable
    allowed_channels:
      - 1234567890123456789       # paste your channel ID here
    command_prefix: "/"
```

**Token options:**

| Method | Example | Notes |
|--------|---------|-------|
| Environment variable | `${DISCORD_TOKEN}` | Recommended — keeps secrets out of the config file |
| Direct value | `"Bot MTIzNDU2..."` | Convenient for local testing only |

Set the environment variable before running:

```bash
export DISCORD_TOKEN="your-token-here"
```

**`allowed_channels`** restricts the bot to respond only in those channels. If the list is empty, the bot responds in all channels it can see — not recommended for shared servers.

---

## Step 7: Start the Head Node

```bash
python -m head.main
```

Watch the logs:

```
INFO: Discord bot configured
INFO: Discord bot logged in as RemoteClaude#1234
INFO: Synced 9 slash command(s)
```

`Synced 9 slash command(s)` confirms that Discord registered the slash commands (`/start`, `/resume`, `/ls`, etc.). This happens automatically on every startup.

> **Note:** Slash commands can take up to an hour to propagate globally after the first sync. If you don't see them immediately, wait a few minutes and try again — or restart the bot once more.

---

## Step 8: Verify the Bot Works

In your allowed channel, type `/help`. The bot should respond with the list of available commands.

Then start your first Claude session:

```
/start my-server /home/alice/myproject
```

---

## Multiple Discord Bots (Advanced)

The current architecture supports **one Discord bot per Head Node instance**. If you want multiple bots (e.g. one per team), run multiple Head Node instances with separate `config.yaml` files pointing to different bot tokens.

```bash
# Bot A
DISCORD_TOKEN=token-a python -m head.main config-a.yaml

# Bot B
DISCORD_TOKEN=token-b python -m head.main config-b.yaml
```

Each instance maintains its own session database (`head/sessions.db`). Make sure they use separate working directories or specify different db paths if needed.

---

## Troubleshooting

**Bot appears offline / doesn't respond**
- Confirm the token is correct and the bot process is running.
- Check that `Message Content Intent` is enabled in the Developer Portal.
- Verify the channel ID in `allowed_channels` matches the target channel.

**Slash commands don't appear**
- Check the logs for `Synced N slash command(s)`. If `N = 0`, there's a permissions issue.
- Ensure the bot invite URL included `applications.commands` scope.
- Wait a few minutes and reload Discord.

**`Missing Access` errors in logs**
- The bot doesn't have permission to post in the channel. Go to the channel settings → Permissions and grant the bot's role `Send Messages` and `View Channel`.

**`DISCORD_TOKEN` not found**
- Export the variable in your shell before running: `export DISCORD_TOKEN="..."`.
- Or set it inline: `DISCORD_TOKEN="..." python -m head.main`.
