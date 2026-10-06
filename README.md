# Discord bot

## Local start

1. Install dependencies:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Create `.env` from `.env.example` and fill in `DISCORD_TOKEN` and `OWNER_ID`.
3. Start the bot:

   ```bash
   python3 bot.py
   ```

## Hosting

Use `python3 bot.py` as the worker start command. Add `DISCORD_TOKEN` and
`OWNER_ID` as environment variables in the hosting service. Do not upload
`.env` or `bot.db` to GitHub.
## Web panel

All bot texts, colors and per-screen thumbnails live in `texts.py`; the owner
overrides them from a web panel that runs inside the bot process.

1. Add to `.env`:
   - `WEB_PANEL_PORT` — the port from the hosting panel's Network tab
     (falls back to `SERVER_PORT` on Pterodactyl hosts);
   - `WEB_PANEL_URL` — the address the browser opens, e.g. `http://1.2.3.4:25565`.
2. Restart the bot and run `/panel` in Discord (owner only). It replies with a
   one-time login link valid for 10 minutes; the browser session lasts 7 days.

After editing texts in code, run `python3 scripts/check_texts.py` to make sure
every key used in the code exists in the catalog.
