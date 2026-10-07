"""
Тесты без Discord: временная БД + фейковые объекты discord.

Запуск из корня проекта:
    python3 -m unittest discover tests -v
"""

import asyncio
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import types
import unittest
import warnings
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP.name, "test.db")
os.environ["LOGO_DIR"] = os.path.join(_TMP.name, "logo")
os.environ["OWNER_ID"] = "1"
os.environ["DISCORD_TOKEN"] = ""
for name in ("WEB_PANEL_PORT", "SERVER_PORT", "HF_TOKEN"):
    os.environ.pop(name, None)
warnings.filterwarnings("ignore", category=DeprecationWarning)

import discord  # noqa: E402

import database  # noqa: E402
import core  # noqa: E402
import actions  # noqa: E402

OWNER, ADMIN, ADMIN2, STAFF, MEMBER = 1, 10, 11, 20, 30
GUILD = 500


# =========================
# FAKES
# =========================

class FakeRole:
    def __init__(self, role_id, position, perms=None, managed=False, default=False, name=None):
        self.id = role_id
        self.position = position
        self.permissions = discord.Permissions(**(perms or {}))
        self.managed = managed
        self._default = default
        self.name = name or f"role{role_id}"
        self.mention = f"<@&{role_id}>"

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position

    def __eq__(self, other):
        return isinstance(other, FakeRole) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


class FakeMember:
    def __init__(self, user_id, roles=(), manage_roles=False):
        self.id = user_id
        self.roles = list(roles)
        self.mention = f"<@{user_id}>"
        self.name = f"user{user_id}"
        self.guild_permissions = discord.Permissions(manage_roles=manage_roles)
        self.added, self.removed = [], []
        self.display_avatar = types.SimpleNamespace(url="https://cdn/avatar.png")

    @property
    def top_role(self):
        return max(self.roles, key=lambda r: r.position) if self.roles else FakeRole(0, 0, default=True)

    async def add_roles(self, role, reason=None):
        self.added.append(role.id)
        self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        self.removed.append(role.id)
        self.roles = [r for r in self.roles if r.id != role.id]


