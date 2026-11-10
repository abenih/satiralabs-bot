# SatiraLabs Inquiry Bot — Setup

The bot code is ready and deployed on the VPS. The only missing piece is
the bot token, which only you can create. Follow these steps once, in order.

## 1. Create the bot in @BotFather

1. Open Telegram and search for **@BotFather** (the official one, with the blue tick).
2. Send `/newbot`.
3. When asked for a name, type: **SatiraLabs**
4. When asked for a username, try **SatiraLabsBot** first. If it is taken,
   use **SatiraLabsETBot** or **satiralabs_inquiry_bot**. (Usernames must end in "bot".)
5. BotFather replies with the bot link (t.me/<username>) and a **token**
   that looks like `123456789:AAE...`.

Keep the token private. Never paste it into group chats or screenshots.
You will hand it over through the secure credential flow arranged in chat;
it belongs only in the server's `.env` file (step 3).

Command list in BotFather (`/setcommands`) — navigation inside the bot
is buttons-only, so set ONLY this one line:

```
start - Open the menu
```

(An owner-only `/inquiries` command exists in the code so the owner can
list stored inquiries; it is deliberately NOT listed in BotFather and
never shown to visitors.)

Optional polish in the same BotFather chat:
- `/setdescription` — "SatiraLabs assistant: services, portfolio and project inquiries. Replies within one business day."
- `/setabouttext` — "SatiraLabs — websites, apps & Telegram bots, SEO, AI automation, ads and security audits. Working worldwide. hello@satiralabs.com"
- `/setuserpic` — upload the SatiraLabs logo.

## 2. Find your own Telegram chat id

1. Search Telegram for **@userinfobot** and press Start.
2. It replies with your numeric **Id** — that is your chat id.
   (Alternatively, message your new bot once it is running and read the
   chat id from the bot's log; the userinfobot route is simpler.)

## 3. Put the values on the server

On the VPS, the file `/opt/satiralabs-bot/.env` holds two lines:

```
BOT_TOKEN=<the token from BotFather>
OWNER_CHAT_ID=<your numeric chat id from step 2>
```

The file is root-owned with mode 600. If you are doing this by hand over SSH:

```bash
ssh -F ~/.ssh/config_spotgames spot-vps
nano /opt/satiralabs-bot/.env    # fill in BOT_TOKEN and OWNER_CHAT_ID
chmod 600 /opt/satiralabs-bot/.env
```

`OWNER_CHAT_ID` matters: your inquiries are forwarded to that chat, and
only that chat can use `/inquiries`. If it is left empty the bot still
works and stores everything — forwarding just stays off until it is set.

## 4. Start the bot

```bash
systemctl enable --now spot-satiralabs-bot
systemctl status spot-satiralabs-bot        # should show: active (running)
journalctl -u spot-satiralabs-bot -n 50     # should show "SatiraLabs bot starting (long polling)"
```

## 5. Verify

1. Open your bot in Telegram (t.me/<username>) and send `/start`.
   You should get the SatiraLabs greeting and the menu keyboard.
2. Tap **💬 Send an inquiry** and walk through the questions with test data.
3. On **Confirm**, check that:
   - the bot replies with a reference number (e.g. SL-0001),
   - a formatted copy of the inquiry arrives in your own chat (the forward),
   - sending `/inquiries` from your chat lists it.
4. Ask a friend (or a second account) to send a test inquiry and confirm
   that `/inquiries` from *their* chat does **not** show owner data.

## Day-to-day

- Inquiries are stored in `/opt/satiralabs-bot/data/inquiries.db` (SQLite)
  and forwarded to you as they arrive.
- Restart after code or .env changes: `systemctl restart spot-satiralabs-bot`
- Logs: `journalctl -u spot-satiralabs-bot -f`
- If the token ever leaks, talk to @BotFather → `/revoke`, then update
  `.env` with the new token and restart the service.
