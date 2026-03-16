# Adding a Telegram Bot

This guide walks you through creating a Telegram bot and wiring it into Remote Code.

---

## Step 1: Create a Bot via BotFather

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Choose a **display name** (e.g. `Remote Code`).
4. Choose a **username** ending in `bot` (e.g. `MyRemoteCodeBot`).
5. BotFather will reply with a **token** like `123456789:ABCdefGHI...`. **Copy this token**.

---

## Step 2: Get Your Telegram User ID

The bot needs your numeric user ID to restrict access. To find it:

1. Search for [@userinfobot](https://t.me/userinfobot) on Telegram.
2. Send it any message.
3. It will reply with your **numeric ID** (e.g. `123456789`).

Alternatively, send a message to your new bot and check the logs — the user ID will appear in the update payload.

---

## Step 3: Configure Remote Code

Open `config.yaml` and add (or update) the `bot.telegram` section:

```yaml
bot:
  telegram:
    token: ${TELEGRAM_TOKEN}           # or paste the token directly
    allowed_users: [123456789]        # your Telegram user ID
    admin_users: [123456789]          # same ID for admin commands
```

**Configuration fields:**

| Field | Description |
|-------|-------------|
| `token` | Bot token from BotFather. Supports `${ENV_VAR}` expansion. |
| `allowed_users` | List of Telegram user IDs that can interact with the bot. Empty `[]` = anyone can use it (not recommended). |
| `admin_users` | User IDs allowed to run `/update` and `/restart`. |
| `allowed_chats` | (Optional) List of chat/group IDs where the bot responds. Empty = all chats. Use negative IDs for groups. |

**Token options:**

| Method | Example | Notes |
|--------|---------|-------|
| Environment variable | `${TELEGRAM_TOKEN}` | Recommended |
| Direct value | `"123456789:ABCdef..."` | Convenient for local testing |

Set the environment variable before running:

```bash
export TELEGRAM_TOKEN="your-token-here"
```

---

## Step 4: Start the Head Node

```bash
python -m head.main
```

Watch the logs:

```
INFO: Telegram bot configured
INFO: Starting Telegram bot...
INFO: Telegram bot started
INFO: Remote Code started with 1 bot(s)
```

The bot registers a command menu automatically — you should see commands like `/start`, `/help`, `/ls`, etc. in the Telegram command picker.

---

## Step 5: Verify the Bot Works

Open a chat with your bot on Telegram and send `/help`. It should reply with the command list.

Start your first session:

```
/start my-server /home/alice/myproject
```

---

## Running Discord and Telegram Together

Remote Code supports running both bots simultaneously. Configure both sections in `config.yaml`:

```yaml
bot:
  discord:
    token: ${DISCORD_TOKEN}
    allowed_channels: []
    admin_users: [123456789012345678]

  telegram:
    token: ${TELEGRAM_TOKEN}
    allowed_users: [123456789]
    admin_users: [123456789]
```

Each platform gets its own adapter and engine instance. Sessions are isolated by channel ID (prefixed with `discord:` or `telegram:`), so there is no cross-talk.

> **Note:** If you only want one platform active, comment out the other's section in config.yaml.

---

## Telegram-Specific Notes

### Message Formatting

Telegram uses HTML formatting internally. The adapter automatically converts markdown to Telegram HTML. Code blocks, bold, italic, and strikethrough all work.

### File Size Limit

Telegram limits file uploads/downloads to **20 MB** per file (standard bot API limit).

### Group Chat Support

To use the bot in a group chat:
1. Add the bot to the group.
2. Add the group's chat ID (a negative number) to `allowed_chats` in config.
3. In group chats, prefix commands with the bot's username: `/help@MyRemoteCodeBot`, or just use the command menu.

### Command Name Mapping

Telegram doesn't allow hyphens in command names. The adapter automatically maps:
- `/add_machine` -> `/add-machine`
- `/remove_machine` -> `/remove-machine`

---

## Troubleshooting

**Bot doesn't respond**
- Verify the token is correct: `python -c "from telegram import Bot; import asyncio; print(asyncio.run(Bot('YOUR_TOKEN').get_me()))"`
- Check that your user ID is in `allowed_users`.
- Make sure no other process is polling the same bot token (only one poller can be active at a time).

**Commands don't appear in the command menu**
- The command menu is registered on every startup. Try restarting the bot.
- In some Telegram clients, you may need to restart the app to see updated commands.

**Rate limiting / RetryAfter errors**
- The adapter handles Telegram rate limits automatically by sleeping and retrying.
- If you see frequent rate limit warnings, reduce the frequency of requests.

**Bot starts then immediately stops**
- Check the logs for errors. Common causes: invalid token, network issues, or another bot instance using the same token.