class FakeGuild:
    def __init__(self):
        self.id = GUILD
        self.owner_id = 999
        self.features = ["ROLE_ICONS"]
        self.everyone = FakeRole(GUILD, 0, default=True)
        self.bot_role = FakeRole(2, 50, perms={"manage_roles": True})
        self.me = FakeMember(777, [self.bot_role], manage_roles=True)
        self.roles_by_id = {}
        self.members = {}
        self.channels = {}
        self.name = "Тестовый сервер"
        self.member_count = 42
        self.premium_subscription_count = 3
        self.premium_tier = 1

    def add_role(self, role):
        self.roles_by_id[role.id] = role
        return role

    @property
    def roles(self):
        return list(self.roles_by_id.values())

    def get_role(self, role_id):
        return self.roles_by_id.get(role_id)

    def get_member(self, user_id):
        return self.members.get(user_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_channel_or_thread(self, channel_id):
        return self.channels.get(channel_id)


class FakeChannel:
    def __init__(self, guild, channel_id, allow=True):
        self.guild = guild
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.parent_id = None
        self.allow = allow
        self.sent = []
        guild.channels[channel_id] = self

    def permissions_for(self, who):
        return discord.Permissions(view_channel=self.allow, send_messages=self.allow, embed_links=True,
                                   send_messages_in_threads=self.allow, mention_everyone=False)

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return types.SimpleNamespace(id=self.id * 1000 + len(self.sent), channel=self)


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.modals = []
        self.done = False

    def is_done(self):
        return self.done

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.sent.append(content)

    async def send_modal(self, modal):
        self.done = True
        self.modals.append(modal)

    async def edit_message(self, **kwargs):
        self.done = True

    async def defer(self, **kwargs):
        self.done = True


class FakeInteraction:
    def __init__(self, guild, member):
        self.guild = guild
        self.user = member
        self.response = FakeResponse()
        self.followup = types.SimpleNamespace(send=self._followup)
        self.client = types.SimpleNamespace(user=guild.me)
        self.message = None
        self.followups = []

    async def _followup(self, content=None, **kwargs):
        self.followups.append(content)

    @property
    def last(self):
        return (self.response.sent + self.followups)[-1]


def run(coro):
    return asyncio.run(coro)


def setUpModule():
    database.init_database()
    core.reload_settings()
    core.ensure_default_actions()
    database.set_access_level(GUILD, ADMIN, "admin")
    database.set_access_level(GUILD, ADMIN2, "admin")
    database.set_access_level(GUILD, STAFF, "staff")


def make_guild():
    guild = FakeGuild()
    guild.safe = guild.add_role(FakeRole(100, 5, name="Художник"))
    guild.high = guild.add_role(FakeRole(101, 40, name="Старший"))
    guild.danger = guild.add_role(FakeRole(102, 6, perms={"administrator": True}, name="Админка"))
    guild.above_bot = guild.add_role(FakeRole(103, 60, name="Выше бота"))
    guild.managed = guild.add_role(FakeRole(104, 7, managed=True, name="Интеграция"))
    staff_role = FakeRole(105, 30, name="Staff")
    guild.add_role(staff_role)
    for uid, roles in ((OWNER, []), (ADMIN, [guild.high]), (ADMIN2, [guild.high]), (STAFF, [staff_role]), (MEMBER, [])):
        guild.members[uid] = FakeMember(uid, roles)
    return guild


# =========================
# TESTS
# =========================

class MigrationTests(unittest.TestCase):
    def test_legacy_database_is_upgraded(self):
        path = os.path.join(_TMP.name, "legacy.db")
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE bot_settings (guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (guild_id, key));
            INSERT INTO bot_settings VALUES (123, 'embed_color', '0x112233');
            CREATE TABLE action_registry (action_key TEXT PRIMARY KEY, min_level TEXT NOT NULL DEFAULT 'member',
                dangerous INTEGER NOT NULL DEFAULT 0, description TEXT, enabled INTEGER NOT NULL DEFAULT 1);
            INSERT INTO action_registry VALUES ('message.edit', 'admin', 1, 'x', 1);
            INSERT INTO action_registry VALUES ('role.assign', 'staff', 1, 'x', 1);
            CREATE TABLE sent_instances (id INTEGER PRIMARY KEY AUTOINCREMENT, build_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, sent_at INTEGER NOT NULL);
        """)
        con.commit()
        con.close()
        old = database.DATABASE_NAME
        database.DATABASE_NAME = path
        try:
            database.init_database()
            database.init_database()  # повторный старт ничего не ломает
            con = sqlite3.connect(path)
            settings = con.execute("SELECT guild_id, key, value FROM bot_settings").fetchall()
            self.assertEqual(settings, [(0, "embed_color", "0x112233")])
            self.assertEqual(con.execute("SELECT use_level FROM action_registry WHERE action_key='message.edit'").fetchone()[0], "admin")
            self.assertEqual(con.execute("SELECT use_level FROM action_registry WHERE action_key='role.assign'").fetchone()[0], "member")
            columns = {row[1] for row in con.execute("PRAGMA table_info(sent_instances)")}
            self.assertIn("part_index", columns)
            con.close()
        finally:
            database.DATABASE_NAME = old

    def test_template_favorites_become_per_user(self):
        tid = database.save_template(GUILD, STAFF, "fav", "message", "{}")
        con = database.get_connection()
        con.execute("UPDATE templates SET is_favorite=1 WHERE id=?", (tid,))
        con.commit()
        con.close()
        database._ensure_extended_tables()
        self.assertTrue(database.is_template_favorite(STAFF, tid))
        self.assertFalse(database.is_template_favorite(MEMBER, tid))
        database.set_user_template_favorite(MEMBER, tid, True)
        self.assertTrue(database.is_template_favorite(MEMBER, tid))
        database.set_user_template_favorite(STAFF, tid, False)
        database._ensure_extended_tables()  # флаг не должен "воскреснуть"
        self.assertFalse(database.is_template_favorite(STAFF, tid))


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.guild = make_guild()

    def i(self, uid):
        return FakeInteraction(self.guild, self.guild.members[uid])

    def test_levels(self):
        self.assertEqual(core.get_user_level(self.i(OWNER)), "owner")
        self.assertEqual(core.get_user_level(self.i(ADMIN)), "admin")
        self.assertEqual(core.member_level(self.guild, STAFF), "staff")
        self.assertEqual(core.member_level(self.guild, MEMBER), "member")

    def test_manage_only_strictly_lower(self):
        self.assertFalse(core.can_manage_user(self.i(ADMIN), ADMIN2))  # равный
        self.assertTrue(core.can_manage_user(self.i(ADMIN), STAFF))
        self.assertFalse(core.can_manage_user(self.i(ADMIN), ADMIN))   # сам себя
        self.assertFalse(core.can_manage_user(self.i(ADMIN), OWNER))
        self.assertFalse(core.can_manage_user(self.i(STAFF), MEMBER))  # staff не управляет
        self.assertTrue(core.can_manage_user(self.i(OWNER), ADMIN))

    def test_assign_admin_is_owner_only(self):
        self.assertFalse(core.can_assign_level(self.i(ADMIN), "admin"))
        self.assertTrue(core.can_assign_level(self.i(ADMIN), "staff"))
        self.assertTrue(core.can_assign_level(self.i(OWNER), "admin"))

    def test_denied_user_loses_individual_grant(self):
        database.add_command_access(GUILD, "embed", MEMBER)
        self.assertTrue(core.has_command_access(self.i(MEMBER), "embed"))
        database.deny_user(GUILD, MEMBER)
        try:
            self.assertFalse(core.has_command_access(self.i(MEMBER), "embed"))
        finally:
            database.undeny_user(GUILD, MEMBER)
            database.remove_command_access(GUILD, "embed", MEMBER)

    def test_role_problem(self):
        g = self.guild
        staff = g.members[STAFF]
        owner = g.members[OWNER]
        self.assertIsNone(core.role_problem(g, g.safe, staff))
        self.assertEqual(core.role_problem(g, g.danger, staff), "roles.dangerous")
        self.assertIsNone(core.role_problem(g, g.danger, owner))
        self.assertEqual(core.role_problem(g, g.above_bot, owner), "roles.above_bot")
        self.assertEqual(core.role_problem(g, g.high, staff), "roles.above_actor")
        self.assertEqual(core.role_problem(g, g.managed, owner), "roles.managed")
        self.assertEqual(core.role_problem(g, g.everyone, owner), "roles.everyone")
        self.assertEqual(core.role_problem(g, None, owner), "roles.not_found")


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.guild = make_guild()

    def test_normalize_legacy_and_new(self):
        legacy = actions.normalize_button({"label": "L", "style": "success", "action": "message", "value": "hi"})
        self.assertEqual((legacy["style"], legacy["action_key"]), ("green", "message.send"))
        link = actions.normalize_button({"label": "L", "action": "link", "value": "x.com"})
        self.assertEqual(link["style"], "link")
        new = actions.normalize_button({"label": "N", "style": "red", "action_key": "role.assign", "value": "5"})
        self.assertEqual((new["style"], new["action_key"]), ("red", "role.assign"))

    def test_components_are_persistent_and_fit_rows(self):
        async def build():
            buttons = [{"label": f"b{n}", "style": "blue", "action_key": "message.send", "value": "x"} for n in range(30)]
            interactive = {"type": "list", "options": [{"label": "o", "action_key": "message.send"}]}
            return actions.build_components("b", 7, buttons, interactive)

        view = run(build())
        ids = [child.custom_id for child in view.children]
        self.assertEqual(ids[0], "rb:l:b:7")
        self.assertEqual(ids[1], "rb:a:b:7:0")
        self.assertEqual(len(view.children), 1 + 20)  # 4 ряда кнопок после списка
        self.assertTrue(all(c.is_persistent() for c in view.children))

    def test_validate_values(self):
        i = FakeInteraction(self.guild, self.guild.members[STAFF])
        self.assertEqual(actions.validate_action_value(i, "role.assign", "<@&100>"), ("100", None))
        self.assertEqual(actions.validate_action_value(i, "role.assign", "102")[1], "roles.dangerous")
        self.assertEqual(actions.validate_action_value(i, "role.assign", "999")[1], "actions.role.bad_value")
        self.assertEqual(actions.validate_action_value(i, "form.trigger", "424242")[1], "actions.form_trigger.bad_value")
        self.assertEqual(actions.validate_action_value(i, None, "example.com", style="link"), ("https://example.com", None))
        self.assertEqual(actions.validate_action_value(i, "select.trigger", "nope")[1], "actions.select_trigger.bad_value")

    def test_role_button_respects_creator(self):
        g = self.guild
        member = g.members[MEMBER]
        i = FakeInteraction(g, member)
        run(actions.dispatch_action(i, "role.toggle", "100", creator_id=STAFF))
        self.assertEqual(member.added, [100])
        # повторное нажатие снимает роль
        i = FakeInteraction(g, member)
        run(actions.dispatch_action(i, "role.toggle", "100", creator_id=STAFF))
        self.assertEqual(member.removed, [100])
        # создатель понижен — кнопка перестаёт работать
        database.set_access_level(GUILD, STAFF, "member")
        try:
            i = FakeInteraction(g, member)
            run(actions.dispatch_action(i, "role.assign", "100", creator_id=STAFF))
            self.assertEqual(i.last, core.t("actions.creator_revoked"))
        finally:
            database.set_access_level(GUILD, STAFF, "staff")

    def test_role_turned_dangerous_after_creation(self):
        g = self.guild
        g.safe.permissions = discord.Permissions(administrator=True)
        i = FakeInteraction(g, g.members[MEMBER])
        run(actions.dispatch_action(i, "role.assign", "100", creator_id=STAFF, skip_confirmation=True))
        self.assertEqual(i.last, core.t("roles.dangerous"))
        self.assertEqual(g.members[MEMBER].added, [])

    def test_admin_only_action_blocked_for_member(self):
        i = FakeInteraction(self.guild, self.guild.members[MEMBER])
        run(actions.dispatch_action(i, "message.edit", "", creator_id=ADMIN))
        self.assertEqual(i.last, core.t("actions.no_access"))

    def test_build_visibility(self):
        g = self.guild
        bid = database.save_message_build(GUILD, STAFF, "secret", "", "[]", "[]", visibility="restricted",
                                          allowed_role_ids_json="[]", visibility_levels_json='["staff"]')
        row = database.get_message_build(bid)
        self.assertFalse(actions.build_visible(FakeInteraction(g, g.members[MEMBER]), row))
        self.assertTrue(actions.build_visible(FakeInteraction(g, g.members[STAFF]), row))
        self.assertTrue(actions.build_visible(FakeInteraction(g, g.members[ADMIN]), row))
        # restricted: ограничение действует и через кнопку
        i = FakeInteraction(g, g.members[MEMBER])
        run(actions.dispatch_action(i, "build.trigger", str(bid), creator_id=ADMIN))
        self.assertEqual(i.last, core.t("actions.build_no_access"))

    def test_build_trigger_shows_private_build_to_presser(self):
        g = self.guild
        embeds = json.dumps([{"title": "Правила"}, {"title": "FAQ"}])
        buttons = json.dumps([{"label": "Ок", "style": "blue", "action_key": "message.send", "value": "hi"}])
        bid = database.save_message_build(GUILD, STAFF, "info", "текст", embeds, buttons)  # private по умолчанию
        # кнопку собрал сам автор — участник видит build: текст+embed, затем второй embed с кнопками
        i = FakeInteraction(g, g.members[MEMBER])
        run(actions.dispatch_action(i, "build.trigger", str(bid), creator_id=STAFF))
        self.assertEqual(i.response.sent, ["текст"])
        self.assertEqual(len(i.followups), 1)
        # чужой приватный build создатель кнопки не видит — и нажавший тоже
        other = database.save_message_build(GUILD, ADMIN, "secret", "x", "[]", "[]")
        i = FakeInteraction(g, g.members[MEMBER])
        run(actions.dispatch_action(i, "build.trigger", str(other), creator_id=STAFF))
        self.assertEqual(i.last, core.t("actions.build_no_access"))
        # и при создании такую кнопку не собрать
        _, error = actions.validate_action_value(FakeInteraction(g, g.members[STAFF]), "build.trigger", str(other))
        self.assertEqual(error, "actions.build_trigger.bad_value")


class EmbedEditorTests(unittest.TestCase):
    def setUp(self):
        import embed_module
        self.em = embed_module
        self.guild = make_guild()

    def i(self):
        return FakeInteraction(self.guild, self.guild.members[STAFF])

    def test_empty_embeds_are_not_sent(self):
        em = self.em
        buttons = json.dumps([{"label": "Ок", "style": "blue", "action_key": "message.send", "value": "hi"}])
        # текст + кнопки, embed пустой -> одно сообщение без embed'а
        bid = database.save_message_build(GUILD, STAFF, "t", "привет", json.dumps([em.default_embed_data()]), buttons)

        async def check():
            parts = actions.message_parts(bid)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0]["content"], "привет")
            self.assertNotIn("embed", parts[0])
            self.assertIn("view", parts[0])
            # только кнопки: Discord не примет сообщение без текста и embed'а — оставляем пустой embed
            only = database.save_message_build(GUILD, STAFF, "b", "", json.dumps([em.default_embed_data()]), buttons)
            self.assertIn("embed", actions.message_parts(only)[0])
            # совсем пусто
            empty = database.save_message_build(GUILD, STAFF, "e", "", json.dumps([em.default_embed_data()]), "[]")
            self.assertEqual(actions.message_parts(empty), [])

        run(check())

    def test_bad_url_and_too_long_keep_state(self):
        em = self.em

        async def check():
            state = em.EmbedState(GUILD, STAFF)
            editor = em.EmbedEditorView(state, back_target=None)
            modal = em.EmbedMediaModal(state, editor)
            modal.image_input._value = "не ссылка"
            i = self.i()
            await modal.on_submit(i)
            self.assertIn("не похоже на ссылку", i.last)
            self.assertFalse(state.active_embed.get("image"))
            modal.image_input._value = "cdn.example.com/a.png"
            await modal.on_submit(self.i())
            self.assertEqual(state.active_embed["image"], "https://cdn.example.com/a.png")

            state.active_embed["fields"] = [{"name": "n", "value": "x" * 1024}] * 5
            modal = em.EmbedBasicModal(state, editor)
            modal.description_input._value = "y" * 1500
            i = self.i()
            await modal.on_submit(i)
            self.assertIn("максимум 6000", i.last)
            self.assertFalse(state.active_embed.get("description"))

        run(check())

    def test_field_edit_and_delete(self):
        em = self.em

        async def check():
            state = em.EmbedState(GUILD, STAFF)
            state.active_embed["fields"] = [{"name": f"f{n}", "value": "v"} for n in range(25)]
            editor = em.EmbedEditorView(state, back_target=None)  # 25 полей влезают в select
            modal = em.EmbedFieldModal(state, editor, index=3)
            self.assertEqual(modal.name_input.default, "f3")
            modal.name_input._value, modal.value_input._value = "новое", "знач"
            await modal.on_submit(self.i())
            self.assertEqual(state.active_embed["fields"][3]["name"], "новое")
            modal = em.EmbedFieldModal(state, editor, index=0)
            modal.name_input._value, modal.value_input._value = "", ""
            await modal.on_submit(self.i())
            self.assertEqual(len(state.active_embed["fields"]), 24)

        run(check())

    def test_builder_views_fit_and_remove_any(self):
        em = self.em

        async def check():
            state = em.EmbedState(GUILD, STAFF)
            state.buttons = [{"label": f"b{n}", "style": "blue", "action_key": "message.send", "value": ""} for n in range(20)]
            view = em.ButtonBuilderView(state, hub=None, back_target=("embed", None))
            select = next(c for c in view.children if isinstance(c, em.RemoveItemSelect))
            select._values = ["4"]
            await select.callback(self.i())
            self.assertEqual(len(state.buttons), 19)
            self.assertNotIn("b4", [b["label"] for b in state.buttons])
            em.ButtonColorView(state, {}, view, back_target=("embed", None))

        run(check())


class BuildToolsTests(unittest.TestCase):
    def setUp(self):
        import build_tools
        self.bt = build_tools
        self.guild = make_guild()

    def i(self, uid=STAFF):
        return FakeInteraction(self.guild, self.guild.members[uid])

    def test_parse_message_ref(self):
        p = self.bt.parse_message_ref
        self.assertEqual(p("https://discord.com/channels/500/600/700000000000000000"), (500, 600, 700000000000000000))
        self.assertEqual(p("https://ptb.discordapp.com/channels/500/600/700"), (500, 600, 700))
        self.assertEqual(p("123456789012345678-223456789012345678"), (None, 123456789012345678, 223456789012345678))
        self.assertEqual(p("700", default_channel_id=42), (None, 42, 700))
        self.assertIsNone(p("привет"))

    def test_message_to_payload_and_sanitize(self):
        bt = self.bt
        source = database.save_message_build(GUILD, STAFF, "src", "", "[]", json.dumps([
            {"label": "Роль", "style": "green", "action_key": "role.toggle", "value": str(self.guild.safe.id)},
        ]))
        row = discord.components.ActionRow({"type": 1, "components": [
            {"type": 2, "style": 3, "label": "Роль", "custom_id": f"rb:a:b:{source}:0"},
            {"type": 2, "style": 1, "label": "Чужая", "custom_id": "other_bot:42"},
            {"type": 2, "style": 5, "label": "Сайт", "url": "https://example.com"},
        ]})
        embed = discord.Embed(title="Заголовок", description="текст", color=0x112233)
        embed.add_field(name="a", value="b")
        embed.set_footer(text="низ")
        message = types.SimpleNamespace(content="привет", embeds=[embed], components=[row])
        payload = bt.message_to_payload(message)
        self.assertEqual(payload["embeds"][0]["title"], "Заголовок")
        self.assertEqual(payload["embeds"][0]["footer_text"], "низ")
        self.assertEqual(payload["buttons"][0]["action_key"], "role.toggle")  # своё действие скопировано
        self.assertIsNone(payload["buttons"][1]["action_key"])
        self.assertEqual(payload["buttons"][2]["style"], "link")

        clean, dropped = bt.sanitize_payload(self.i(), payload)
        self.assertEqual(dropped, 0)
        self.assertEqual(bt.unconfigured_count(clean), 1)
        # роль, которой на сервере нет, и опасная роль — действие сбрасывается
        payload["buttons"].append({"label": "x", "style": "blue", "action_key": "role.assign", "value": "999"})
        payload["buttons"].append({"label": "y", "style": "blue", "action_key": "role.assign", "value": str(self.guild.danger.id)})
        clean, dropped = bt.sanitize_payload(self.i(), payload)
        self.assertEqual(dropped, 2)
        # мусорные ссылки в embed'е вычищаются
        clean, _ = bt.sanitize_payload(self.i(), {"embeds": [{"title": "t", "image": "не ссылка", "color": "red"}]})
        self.assertIsNone(clean["embeds"][0]["image"])

    def test_export_roundtrip(self):
        bt = self.bt
        bid = database.save_message_build(GUILD, STAFF, "Правила сервера", "hi", json.dumps([{"title": "t"}]), "[]")
        name, data = bt.export_build(database.get_message_build(bid))
        self.assertTrue(name.endswith(f"_{bid}.json"))
        payload, error = bt.parse_export(data)
        self.assertIsNone(error)
        self.assertEqual(payload["name"], "Правила сервера")
        self.assertEqual(bt.parse_export(b"{not json")[1], "build_tools.json.bad_json")
        self.assertEqual(bt.parse_export(b'{"format": "other"}')[1], "build_tools.json.bad_format")

        async def check():
            i = self.i()
            i.channel = None
            await bt.open_imported(i, payload, name=payload["name"])
            self.assertIn("embed'ов 1", i.response.sent[0])

        run(check())

    def test_versions_and_restore(self):
        bt = self.bt
        bid = database.save_message_build(GUILD, STAFF, "v1", "первый", "[]", "[]")
        bt.remember_version(bid, STAFF)
        row = database.get_message_build(bid)
        database.update_message_build(bid, **{**database.build_snapshot(row), "content": "второй"})
        for _ in range(database.BUILD_VERSIONS_KEPT + 3):
            bt.remember_version(bid, STAFF)
        versions = database.get_build_versions(bid)
        self.assertEqual(len(versions), database.BUILD_VERSIONS_KEPT)

        first = database.save_message_build(GUILD, STAFF, "v", "старое", "[]", "[]")
        bt.remember_version(first, STAFF)
        version_id = database.get_build_versions(first)[0][0]
        row = database.get_message_build(first)
        database.update_message_build(first, **{**database.build_snapshot(row), "content": "новое"})

        async def check():
            view = bt.VersionActionsView(first, version_id, back_target=None)
            # участник без прав откатить не может
            i = self.i(MEMBER)
            await view.restore.callback(i)
            self.assertEqual(database.get_message_build(first)[4], "новое")
            await view.restore.callback(self.i(STAFF))
            self.assertEqual(database.get_message_build(first)[4], "старое")
            # откат тоже в истории: «новое» можно вернуть
            latest = database.get_build_version(database.get_build_versions(first)[0][0])
            self.assertEqual(latest[3]["content"], "новое")

        run(check())
        database.delete_message_build(first)
        self.assertEqual(database.get_build_versions(first), [])


class LiveAndAutomationTests(unittest.TestCase):
    def setUp(self):
        import automation
        import live
        self.automation, self.live = automation, live
        self.guild = make_guild()
        automation._bot = types.SimpleNamespace(get_guild=lambda gid: self.guild if gid == GUILD else None)
        automation._trigger_cache.clear()
        automation._cooldowns.clear()
        live._bot = None

    def test_parse_time(self):
        a = self.automation
        from datetime import datetime
        now = datetime(2026, 10, 6, 15, 0, tzinfo=self.live.TIMEZONE)
        self.assertEqual(a.parse_when("18:30", now).hour, 18)
        self.assertEqual(a.parse_when("14:00", now).day, 7)  # уже прошло -> завтра
        self.assertEqual(a.parse_when("25.12 9:05", now).month, 12)
        self.assertEqual(a.parse_when("01.01 10:00", now).year, 2027)
        self.assertEqual((a.parse_when("+2ч", now) - now).total_seconds(), 7200)
        self.assertEqual((a.parse_when("через 30 мин", now) - now).total_seconds(), 1800)
        self.assertIsNone(a.parse_when("вчера", now))
        self.assertIsNone(a.parse_when("31.02 10:00", now))
        self.assertEqual(a.parse_interval(""), 0)
        self.assertEqual(a.parse_interval("1 неделя"), 10080)
        self.assertEqual(a.parse_interval("12ч"), 720)
        self.assertIsNone(a.parse_interval("1м"))  # слишком часто
        self.assertIsNone(a.parse_when("+1 мес", now))  # не «1 минута»
        self.assertEqual(a.parse_duration("2 часа"), 120)
        self.assertEqual(a.parse_duration("3 недели"), 30240)

    def test_variables_and_inheritance(self):
        live = self.live
        database.change_counter(GUILD, "очки", set_to=7)
        parent = database.save_message_build(GUILD, STAFF, "parent", "", json.dumps([
            {"title": "P", "color": 0xFF0000, "footer_text": "Наш сервер"}]), "[]")
        child = database.save_message_build(GUILD, STAFF, "child", "Нас {member_count}, очков {counter:очки}, {unknown}",
                                            json.dumps([{"title": "Привет {user}", "color": 1}]), "[]")
        database.set_build_settings(child, {"parent_id": parent})
        self.assertEqual(database.get_child_builds(parent), [child])

        async def check():
            member = self.guild.members[MEMBER]
            parts = await live.parts_for(self.guild, child, user=member)
            self.assertEqual(parts[0]["content"], "Нас 42, очков 7, {unknown}")
            embed = parts[0]["embed"]
            self.assertEqual(embed.title, f"Привет <@{MEMBER}>")
            self.assertEqual(embed.color.value, 0xFF0000)  # цвет родителя
            self.assertEqual(embed.footer.text, "Наш сервер")

        run(check())

    def test_schedule_runs_and_reschedules(self):
        a = self.automation
        channel = FakeChannel(self.guild, 9001)
        bid = database.save_message_build(GUILD, STAFF, "news", "Новости", "[]", "[]")
        once = database.add_schedule(GUILD, bid, STAFF, channel.id, 100, 0)
        repeat = database.add_schedule(GUILD, bid, STAFF, channel.id, 100, 60)
        gone = database.add_schedule(GUILD, bid, 123456, channel.id, 100, 0)  # автора нет на сервере

        async def check():
            now = 100 + 3 * 3600 + 5
            for row in database.get_due_schedules(now):
                if row[2] == bid:
                    await a.run_schedule(row, now)

        run(check())
        self.assertEqual(len(channel.sent), 2)
        self.assertEqual(channel.sent[0]["content"], "Новости")
        self.assertEqual(database.get_schedule(once)[7], 0)  # разовое выключилось
        rep = database.get_schedule(repeat)
        self.assertEqual(rep[7], 1)
        self.assertGreater(rep[5], 100 + 3 * 3600 + 5)  # пропущенные запуски не досылаются
        self.assertIsNotNone(database.get_schedule(gone)[9])  # ошибка видна в списке
        self.assertEqual(len(database.get_sent_instances(bid)), 2)

    def test_keyword_trigger_with_cooldown(self):
        a = self.automation
        channel = FakeChannel(self.guild, 9002)
        bid = database.save_message_build(GUILD, STAFF, "faq", "Привет, {user}! Правила тут.", "[]", "[]")
        database.add_trigger(GUILD, bid, STAFF, "keyword", pattern="правила, rules", watch_channel_id=channel.id,
                             cooldown_seconds=60)

        def message(text, author=MEMBER, bot=False):
            member = self.guild.members[author]
            member.bot = bot
            return types.SimpleNamespace(guild=self.guild, author=member, webhook_id=None, channel=channel, content=text)

        async def check():
            await a.on_message(message("где ПРАВИЛА?"))
            await a.on_message(message("правила!"))  # пауза
            await a.on_message(message("ничего"))
            await a.on_message(message("rules", bot=True))  # боты не запускают триггеры

        run(check())
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(channel.sent[0]["content"], f"Привет, <@{MEMBER}>! Правила тут.")
        # персональное сообщение не попадает в «обновить отправленные»
        self.assertEqual(database.get_sent_instances(bid), [])

    def test_member_join_welcomes_everyone_and_cache_invalidates(self):
        a = self.automation
        channel = FakeChannel(self.guild, 9003)
        bid = database.save_message_build(GUILD, STAFF, "hi", "Привет, {user}!", "[]", "[]")

        async def join(uid):
            await a.on_member_join(types.SimpleNamespace(guild=self.guild, id=uid, mention=f"<@{uid}>",
                                                         display_name=f"u{uid}", name=f"u{uid}"))

        async def check():
            await join(MEMBER)  # триггера ещё нет — кэш запомнил пустой список
            trigger_id = database.add_trigger(GUILD, bid, STAFF, "member_join", target_channel_id=channel.id)
            a.invalidate_triggers(GUILD)
            await join(MEMBER)
            await join(ADMIN2)  # второй новичок в ту же паузу тоже получает приветствие
            await join(MEMBER)  # повтор того же человека — пауза
            self.assertEqual([m["content"] for m in channel.sent], [f"Привет, <@{MEMBER}>!", f"Привет, <@{ADMIN2}>!"])
            database.delete_trigger(trigger_id)
            a.invalidate_triggers(GUILD)
            a._cooldowns.clear()
            await join(STAFF)
            self.assertEqual(len(channel.sent), 2)

        run(check())

    def test_user_name_cannot_ping(self):
        async def check():
            user = types.SimpleNamespace(mention="<@5>", display_name="<@&777> @everyone", name="x")
            values = await self.live.collect(self.guild, {("user_name", None)}, user=user)
            self.assertNotIn("<@&777>", values["user_name"])
            self.assertNotIn("@everyone", values["user_name"])

        run(check())

    def test_resync_keeps_sends_grouped(self):
        live = self.live
        edits, deleted, counter = [], [], [1000]

        class Partial:
            def __init__(self, mid):
                self.id = mid

            async def edit(self, **kwargs):
                edits.append(self.id)

            async def delete(self):
                deleted.append(self.id)

        class Channel:
            id = 9100
            guild = self.guild

            def get_partial_message(self, mid):
                return Partial(mid)

            async def send(self, **kwargs):
                counter[0] += 1
                return types.SimpleNamespace(id=counter[0])

        self.guild.channels[Channel.id] = Channel()
        bid = database.save_message_build(GUILD, STAFF, "g", "", json.dumps([{"title": "1"}, {"title": "2"}]), "[]")
        for mid, part in ((1, 0), (2, 1), (3, 0), (4, 1)):  # две отправки по 2 сообщения
            database.save_sent_instance(bid, mid, Channel.id, GUILD, part)
        row = database.get_message_build(bid)
        database.update_message_build(bid, **{**database.build_snapshot(row),
                                              "embeds_json": json.dumps([{"title": "1"}, {"title": "2"}, {"title": "3"}])})

        async def check():
            first = await live.resync_build(self.guild, bid)
            self.assertEqual(first[2], 2)  # дослано по одному в каждую отправку
            second = await live.resync_build(self.guild, bid)
            self.assertEqual(second, (6, 0, 0, 0))  # повторно ничего не досылается и не удаляется
            groups = live._group_sends([(m, p) for m, _, _, _, p in database.get_sent_instances(bid)])
            self.assertEqual(sorted(len(g) for g in groups), [3, 3])

        run(check())
        self.assertEqual(deleted, [])

    def test_counter_variant_and_goto(self):
        g = self.guild
        self.assertEqual(actions.parse_counter("Очки"), ("очки", "+", 1))
        self.assertEqual(actions.parse_counter("очки =0"), ("очки", "=", 0))
        self.assertIsNone(actions.parse_counter("очки +x"))
        base = database.save_message_build(GUILD, STAFF, "base", "для всех", "[]", "[]", visibility="public")
        staff_view = database.save_message_build(GUILD, STAFF, "mod", "для staff", "[]", "[]")
        database.set_build_settings(base, {"variants": [{"level": "staff", "build_id": staff_view}]})

        async def check():
            i = FakeInteraction(g, g.members[MEMBER])
            await actions.dispatch_action(i, "build.trigger", str(base), creator_id=STAFF)
            self.assertEqual(i.response.sent, ["для всех"])
            i = FakeInteraction(g, g.members[STAFF])
            await actions.dispatch_action(i, "build.trigger", str(base), creator_id=STAFF)
            self.assertEqual(i.response.sent, ["для staff"])

            # шаг мастера: личное сообщение меняется на месте
            edits = []
            i = FakeInteraction(g, g.members[MEMBER])
            i.message = types.SimpleNamespace(flags=types.SimpleNamespace(ephemeral=True))

            async def edit_message(**kwargs):
                edits.append(kwargs)
            i.response.edit_message = edit_message
            await actions.dispatch_action(i, "build.goto", str(base), creator_id=STAFF)
            self.assertEqual(edits[0]["content"], "для всех")

            i = FakeInteraction(g, g.members[MEMBER])
            await actions.dispatch_action(i, "counter.change", "звёзды +5", creator_id=STAFF)
            self.assertIn("5", i.last)
            self.assertEqual(database.get_counters(GUILD)["звёзды"], 5)

        run(check())

    def test_panels_fit_discord_limits(self):
        a = self.automation
        bid = database.save_message_build(GUILD, STAFF, "ui", "x", "[]", "[]")
        for n in range(3):
            database.add_schedule(GUILD, bid, STAFF, 1, 10 ** 10, 60 * n)
            database.add_trigger(GUILD, bid, STAFF, "keyword", pattern="a")

        async def check():
            back = ("embed", None)
            a.SchedulesView(bid, back_target=back)
            a.TriggersView(bid, back_target=back)
            settings = a.BuildSettingsView(bid, back_target=back)
            a.VariantConditionView(settings, back_target=back)
            a.ScheduleItemView(bid, 1, back_target=back)
            i = FakeInteraction(self.guild, self.guild.members[STAFF])
            self.assertIn("{member_count}", a.settings_embed(i, bid).description)
            self.assertIn("#", a.schedules_embed(i, bid).description)

        run(check())


class HintsTests(unittest.TestCase):
    def test_panels_explain_their_buttons(self):
        g = make_guild()
        i = FakeInteraction(g, g.members[STAFF])
        home = core.panel_embed(i, "embed.home")
        self.assertIn("Конструктор сообщений", home.description)
        self.assertIn("**Из сообщения** — ", home.description)
        # карточка build'а показывает пояснения к кнопкам своей панели (embed.final)
        card = core.panel_embed(i, "embed.build_card", id=1, owner="x", visibility="public", embeds=1, buttons=0, sent=0)
        self.assertIn("**Расписание** — ", card.description)
        # переопределённая в веб-панели подпись попадает и в пояснение; пустое пояснение скрывается
        core.set_setting("embed.home.create", "Новое")
        core.set_setting("embed.home.saved.hint", "")
        try:
            text = core.panel_embed(i, "embed.home").description
            self.assertIn("**Новое** — ", text)
            self.assertNotIn("Мои сохранённые", text)
        finally:
            core.reset_setting("embed.home.create")
            core.reset_setting("embed.home.saved.hint")
        self.assertEqual(core.panel_embed(i, "embed.home", hints=False).description.count("▸"), 0)

    def test_every_hint_has_a_real_button(self):
        import ast
        import pathlib
        from texts import CATALOG
        buttons = set()
        for path in ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                prefix = next((st.value.value for st in cls.body if isinstance(st, ast.Assign)
                               and any(getattr(tg, "id", None) == "texts" for tg in st.targets)
                               and isinstance(st.value, ast.Constant)), None)
                for st in cls.body:
                    if prefix and isinstance(st, (ast.AsyncFunctionDef, ast.FunctionDef)) and any(
                            isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "button" for d in st.decorator_list):
                        buttons.add(f"{prefix}.{st.name}")
        stale = [key for key in CATALOG if key.endswith(".hint") and key[:-5] not in buttons]
        self.assertEqual(stale, [])


class ResyncTests(unittest.TestCase):
    def test_group_sends(self):
        import embed_module

        groups = embed_module._group_sends([(1, 0), (2, 1), (3, 0), (4, 1), (5, None), (6, None)])
        self.assertEqual(groups, [[1, 2], [3, 4], [5], [6]])


class TextTests(unittest.TestCase):
    def test_t_and_overrides(self):
        self.assertIn("5", core.t("embed.buttons.text", count=1, max=5, buttons=""))
        core.set_setting("common.cancelled", "Отмена {oops} {")
        self.assertEqual(core.t("common.cancelled", x=1), "Отмена {oops} {")
        core.reset_setting("common.cancelled")
        self.assertEqual(core.t("common.cancelled"), "Отменено.")

    def test_catalog_complete(self):
        import subprocess

        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_texts.py")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)


class WebPanelTests(unittest.TestCase):
    def test_validate(self):
        import web_panel

        self.assertEqual(web_panel.validate("embed_color", "#ff8800"), ("0xFF8800", None))
        self.assertIsNotNone(web_panel.validate("embed_color", "#12345")[1])
        self.assertIsNotNone(web_panel.validate("access.home.text", "Уровень {oops}")[1])
        self.assertEqual(web_panel.validate("access.home.text", "Уровень {level}"), ("Уровень {level}", None))
        self.assertEqual(web_panel.validate("forms.home.thumbnail", "none"), ("none", None))
        self.assertIn("logo.prompt.base", web_panel.editable_keys())

    def test_command_images_chain(self):
        g = make_guild()
        i = FakeInteraction(g, g.members[STAFF])
        try:
            core.set_setting("thumbnail.default", "https://cdn/default.png")
            core.set_setting("thumbnail.embed", "https://cdn/embed.png")
            core.set_setting("banner.embed", "https://cdn/embed-banner.png")
            # экран команды без своей картинки -> картинка команды; automation относится к /embed
            self.assertEqual(core.panel_embed(i, "embed.home").thumbnail.url, "https://cdn/embed.png")
            self.assertEqual(core.panel_embed(i, "automation.schedules", list="", tz="").thumbnail.url, "https://cdn/embed.png")
            self.assertEqual(core.panel_embed(i, "embed.home").image.url, "https://cdn/embed-banner.png")
            # другая команда -> общая картинка, баннера нет
            self.assertEqual(core.panel_embed(i, "forms.home").thumbnail.url, "https://cdn/default.png")
            self.assertIsNone(core.panel_embed(i, "forms.home").image.url)
            # своя картинка экрана и явное none
            core.set_setting("embed.home.thumbnail", "none")
            self.assertIsNone(core.panel_embed(i, "embed.home").thumbnail.url)
        finally:
            for key in ("thumbnail.default", "thumbnail.embed", "banner.embed", "embed.home.thumbnail"):
                core.reset_setting(key)

    def test_asset_upload(self):
        import web_panel
        from PIL import Image

        web_panel.ASSETS_DIR = pathlib.Path(_TMP.name) / "assets"
        buffer = io.BytesIO()
        Image.new("RGBA", (256, 256), (10, 20, 30, 0)).save(buffer, format="PNG")
        name, error = web_panel.save_asset(buffer.getvalue())
        self.assertIsNone(error)
        self.assertRegex(name, r"^[0-9a-f]{20}\.png$")
        self.assertEqual(web_panel.save_asset(buffer.getvalue())[0], name)  # тот же файл — то же имя
        self.assertIsNotNone(web_panel.save_asset(b"<svg onload=alert(1)>")[1])
        self.assertIsNotNone(web_panel.save_asset(b"x" * (web_panel.MAX_ASSET_BYTES + 1))[1])
        self.assertEqual(web_panel.validate("banner.embed", "none"), ("none", None))
        self.assertIn("banner.embed", web_panel.editable_keys())
        self.assertEqual(web_panel.group_of("banner.embed"), "style")

    def test_upload_rejects_non_object_body(self):
        import web_panel

        class Request:
            def __init__(self, body):
                self.body = body

            async def json(self):
                return self.body

        old = os.environ.get("WEB_PANEL_URL")
        os.environ["WEB_PANEL_URL"] = "http://127.0.0.1:1"
        try:
            for body in ([], "строка", {"data": "не base64!"}):
                response = run(web_panel.upload_asset(Request(body)))
                self.assertEqual(response.status, 400)
        finally:
            if old is None:
                os.environ.pop("WEB_PANEL_URL", None)
            else:
                os.environ["WEB_PANEL_URL"] = old


class LogoTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        self.Image = Image
        buffer = io.BytesIO()
        Image.new("RGB", (300, 200), (200, 20, 20)).save(buffer, format="PNG")
        self.png = buffer.getvalue()

    def test_icon_processing(self):
        import logo_module

        icon = logo_module.to_icon_png(logo_module.open_image(self.png))
        self.assertEqual(self.Image.open(io.BytesIO(icon)).size, (512, 512))
        noisy = self.Image.effect_noise((900, 900), 100).convert("RGB")
        buffer = io.BytesIO()
        noisy.save(buffer, format="PNG")
        small = logo_module.role_icon_bytes(buffer.getvalue())
        self.assertIsNotNone(small)
        self.assertLessEqual(len(small), logo_module.ROLE_ICON_MAX_BYTES)

    def test_style_references_dataset_and_zip_limits(self):
        import logo_module

        style_id = database.save_logo_style(GUILD, ADMIN, "Мой стиль", "neon outline")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("a.png", self.png)
            z.writestr("notes.txt", "x")
            z.writestr("broken.png", b"not an image")
        added, skipped = logo_module.import_zip(database.get_logo_style(style_id), archive.getvalue())
        self.assertEqual((added, skipped), (1, 2))
        row = database.get_logo_style(style_id)
        self.assertEqual(len(logo_module.references_of(row)), 1)
        buffer, trigger = logo_module.build_dataset_zip(row)
        names = zipfile.ZipFile(buffer).namelist()
        self.assertIn("001.png", names)
        self.assertIn("001.txt", names)
        self.assertTrue(trigger.startswith("rbstyle"))

    def test_generation_uses_style_and_lora(self):
        import logo_module

        calls = {}

        class FakeClient:
            async def text_to_image(self, prompt, **kwargs):
                calls["prompt"], calls["model"] = prompt, kwargs["model"]
                return self_image()

        def self_image():
            return self.Image.new("RGB", (1024, 1024), (0, 0, 0))

        style_id = database.save_logo_style(GUILD, ADMIN, "LoRA", "pastel glass", lora="me/my-icons-lora")
        original = logo_module._client
        logo_module._client = lambda: FakeClient()
        try:
            png = run(logo_module.generate_image("generate", database.get_logo_style(style_id), "кот-волшебник"))
        finally:
            logo_module._client = original
        self.assertEqual(calls["model"], "me/my-icons-lora")
        self.assertIn("pastel glass", calls["prompt"])
        self.assertIn("кот-волшебник", calls["prompt"])
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_role_icon_rules_and_suggestions(self):
        import logo_module

        g = make_guild()
        admin = g.members[ADMIN]
        admin.guild_permissions = discord.Permissions(manage_roles=True)
        self.assertIsNone(logo_module.role_icon_problem(g, g.safe, admin))
        self.assertEqual(logo_module.role_icon_problem(g, g.safe, g.members[MEMBER]), "logo.need_manage_roles")
        g.features = []
        self.assertEqual(logo_module.role_icon_problem(g, g.safe, admin), "logo.no_role_icons")
        g.features = ["ROLE_ICONS"]
        suggested = logo_module.suggest_roles(g, "иконка для художник", admin)
        self.assertEqual([r.id for r in suggested][:1], [100])

    def test_backend_problem_without_token(self):
        import logo_module

        self.assertEqual(logo_module.backend_problem(), "logo.no_token")


class ViewSmokeTests(unittest.TestCase):
    """Все панели и модалки собираются, подписи укладываются в лимиты Discord."""

    def test_views_and_modals(self):
        import bot
        import access_module as am
        import embed_module as em
        import extended_modules as ex
        import logo_module as lm

        async def build():
            st = em.EmbedState(GUILD, STAFF)
            fs = ex.FormState(GUILD, STAFF)
            bs = bot.ButtonSetState()
            back = (discord.Embed(title="x"), None)
            hub = em.InteractiveHubView(st, back)
            views = [
                am.AccessHomeView(bot.bot), am.UserLevelView(1, "@u", back), am.LevelDurationView(1, "@u", "staff", back),
                am.CommandAccessView("ping", "d", back), am.RoleLevelAccessView(1, "@r", back), am.SettingsView(bot.bot, back),
                am.DesignView(), am.ActionEditView("message.send", back),
                em.EmbedHomeView(GUILD, STAFF), em.VisibilityView(st, back), em.EmbedEditorView(st, back), hub,
                em.CustomListBuilderView(st, hub, back), em.NativeSelectTypeView(st, hub, back),
                em.ButtonBuilderView(st, hub, back), em.MessageBuildFinalView(1, back),
                actions.DangerousActionView("message.edit", None),
                ex.FormView(fs), ex.FormUseView(1), ex.FormStartView(GUILD, STAFF), ex.TemplateActions(1),
                ex.WebhookActions(1), ex.WebhookDeleteView(1), ex.WebhookRegenerateView(1), ex.WebhookHome(),
                ex.SelectHome(), ex.TemplateHome(), ex.RoleMenuActions(1),
                bot.StandaloneButtonStartView(GUILD, STAFF), bot.InlineButtonBuilderView(bs, back), bot.ButtonSetActions(1),
                lm.StyleActions(1),
            ]
            modals = [
                am.LevelDurationCustomModal(1, "@u", "staff"), am.DesignModal(),
                actions.MessageEditModal(types.SimpleNamespace(content="hi", embeds=[])), actions.WebhookSendModal(""),
                actions.WebhookSendModal(record_id=1),
                em.EmbedBasicModal(st, None), em.EmbedMediaModal(st, None), em.EmbedAuthorFooterModal(st, None),
                em.EmbedFieldModal(st, None), em.BuildMetaModal(st, None), em.ListOptionModal(st, None),
                em.ButtonLabelModal(st, None), em.ActionValueModal(st, {"action_key": "role.assign"}, "buttons", None),
                em.SaveAsTemplateModal(1),
                ex.FormBasicModal(fs, None), ex.FormQuestionModal(fs, None), ex.FormRoutingModal(fs, None),
                ex.TemplateModal(), ex.WebhookCreateModal(), ex.WebhookMessageModal(1), ex.RejectReasonModal(1, None),
                ex.RoleMenuModal([1, 2]), bot.InlineButtonModal(bs, back), bot.ButtonSetNameModal(bs, back),
                lm.StyleModal(),
            ]
            for view in views:
                for item in view.children:
                    label = getattr(item, "label", None)
                    if label is not None:
                        self.assertLessEqual(len(label), 80, (view, label))
                self.assertLessEqual(len(view.children), 25, view)
            for modal in modals:
                self.assertTrue(modal.title and len(modal.title) <= 45, modal)
                self.assertLessEqual(len(modal.children), 5, modal)
                for child in modal.children:
                    self.assertLessEqual(len(child.label), 45, (modal, child.label))
            # URL сохранённого вебхука не показывается нажавшему
            self.assertEqual(len(actions.WebhookSendModal(record_id=1).children), 1)
            return len(views), len(modals)

        n_views, n_modals = run(build())
        self.assertGreater(n_views, 30)
        self.assertGreater(n_modals, 20)

    def test_commands_registered(self):
        import bot

        names = sorted(c.name for c in bot.bot.tree.get_commands())
        self.assertEqual(names, sorted(["access", "buttons", "design", "embed", "forms", "logo", "messages",
                                        "panel", "ping", "select", "templates", "webhooks"]))


if __name__ == "__main__":
    unittest.main()
