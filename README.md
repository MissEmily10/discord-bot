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

Panel images: each command has its own icon and banner
(`thumbnail.<command>`, `banner.<command>` in the **Оформление** section; every
screen of the command picks them up). Use **Загрузить** next to an image field
to upload a PNG/JPG straight from the browser — sizes and the full list are in
[docs/DESIGN_ASSETS.md](docs/DESIGN_ASSETS.md).

After editing texts in code, run `python3 scripts/check_texts.py` to make sure
every key used in the code exists in the catalog.

## Permissions model

- Levels: `limited < member < staff < admin < owner` (owner = `OWNER_ID`).
  A user can only change the level, access or ban of someone strictly below
  them; granting `admin` (to a user or a role) is owner-only. A ban beats any
  individual command grant.
- Action registry (`/access` → Settings → Action Registry): every button action
  has a **create** level (who may put it on a button) and a **use** level (who
  may press it). On every click the bot re-checks both the presser and the
  button's creator — demote the creator and their buttons stop working.
- Roles handed out by buttons, role menus, forms and `/logo` must be below the
  bot's and the creator's top role; roles with dangerous permissions
  (administrator, bans, manage server/roles/channels/webhooks, …) can only be
  handed out by the owner. This is checked when the button is created and again
  on every click.
- The bot never posts on someone's behalf where that person can't post, and
  `@everyone`/role pings go through only if the sender has that permission.
- Every sensitive change is written to the audit log (`/access` → Журнал).

Buttons, lists, form apply buttons, review buttons, role menus and `/logo`
results are persistent: they keep working after a restart and always read the
current data from the database.

## Message Build tools (`/embed`)

- **From an existing message:** `/embed` → **Из сообщения** (or
  `/embed from_message:<link>`) — paste a message link (right click → Copy
  Message Link) or an ID from the current channel. Content, embeds, buttons and
  lists are turned into a draft build. Buttons made by this bot keep their
  actions; other bots' buttons keep only their look and need a new action.
- **Export / import:** a build's card → **Экспорт JSON** gives a file;
  `/embed file:<json>` on any server opens it as a draft. Role, form, webhook
  and build references are re-checked for the new server and creator — the ones
  that don't fit are reset, not trusted.
- **History:** before every save the previous state is kept (last 25). A build's
  card → **История** → pick a version → preview or roll back. A rollback is
  itself saved, so it can be undone. Sent messages follow after
  **Обновить отправленные**.

## Automation and live messages

All of this lives on a build's card (`/messages` → a build):

- **Расписание** — post the build to a channel, thread or forum (a forum gets a
  new post) at a given time: `18:30`, `25.12 18:30`, `+2ч`; optionally repeat
  (`1д`, `12ч`, `1 неделя`, at most every 10 minutes). Times use `TIMEZONE`
  from `.env` (default `Europe/Moscow`). Missed runs while the bot was off are
  skipped, not sent in a burst.
- **Триггеры** — the build posts itself when something happens:
  a new member joins (to a chosen channel), a new thread / forum post appears
  (into that thread), or someone writes a keyword (replied right there; per
  channel or server-wide, with a cooldown). Keyword triggers need **Message
  Content Intent** enabled in the Developer Portal and
  `MESSAGE_CONTENT_INTENT=1` in `.env`; without it they only see messages that
  mention the bot.
- **Настройки** —
  - *live refresh*: every N minutes (≥5) sent messages are re-rendered;
  - *style parent*: the build takes the parent's color, and its author, footer
    and thumbnail when it has none; «Обновить отправленные» on the parent
    updates all children too;
  - *variants by role*: when the build is opened by a button, staff/admins or
    chosen roles see another build instead (e.g. a moderation panel).

Schedules and triggers run as the person who set them up and are re-checked on
every run (still on the server, still sees the build, may post there). Problems
are shown next to the item in its list.

**Variables** in text, titles, fields, author and footer:
`{server} {member_count} {online} {boosts} {boost_level} {date} {time}
{role:ROLE_ID} {counter:name} {submissions} {pending} {approved} {rejected}
{submissions:FORM_ID} {last_submission} {user} {user_name}`.
`{user}` is the person who pressed the button or triggered the build; messages
with it are personal and are not re-rendered later. Sent messages using
`{submissions}`/`{last_submission}` update after each form submission or
review; `{counter:…}` after each counter change.

**Button actions** added to the registry:
`build.goto` (a step: an ephemeral message rewrites itself into another build —
multi-step wizards), `build.refresh` (re-render another build's sent messages)
and `counter.change` (`points +1`, `points -1`, `points =0` — updates every sent
build that shows `{counter:points}`).

## Logo generator (`/logo`)

Role icons generated in your own style via Hugging Face Inference Providers.

1. Create a Hugging Face token (Settings → Access Tokens, "Make calls to
   Inference Providers") and put it into `.env` as `HF_TOKEN`.
2. `/logo styles` → **Новый стиль** — name and a short style description.
3. `/logo reference style:<style> file:<png or zip>` — upload your reference
   icons (up to 60; a zip with all 30 at once works).
4. Optional: **Описать по референсам** writes the style description for you
   from the references (vision model).
5. For an exact style match: **Датасет для LoRA** gives a zip with the icons and
   captions. Train a FLUX LoRA on it (e.g. fal-ai `flux-lora-fast-training` or
   Hugging Face AutoTrain), publish it as a HF repo and put the repo id into the
   style's **LoRA** field.
6. Generate: `/logo generate brief:"…"` (from scratch) or
   `/logo stylize image:<your mockup>` (redraws your icon in the style).
   Result buttons: put on a role (with name-based suggestions), another
   variant, remove background. Role icons need server boost level 2.

Models and limits are configurable in `.env` (see `.env.example`); prompts are
in the text catalog (`logo.prompt.*`) and editable in the web panel.

## Tests

```bash
python3 -m unittest discover tests -v
```

Runs without Discord: a temporary database and fake Discord objects cover
migrations, the permission model, button re-checks, role-escalation guards,
the web panel validation and the logo pipeline (with the model mocked).
