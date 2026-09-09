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