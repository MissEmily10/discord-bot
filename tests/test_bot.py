"""
Тесты без Discord: временная БД + фейковые объекты discord.

Запуск из корня проекта:
    python3 -m unittest discover tests -v
"""

import asyncio
import io
import time
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
for name in ("WEB_PANEL_PORT", "SERVER_PORT", "HF_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(name, None)
warnings.filterwarnings("ignore", category=DeprecationWarning)

import discord  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

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
                                   send_messages_in_threads=self.allow, mention_everyone=False,
                                   read_message_history=self.allow)

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return types.SimpleNamespace(id=self.id * 1000 + len(self.sent), channel=self)


def _shown(content, kwargs):
    """Что увидит человек: текст сообщения или — для карточек вроде «доступ запрещён» — описание embed'а."""
    if content is None and kwargs.get("embed") is not None:
        return kwargs["embed"].description
    return content


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.modals = []
        self.done = False

    def is_done(self):
        return self.done

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.sent.append(_shown(content, kwargs))

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
        self.followups.append(_shown(content, kwargs))

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
        self.assertEqual(i.last, core.t("actions.role.creator_dangerous"))
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
        # кнопку собрал сам автор — участник видит build одним сообщением (оба embed'а влезают)
        i = FakeInteraction(g, g.members[MEMBER])
        run(actions.dispatch_action(i, "build.trigger", str(bid), creator_id=STAFF))
        self.assertEqual(i.response.sent, ["текст"])
        self.assertEqual(len(i.followups), 0)
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


class SendThenSaveTests(unittest.TestCase):
    def setUp(self):
        import embed_module
        self.em = embed_module
        self.guild = make_guild()

    def interaction(self):
        i = FakeInteraction(self.guild, self.guild.members[STAFF])
        i.edits = []

        async def edit_original_response(**kwargs):
            i.edits.append(kwargs)
        i.edit_original_response = edit_original_response
        return i

    def listed_ids(self):
        return [row[0] for row in database.get_message_builds(GUILD)]

    def test_send_without_saving_then_save(self):
        em = self.em
        channel = FakeChannel(self.guild, 9200)
        state = em.EmbedState(GUILD, STAFF)
        state.content = "Объявление"

        async def check():
            hub = em.InteractiveHubView(state, back_target=None)
            i = self.interaction()
            await em.send_from_editor(i, state, [channel], hub)
            build_id = state.build_id
            self.assertEqual(channel.sent[0]["content"], "Объявление")
            # отправлено, работает, но в списках не видно
            self.assertNotIn(build_id, self.listed_ids())
            self.assertIn(build_id, [r[0] for r in database.get_message_builds(GUILD, include_hidden=True)])
            self.assertIsInstance(i.edits[-1]["view"], em.SaveAfterSendView)
            # повторная отправка того же черновика не плодит новые build'ы
            await em.send_from_editor(self.interaction(), state, [channel], hub)
            self.assertEqual(state.build_id, build_id)
            # «Сохранить» с названием -> появляется в списке
            modal = em.SaveAfterSendModal(build_id, state.name)
            modal.name_input._value = "Новости недели"
            await modal.on_submit(self.interaction())
            self.assertIn(build_id, self.listed_ids())
            self.assertEqual(database.get_message_build(build_id)[3], "Новости недели")
            # уже сохранённый: отправка не спрашивает и не прячет
            i = self.interaction()
            await em.send_from_editor(i, state, [channel], hub)
            self.assertIsInstance(i.edits[-1]["view"], em.MessageBuildFinalView)
            self.assertIn(build_id, self.listed_ids())

        run(check())

    def test_failed_send_leaves_no_junk(self):
        em = self.em
        closed = FakeChannel(self.guild, 9201, allow=False)
        state = em.EmbedState(GUILD, STAFF)
        state.content = "x"
        before = len(database.get_message_builds(GUILD, include_hidden=True))

        async def check():
            i = self.interaction()
            await em.send_from_editor(i, state, [closed], em.InteractiveHubView(state, back_target=None))
            self.assertIsNone(state.build_id)
            self.assertIn("НЕ ОТПРАВЛЕНО", i.edits[-1]["embed"].title)

        run(check())
        self.assertEqual(len(database.get_message_builds(GUILD, include_hidden=True)), before)

    def test_layout_single_or_split(self):
        em = self.em
        channel = FakeChannel(self.guild, 9300)
        state = em.EmbedState(GUILD, STAFF)
        state.embeds = [{"title": "Первый"}, {"title": "Второй"}, {"title": "Третий"}]

        async def check():
            hub = em.InteractiveHubView(state, back_target=None)
            # шаг выбора: «одним сообщением»
            view = em.LayoutChoiceView(state, back_target=None)
            await view.single.callback(self.interaction())
            self.assertEqual(state.layout, "single")
            await em.send_from_editor(self.interaction(), state, [channel], hub)
            self.assertEqual(len(channel.sent), 1)
            self.assertEqual([e.title for e in channel.sent[0]["embeds"]], ["Первый", "Второй", "Третий"])
            self.assertEqual(database.get_message_build(state.build_id)[14], "single")
            # «разными сообщениями» — три сообщения одной пачкой
            state.layout = "split"
            await em.send_from_editor(self.interaction(), state, [channel], hub)
            self.assertEqual(len(channel.sent), 4)
            # по кнопке — всегда одно сообщение
            parts = actions.message_parts(state.build_id)
            self.assertEqual(len(parts), 3)
            self.assertEqual(len(actions.button_message(parts)["embeds"]), 3)

        run(check())

    def test_single_layout_respects_discord_limit(self):
        em = self.em
        state = em.EmbedState(GUILD, STAFF)
        state.embeds = [{"description": "x" * 3500}, {"description": "y" * 3500}]
        state.layout = "single"
        self.assertEqual(em.state_problem(state)[0], "embed.layout.too_long")
        state.layout = "split"
        self.assertIsNone(em.state_problem(state))

    def test_plain_save_is_listed(self):
        em = self.em
        state = em.EmbedState(GUILD, STAFF)
        state.content = "сохранить"
        run(em.finish_message_build(self.interaction(), state))
        self.assertIn(state.build_id, self.listed_ids())


class MediaTests(unittest.TestCase):
    FOREIGN = "<:pepe:123456789012345678>"
    ANIMATED = "<a:dance:223456789012345678>"

    def setUp(self):
        import media
        from PIL import Image
        self.media = media
        media.ensure_table()
        media._app_emoji_ids.clear()
        media._app_emoji_names.clear()
        buffer = io.BytesIO()
        Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(buffer, format="PNG")
        self.png = buffer.getvalue()
        self.downloads, self.created = [], []
        self.fail_download = set()
        test = self

        async def fake_fetch(url, timeout=8, max_bytes=0):
            test.downloads.append(url)
            return None if any(part in url for part in test.fail_download) else test.png
        self._old_fetch = core.fetch_bytes
        core.fetch_bytes = fake_fetch

        class Emoji:
            def __init__(self, emoji_id, name, animated=False):
                self.id, self.name, self.animated = emoji_id, name, animated

            def __str__(self):
                return f"<{'a' if self.animated else ''}:{self.name}:{self.id}>"

        async def create_application_emoji(name, image):
            emoji = Emoji(900000000000000000 + len(test.created), name)
            test.created.append(emoji)
            return emoji

        local = {111111111111111111}
        self.bot = types.SimpleNamespace(
            get_emoji=lambda emoji_id: object() if emoji_id in local else None,
            create_application_emoji=create_application_emoji,
        )
        os.environ["WEB_PANEL_URL"] = "http://bot.example:8080"
        import web_panel
        web_panel.ASSETS_DIR = pathlib.Path(_TMP.name) / "assets"

    def tearDown(self):
        core.fetch_bytes = self._old_fetch
        os.environ.pop("WEB_PANEL_URL", None)

    def test_foreign_emoji_is_copied_once(self):
        m = self.media

        async def check():
            first = await m.mirror_emoji(self.bot, self.FOREIGN)
            self.assertRegex(first, r"^<:pepe:9\d+>$")
            again = await m.mirror_emoji(self.bot, self.FOREIGN)
            self.assertEqual(first, again)
            self.assertEqual(len(self.created), 1)  # вторую копию не создаёт
            # свой эмодзи и обычный unicode не трогаем
            self.assertEqual(await m.mirror_emoji(self.bot, "<:ours:111111111111111111>"), "<:ours:111111111111111111>")
            self.assertEqual(await m.mirror_emoji(self.bot, "🔥"), "🔥")
            await m.mirror_emoji(self.bot, self.ANIMATED)
            self.assertTrue(self.downloads[-1].endswith(".gif?size=128"))

        run(check())

    def test_prepare_payload(self):
        m = self.media
        self.fail_download.add("223456789012345678")  # анимированный не скачался
        self.fail_download.add("old.png")              # протухшая ссылка
        payload = {
            "content": f"Привет {self.FOREIGN}!",
            "embeds": [{"title": "t", "image": "https://cdn.discordapp.com/attachments/1/2/pic.png?ex=1&hm=2",
                        "thumbnail": "https://media.discordapp.net/attachments/1/2/old.png", "fields": []}],
            "buttons": [{"label": "a", "emoji": self.FOREIGN}, {"label": "b", "emoji": self.ANIMATED}],
            "interactive": {"type": "list", "options": [{"label": "o", "emoji": "🙂"}]},
        }
        self.assertTrue(m.needs_work(self.bot, payload))

        async def check():
            warnings = await m.prepare(self.bot, payload)
            self.assertNotIn("123456789012345678", payload["content"])
            self.assertRegex(payload["buttons"][0]["emoji"], r"^<:pepe:9\d+>$")
            self.assertIsNone(payload["buttons"][1]["emoji"])  # не скопировался — убран, иначе кнопку не примут
            self.assertTrue(payload["embeds"][0]["image"].startswith("http://bot.example:8080/assets/"))
            self.assertIsNone(payload["embeds"][0]["thumbnail"])
            keys = [key for key, _ in warnings]
            self.assertIn("media.expired", keys)
            self.assertIn("media.emoji_failed", keys)
            self.assertFalse(m.needs_work(self.bot, payload))

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

    def test_import_from_other_server(self):
        bt = self.bt
        other = make_guild()
        other.id = 600
        other.members.pop(MEMBER)
        channel = FakeChannel(other, 7001)
        message = types.SimpleNamespace(id=1)

        async def fetch_message(message_id):
            return message
        channel.fetch_message = fetch_message
        link = "https://discord.com/channels/600/7001/123"

        async def check():
            i = self.i(MEMBER)
            i.channel = None
            i.client = types.SimpleNamespace(get_guild=lambda gid: None)
            self.assertEqual((await bt.fetch_message_for(i, link))[1], "build_tools.import.bot_not_there")
            i.client = types.SimpleNamespace(get_guild=lambda gid: other if gid == 600 else None)
            self.assertEqual((await bt.fetch_message_for(i, link))[1], "build_tools.import.not_member")
            i = self.i(STAFF)
            i.channel = None
            i.client = types.SimpleNamespace(get_guild=lambda gid: other if gid == 600 else None)
            self.assertIs((await bt.fetch_message_for(i, link))[0], message)
            channel.allow = False  # на том сервере канал закрыт для тебя
            self.assertEqual((await bt.fetch_message_for(i, link))[1], "build_tools.import.no_access")

        run(check())

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

    def test_counter_button_changes_live_pictures(self):
        import cards
        import card_templates as ct
        rating = cards.get_card(cards.create_card(GUILD, STAFF, "Кики", "rating"))
        rating["data"]["rows"] = [{"name": "Kick", "score": 3}]
        cards.save_card(rating)
        counter = cards.get_card(cards.create_card(GUILD, STAFF, "Очки", "counter"))
        counter["command"] = "kick"
        cards.save_card(counter)
        g = make_guild()

        async def check():
            i = FakeInteraction(g, g.members[MEMBER])
            await actions.dispatch_action(i, "counter.change", "kick +2", creator_id=STAFF)
            self.assertEqual(cards.get_card(rating["id"])["data"]["rows"][0]["score"], 5)
            main = ct.spec("counter")["fields"][0][0]
            self.assertEqual(cards.get_card(counter["id"])["data"][main], 2)
            await actions.dispatch_action(FakeInteraction(g, g.members[ADMIN]), "counter.change", "Kick =10",
                                          creator_id=STAFF)
            self.assertEqual(cards.get_card(rating["id"])["data"]["rows"][0]["score"], 10)
            self.assertFalse(cards.apply_counter(cards.get_card(rating["id"]), "нет такого", "+1"))

        run(check())

    def test_counter_variable_ignores_case(self):
        import live
        g = make_guild()
        database.change_counter(GUILD, "лапки", delta=4)
        values = run(live.collect(g, {("counter", "Лапки ")}))
        self.assertEqual(values["counter"]("Лапки "), 4)

    def test_counter_report_and_attached_buttons_kept(self):
        import cards
        g = make_guild()
        channel = FakeChannel(g, 9991)
        g.channels[9991] = channel
        edits = []

        class Row:
            children = [type("B", (), {"custom_id": "rb:a:b:1:0"})()]

        class Picture:
            components = [Row()]

            async def edit(self, **kwargs):
                edits.append(kwargs)

        async def fetch_message(mid):
            if mid == 1:
                return Picture()
            raise discord.NotFound(type("R", (), {"status": 404, "reason": ""})(), "gone")
        channel.fetch_message = fetch_message
        shown = cards.get_card(cards.create_card(GUILD, STAFF, "Хвосты", "counter"))
        shown["channel_id"], shown["message_id"] = 9991, 1
        cards.save_card(shown)
        gone = cards.get_card(cards.create_card(GUILD, STAFF, "хвосты", "progress"))
        gone["channel_id"], gone["message_id"] = 9991, 2
        cards.save_card(gone)
        cards.create_card(GUILD, STAFF, "Хвосты!", "counter")  # не опубликована
        report = dict()
        for title, status in run(cards.counter_changed(g, "хвосты", "+", 1)):
            report[title] = status
        self.assertEqual(report, {"Хвосты": "updated", "хвосты": "message_gone", "Хвосты!": "not_published"})
        self.assertNotIn("view", edits[0])  # прикреплённые под картинку кнопки не стираются
        self.assertTrue(edits[0]["attachments"][0].filename.startswith("card-"))

        async def check():
            i = FakeInteraction(g, g.members[ADMIN])
            await actions.dispatch_action(i, "counter.change", "хвосты +1", creator_id=STAFF)
            self.assertIn("не опубликована", i.last)
            i = FakeInteraction(g, g.members[ADMIN])
            await actions.dispatch_action(i, "counter.change", "никто +1", creator_id=STAFF)
            self.assertIn("Ни одна живая картинка", i.last)
        run(check())

    def test_counter_button_matches_title_and_table_text(self):
        import cards
        titled = cards.get_card(cards.create_card(GUILD, STAFF, "Kick", "counter"))
        self.assertTrue(cards.apply_counter(titled, "kick", "+1"))
        self.assertEqual(titled["data"]["value"], 1)
        table = cards.get_card(cards.create_card(GUILD, STAFF, "Табло", "table"))
        table["data"]["rows"] = [["Kick", "3", "пт"]]
        self.assertTrue(cards.apply_counter(table, "KICK", "+2"))
        self.assertEqual(table["data"]["rows"][0][1], "5")
        chart = cards.get_card(cards.create_card(GUILD, STAFF, "Активность", "chart"))
        chart["data"]["rows"] = [["Kick", 4]]
        self.assertTrue(cards.apply_counter(chart, "kick", "10"))  # «=10» кнопка передаёт как «10»
        self.assertEqual(chart["data"]["rows"][0][1], 10)
        self.assertFalse(cards.apply_counter(table, "нет", "+1"))

    def test_all_triggers_entry(self):
        import embed_module as em
        bid = database.save_message_build(GUILD, STAFF, "Привет", "x", "[]", "[]")
        database.add_trigger(GUILD, bid, STAFF, "keyword", pattern="привет")
        i = FakeInteraction(make_guild(), make_guild().members[ADMIN])
        self.assertIn("Привет", em.all_triggers_embed(i).description)
        self.assertTrue(any(isinstance(c, discord.ui.Select) for c in em.AllTriggersView(i).children))

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


class PaginationTests(unittest.TestCase):
    def labels(self, view):
        return [[c.get("label") or c.get("placeholder") or (c.get("emoji") or {}).get("name") for c in row["components"]]
                for row in view.to_components()]

    def test_max_five_actions_per_page(self):
        import embed_module as em

        async def check():
            view = em.MessageBuildFinalView(1, back_target=("e", None))
            rows = self.labels(view)
            self.assertEqual(rows[0][:2], ["Отправить", "Редактировать"])  # важное — первым
            self.assertLessEqual(len(rows[0]), 5)
            self.assertIn("стр. 1/3", rows[-1])
            self.assertIn(core.t("nav_back"), rows[-1])  # «Назад» — в ряду навигации, не в лимите
            # ▶ листает дальше, ◀ с первой — на последнюю
            nav = [c for c in view.children if getattr(c, "_panel_nav", False) and c.emoji and not c.label]
            i = FakeInteraction(make_guild(), make_guild().members[STAFF])
            edits = []

            async def edit_message(**kwargs):
                edits.append(kwargs)
            i.response.edit_message = edit_message
            await nav[-1].callback(i)
            self.assertIn("стр. 2/3", self.labels(view)[-1])
            await nav[0].callback(i)
            await nav[0].callback(i)
            self.assertIn("стр. 3/3", self.labels(view)[-1])
            self.assertIs(edits[-1]["view"], view)
            # маленькие панели не трогаем
            small = em.SaveAfterSendView(1, em.EmbedState(1, 1))
            self.assertFalse(any("стр." in str(label) for row in self.labels(small) for label in row))

        run(check())


class HubTests(unittest.TestCase):
    def labels(self, view):
        return [c.get("label") for row in view.to_components() for c in row["components"]]

    def test_hub_panels_render(self):
        import hubs

        async def check():
            g = make_guild()
            i = FakeInteraction(g, g.members[STAFF])
            self.assertEqual(self.labels(hubs.LibraryHome())[:2], ["Сообщения", "Шаблоны"])
            self.assertEqual(self.labels(hubs.InteractiveHome(lambda gid, uid: None))[:3],
                             ["Наборы кнопок", "Меню ролей", "Формы"])
            emin = self.labels(hubs.EminemovnaHome(bot=None))
            self.assertEqual(emin, ["Доступ", "Оформление", "Стили иконок", "Веб-панель", "Бункер"])
            for context in ("library.home", "interactive.home", "eminemovna.home", "forms.review_home"):
                self.assertTrue(core.panel_embed(i, context).title)

        run(check())

    def test_forms_review_flow(self):
        import hubs
        g = make_guild()
        form_id = database.save_form(GUILD, STAFF, "Набор в модераторы", "", json.dumps([{"label": "Возраст"}]),
                                     reviewer_user_ids_json=json.dumps([STAFF]))
        sid = database.save_submission(form_id, GUILD, MEMBER, json.dumps({"Возраст": "20"}))

        async def check():
            reviewer = FakeInteraction(g, g.members[STAFF])
            forms = hubs.reviewable_forms(reviewer)
            self.assertEqual([(f[0], n) for f, n in forms if f[0] == form_id], [(form_id, 1)])
            # обычный участник чужие заявки не видит
            self.assertFalse(any(f[0] == form_id for f, _ in hubs.reviewable_forms(FakeInteraction(g, g.members[MEMBER]))))
            card = hubs.submission_embed(reviewer, sid)
            self.assertEqual(card.fields[0].value, "20")
            view = hubs.SubmissionReviewView(sid, back_target=None)
            await view.approve.callback(reviewer)
            self.assertEqual(database.get_submission(sid)[5], "approved")
            self.assertEqual(database.count_pending_submissions(form_id), 0)

        run(check())

    def test_old_grants_move_to_new_commands(self):
        database.grant_command_access(GUILD, "buttons", MEMBER) if hasattr(database, "grant_command_access") else None
        connection = database.get_connection()
        connection.execute("INSERT OR IGNORE INTO command_access (guild_id, command_name, user_id, expires_at) VALUES (?, 'templates', ?, NULL)", (GUILD, ADMIN2))
        connection.commit(); connection.close()
        database.migrate_command_grants()
        connection = database.get_connection()
        rows = connection.execute("SELECT command_name FROM command_access WHERE guild_id = ? AND user_id = ?", (GUILD, ADMIN2)).fetchall()
        connection.close()
        self.assertIn(("library",), rows)


class WebhookSendTests(unittest.TestCase):
    def test_temporary_webhook_is_created_used_and_deleted(self):
        import live
        g = make_guild()
        log = []

        class Hook:
            id = 42

            async def send(self, wait=False, **kwargs):
                log.append(("send", kwargs))
                return types.SimpleNamespace(id=len(log), channel=channel)

            async def delete(self, reason=None):
                log.append(("delete", None))

        class Channel(FakeChannel):
            managers = {777, ADMIN}  # «Управлять вебхуками» — у бота и у админа

            def permissions_for(self, who):
                perms = super().permissions_for(who)
                perms.manage_webhooks = who.id in self.managers
                return perms

            async def create_webhook(self, name, avatar=None, reason=None):
                log.append(("create", name, avatar))
                return Hook()

        channel = Channel(g, 9500)
        buttons = json.dumps([{"label": "Ок", "style": "blue", "action_key": "message.send", "value": "hi"}])
        bid = database.save_message_build(GUILD, ADMIN, "wh", "Привет", "[]", buttons)

        async def check():
            # нет права «Управлять вебхуками» и не admin — нельзя (иначе любой пишет от любого имени)
            _, error = await live.deliver_temp_webhook(g, g.members[STAFF], bid, channel, "Ведущий")
            self.assertIn("Управлять вебхуками", error)
            self.assertEqual(log, [])
            message, error = await live.deliver_temp_webhook(g, g.members[ADMIN], bid, channel, "Ведущий", b"png")
            self.assertIsNone(error)
            self.assertEqual(log[0], ("create", "Ведущий", b"png"))
            self.assertEqual(log[1][1]["content"], "Привет")
            self.assertIn("view", log[1][1])           # кнопки уходят и через вебхук
            self.assertEqual(log[-1], ("delete", None))  # вебхук сразу удалён
            self.assertEqual(database.get_sent_instances(bid), [])  # такие сообщения не обновляются

        run(check())


class PreviewPictureTests(unittest.TestCase):
    """Картинка-предпросмотр: разбор разметки и эмодзи, рисование, кнопки в конструкторе."""

    def test_emoji_and_markdown(self):
        import preview_render as pr
        codes = [pr.twemoji_code(seq) for _, _, seq in pr.emoji_matches("Привет 👋 ★ ✓ ✅ ❤️ 👨‍👩‍👧 🇷🇺 1️⃣ 👍🏽")]
        self.assertEqual(codes, ["1f44b", "2705", "2764", "1f468-200d-1f469-200d-1f467", "1f1f7-1f1fa",
                                 "31-20e3", "1f44d-1f3fd"])
        pieces = pr.parse_inline("**жир** <@123456789012345678> <:pepe:123456789012345678> 2*3",
                                 {("user", 123456789012345678): "Эмилия"}, pr.DEFAULT_LABELS)
        self.assertIn("bold", pieces[0]["style"])
        self.assertEqual([p["text"] for p in pieces if p["kind"] == "pill"], ["@Эмилия"])
        self.assertEqual([p["id"] for p in pieces if p["kind"] == "cemoji"], ["123456789012345678"])
        self.assertTrue("".join(p["text"] for p in pieces if p["kind"] == "text").endswith("2*3"))

    def test_render_split_is_taller_than_single(self):
        import preview_render as pr
        data = {"content": "Текст 👋", "embeds": [
            {"title": "Первый", "description": "# Заголовок\n> цитата\n```\nкод\n```",
             "fields": [{"name": "a", "value": "1", "inline": True}, {"name": "b", "value": "2", "inline": True}],
             "image": "https://example.com/missing.png", "footer_text": "подпись", "timestamp": True},
            {"title": "Второй", "thumbnail": "https://example.com/t.png"},
        ], "buttons": [{"label": "Сайт", "style": "link", "value": "https://example.com"}],
            "interactive": None, "layout": "single"}
        heights = {}
        for layout in ("single", "split"):
            parts = actions.message_parts(0, None, dict(data, layout=layout))
            png = pr.render(pr.messages_from_parts(parts), {"name": "Бот", "time": "12:00"}, theme="light")
            picture = PILImage.open(io.BytesIO(png))
            self.assertEqual(picture.width, pr.WIDTH * pr.SCALE)
            heights[layout] = picture.height
        self.assertGreater(heights["split"], heights["single"])

    def test_editor_and_saved_build_send_picture(self):
        import embed_module as em
        import preview_render as pr

        async def no_network(urls):
            return {}

        async def check():
            original = pr.fetch_assets
            pr.fetch_assets = no_network
            try:
                g = make_guild()
                state = em.EmbedState(GUILD, STAFF)
                state.embeds = [{"title": "Привет", "description": "**текст**"}, {"title": "Второй"}]
                state.buttons = [{"label": "Кнопка", "style": "primary", "action_key": "message.send", "value": "x"}]
                sent = []
                for view in (em.EmbedEditorView(state, back_target=None), em.InteractiveHubView(state, back_target=None)):
                    i = FakeInteraction(g, g.members[STAFF])

                    async def capture(content=None, **kwargs):
                        sent.append(kwargs)

                    i.followup = types.SimpleNamespace(send=capture)
                    await view.preview.callback(i)
                self.assertEqual(len(sent), 2)
                self.assertTrue(all(item["file"].filename == "preview.png" for item in sent))
                self.assertEqual(sent[0]["file"].fp.read(8), b"\x89PNG\r\n\x1a\n")
                labels = [c.label for c in sent[0]["view"].children]
                self.assertEqual(labels, ["Светлая тема"])
                # пустой редактор — без картинки, с объяснением
                empty = em.EmbedState(GUILD, STAFF)
                i = FakeInteraction(g, g.members[STAFF])
                await em.EmbedEditorView(empty, back_target=None).preview.callback(i)
                self.assertEqual(i.last, core.t("embed.build_empty"))
            finally:
                pr.fetch_assets = original

        run(check())


class FormPackTests(unittest.TestCase):
    """Паки форм: меню в сообщении, карточка формы, баннеры с должностью."""

    def test_english_names(self):
        import form_packs as fp
        self.assertEqual(fp.quick_english("Модератор"), "moderator")
        self.assertEqual(fp.quick_english("Старший модератор"), "senior moderator")
        self.assertEqual(fp.quick_english("Ивентер"), "eventer")
        self.assertEqual(fp.quick_english("Шашлычник"), "shashlychnik")
        self.assertEqual(fp.quick_english("Support"), "support")
        self.assertEqual(run(fp.to_english("Хелпер")), "helper")

    def test_template_banners(self):
        import banner_render as br
        for screen in br.TEMPLATES:
            self.assertTrue(br.is_template(screen), screen)
        short = PILImage.open(io.BytesIO(br.render("forms.public_card", name="Модератор")))
        self.assertEqual(short.size, (1200, 200))
        long_png = br.render("forms.public_card", name="Очень длинное название должности на сервере")
        self.assertEqual(PILImage.open(io.BytesIO(long_png)).size, (1200, 200))
        core.set_setting("forms.approved.banner", "https://cdn/own.png")
        try:
            self.assertFalse(br.is_template("forms.approved"))  # своя картинка — без текста
        finally:
            core.reset_setting("forms.approved.banner")

    def test_pack_menu_card_and_review_banner(self):
        import form_packs as fp
        from extended_modules import decide
        g = make_guild()
        form_id = database.save_form(GUILD, STAFF, "Модератор", "Следит за чатами", json.dumps([{"label": "Возраст"}]),
                                     reviewer_user_ids_json=json.dumps([STAFF]))
        pack_id = fp.save_pack(GUILD, STAFF, "Служебные роли", "Куда подать заявку")
        fp._doodles["doodle_star"] = "<:doodle_star:123456789012345678>"
        fp.set_meta(form_id, name_en="moderator", emoji=fp.pick_emoji(GUILD))
        fp.set_item(pack_id, form_id, "порядок в чатах")
        # меню в сообщении
        data = {"content": "", "embeds": [{"title": "Куда подать заявку"}], "buttons": [],
                "interactive": {"type": "form_packs", "packs": [pack_id]}, "layout": "single"}
        parts = actions.message_parts(0, None, data)
        rows = parts[-1]["view"].to_components()
        select = rows[0]["components"][0]
        self.assertEqual(select["custom_id"], f"rb:fp:{pack_id}")
        self.assertEqual(select["placeholder"], "Служебные роли")
        self.assertEqual(select["options"][0]["label"], "Модератор")
        self.assertEqual(select["options"][0]["description"], "порядок в чатах")
        self.assertEqual(select["options"][0]["emoji"]["name"], "doodle_star")
        # выбор формы -> карточка с баннером и «Подать заявку»
        i = FakeInteraction(g, g.members[MEMBER])
        shown = {}

        async def send_message(**kwargs):
            shown.update(kwargs)

        i.response.send_message = send_message
        item = fp.FormPackSelect(pack_id, select=discord.ui.Select(custom_id=f"rb:fp:{pack_id}",
                                                                  options=[discord.SelectOption(label="x")]))
        item.item._values = [str(form_id)]
        run(item.callback(i))
        self.assertEqual(shown["file"].filename, "banner.png")
        self.assertEqual(shown["embeds"][0].image.url, "attachment://banner.png")
        self.assertEqual(shown["embeds"][1].title, "Модератор")
        self.assertEqual(shown["view"].children[0].custom_id, f"rb:fa:{form_id}")
        self.assertTrue(shown["ephemeral"])
        # итог проверки: баннер с должностью по-английски вложением
        sid = database.save_submission(form_id, GUILD, MEMBER, json.dumps({"Возраст": "20"}))
        edits = []

        async def edit(**kwargs):
            edits.append(kwargs)

        reviewer = FakeInteraction(g, g.members[STAFF])
        reviewer.response.done = True
        card = core.panel_embed(reviewer, "forms.submission", form="Модератор", user="<@1>", id=sid)
        run(decide(reviewer, sid, "rejected", "мало опыта", types.SimpleNamespace(embeds=[card], edit=edit)))
        self.assertEqual(edits[0]["attachments"][0].filename, "banner.png")
        self.assertEqual(edits[0]["embeds"][0].image.url, "attachment://banner.png")
        # чужой сервер не может подсунуть пак в импорт
        self.assertIsNone(fp.clean_interactive(GUILD + 1, {"type": "form_packs", "packs": [pack_id]}))
        fp._doodles.clear()


class LiveCardTests(unittest.TestCase):
    """Живые картинки: рейтинг, правка по кнопке, доступ выбранных людей и ролей."""

    def test_scores_and_render(self):
        import cards
        card = {"title": "Ивент", "data": {"rows": []}}
        self.assertIsNone(cards.set_score(card, "Эмилия", "10"))
        self.assertIsNone(cards.set_score(card, "эмилия", "+5"))
        self.assertIsNone(cards.set_score(card, "Кот", "15"))
        self.assertIsNone(cards.set_score(card, "Пёс", "-3"))
        self.assertEqual(cards.set_score(card, "X", "много"), "cards.error.bad_value")
        self.assertEqual(cards.set_score(card, " ", "1"), "cards.error.no_name")
        rows = cards.sorted_rows(card)
        self.assertEqual([(r["name"], r["score"]) for r in rows], [("Кот", 15), ("Эмилия", 15), ("Пёс", -3)])
        png = cards.render_rating(card["title"], rows)
        self.assertEqual(PILImage.open(io.BytesIO(png)).width, 1200)
        self.assertEqual(PILImage.open(io.BytesIO(cards.render_rating("Пусто", []))).width, 1200)

    def test_edit_access_and_refresh(self):
        import cards
        g = make_guild()
        card_id = cards.create_card(GUILD, STAFF, "Рейтинг")
        card = cards.get_card(card_id)
        owner = FakeInteraction(g, g.members[STAFF])
        member = FakeInteraction(g, g.members[MEMBER])
        self.assertTrue(cards.can_edit(owner, card))
        self.assertFalse(cards.can_edit(member, card))
        card["editor_roles"] = [g.safe.id]
        g.members[MEMBER].roles.append(g.safe)
        self.assertTrue(cards.can_edit(member, card))
        self.assertFalse(cards.can_manage(member, card))  # менять очки — да, доступ и удаление — нет
        # опубликованная картинка перерисовывается после правки
        edits = []

        class Message:
            async def edit(self, **kwargs):
                edits.append(kwargs)

        channel = FakeChannel(g, 9400)

        async def fetch_message(message_id):
            return Message()

        channel.fetch_message = fetch_message
        card["channel_id"], card["message_id"] = channel.id, 1
        cards.save_card(card)
        self.assertIsNone(cards.set_score(card, "Эмилия", "7"))
        self.assertTrue(run(cards.refresh_message(g, card)))
        self.assertTrue(edits[0]["attachments"][0].filename.startswith("card-"))
        self.assertEqual(edits[0]["view"].children[0].custom_id, f"rb:cd:{card_id}")
        message = run(cards.card_message(cards.get_card(card_id)))
        self.assertEqual(message["embed"].image.url, "attachment://" + message["file"].filename)


class CardTemplateTests(unittest.TestCase):
    def test_templates_change_and_render(self):
        import card_commands as cc
        import card_templates as ct
        from PIL import Image as PILImage
        for template in ct.TEMPLATES:
            card = {"id": 1, "template": template, "title": "Тест", "data": ct.default_data(template)}
            png = ct.render(card, card["title"], card["data"])
            self.assertEqual(PILImage.open(io.BytesIO(png)).width, 1200)
        progress = {"template": "progress", "data": ct.default_data("progress")}
        self.assertIsNone(cc.apply_value(progress, "+50"))
        self.assertIsNone(cc.apply_value(progress, "цель 500"))
        self.assertIsNone(cc.apply_value(progress, "340/400"))
        self.assertEqual((progress["data"]["value"], progress["data"]["goal"]), (340, 400))
        self.assertEqual(cc.apply_value(progress, "много"), "cards.error.bad_value")
        counter = {"template": "counter", "data": ct.default_data("counter")}
        cc.apply_value(counter, "=7")
        cc.apply_value(counter, "-2")
        self.assertEqual(counter["data"]["value"], 5)
        table = {"template": "table", "data": ct.default_data("table")}
        self.assertIsNone(cc.apply_value(table, "Аня | дизайн | пт"))
        self.assertIsNone(cc.apply_value(table, "сб", who="Аня", column=3))
        self.assertIsNone(cc.apply_value(table, "Кот | музыка | вс"))
        self.assertEqual(table["data"]["rows"], [["Аня", "дизайн", "сб"], ["Кот", "музыка", "вс"]])
        self.assertIsNone(cc.apply_value(table, "убрать", who="1"))
        self.assertEqual(len(table["data"]["rows"]), 1)
        self.assertEqual(cc.apply_value(table, "x", who="Кот", column=9), "cards.error.bad_column")

    def test_every_template_in_every_theme(self):
        import card_templates as ct
        from PIL import Image as PILImage
        for template in ct.SPECS:
            for theme in ct.THEMES:
                png = ct.sample(template, theme)
                self.assertEqual(PILImage.open(io.BytesIO(png)).width, 1200, (template, theme))
            empty = ct.default_data(template)
            self.assertTrue(ct.render({"template": template}, "", empty))  # пустые данные не роняют

    def test_every_style(self):
        import card_templates as ct
        from PIL import Image as PILImage
        self.assertEqual(len(ct.STYLE_DEFS), 22)
        self.assertLessEqual(len(ct.STYLES), 25)  # выбор стиля — один список Discord
        for style in ct.STYLE_DEFS:
            for template in ("progress", "poll", "versus", "calendar", "spotlight", "chart", "table"):
                png = ct.sample(template, "night", style)
                self.assertEqual(PILImage.open(io.BytesIO(png)).width, 1200, (template, style))

    def test_glass_and_glitch_keep_own_colors(self):
        import card_templates as ct
        from PIL import Image as PILImage
        # у стекла и глитча свои цвета: тема картинки на них не влияет
        for style in ("glass", "glitch"):
            a = PILImage.open(io.BytesIO(ct.sample("counter", "cream", style))).convert("RGB")
            b = PILImage.open(io.BytesIO(ct.sample("counter", "lime", style))).convert("RGB")
            self.assertEqual(a.getpixel((600, 300)), b.getpixel((600, 300)), style)
        self.assertNotIn("glass", ct.THEME_NAMES)

    def test_generic_command_semantics(self):
        import card_commands as cc
        import card_templates as ct

        def card(template):
            return {"template": template, "data": ct.default_data(template)}

        quote = card("quote")
        self.assertIsNone(cc.apply_value(quote, "Мур"))
        self.assertIsNone(cc.apply_value(quote, "Кот", who="автор"))
        self.assertEqual((quote["data"]["text"], quote["data"]["author"]), ("Мур", "Кот"))
        poll = card("poll")
        self.assertIsNone(cc.apply_value(poll, "Мафия | 3"))
        self.assertIsNone(cc.apply_value(poll, "+2", who="Мафия"))
        self.assertIsNone(cc.apply_value(poll, "Какой ивент?", who="вопрос"))
        self.assertEqual(poll["data"]["rows"], [["Мафия", 5]])
        versus = card("versus")
        self.assertIsNone(cc.apply_value(versus, "+1", who="счёт слева"))
        self.assertEqual(versus["data"]["left_score"], 1)
        countdown = card("countdown")
        self.assertEqual(cc.apply_value(countdown, "когда-нибудь"), "cards.error.bad_date")
        self.assertIsNone(cc.apply_value(countdown, "25.12 20:00"))
        checklist = card("checklist")
        self.assertIsNone(cc.apply_value(checklist, "Призы | нет"))
        self.assertIsNone(cc.apply_value(checklist, "да", who="Призы"))
        self.assertEqual(checklist["data"]["rows"], [["Призы", "да"]])
        self.assertEqual(cc.apply_value(card("streak"), "x", who="1", column=1), "cards.error.no_rows")

    def test_dates(self):
        import card_templates as ct
        from datetime import datetime
        base = datetime(2026, 10, 15, 12, 0)
        self.assertEqual(ct.parse_when("25.12 20:00", base), datetime(2026, 12, 25, 20, 0))
        self.assertEqual(ct.parse_when("01.02", base), datetime(2027, 2, 1))
        self.assertEqual(ct.parse_when("10:00", base), datetime(2026, 10, 16, 10, 0))
        self.assertIsNone(ct.parse_when("31.02", base))
        self.assertEqual(ct.plural(3, "день", "дня", "дней"), "дня")
        self.assertEqual(ct.plural(11, "день", "дня", "дней"), "дней")

    def test_command_names(self):
        import card_commands as cc
        self.assertEqual(cc.clean_name("/Очки Клана"), "очки_клана")
        self.assertIsNone(cc.clean_name("очки!"))
        self.assertIsNone(cc.clean_name("x" * 40))

    def test_variables_in_card_message(self):
        import cards
        g = make_guild()
        card_id = cards.create_card(GUILD, STAFF, "Нас {member_count}", "counter")
        card = cards.get_card(card_id)
        self.assertTrue(cards.uses_variables(card))
        message = run(cards.card_message(card, g))
        self.assertTrue(message["file"].filename.startswith("card-"))
        cards.delete_card(card_id)


class VariablesTests(unittest.TestCase):
    def test_lists_and_exclusions(self):
        import variables as v
        from datetime import datetime

        class Voice:
            def __init__(self, cid, name, people):
                self.id, self.name = cid, name
                self.members = [types.SimpleNamespace(bot=False, display_name=p, voice=None) for p in people]

        a, b, afk = Voice(101, "Клан", ["Аня", "Кот"]), Voice(102, "Музыка", ["Пёс"]), Voice(103, "AFK", ["Сон"])
        guild = types.SimpleNamespace(channels=[a, b, afk], roles=[], members=[], member_count=4)
        pick = lambda arg: [c.id for c in v.pick_channels(guild, arg, kinds=(Voice,))]
        self.assertEqual(pick(None), [101, 102, 103])
        self.assertEqual(pick("Клан, музыка"), [101, 102])
        self.assertEqual(pick("-AFK"), [101, 102])
        self.assertEqual(pick("<#102>"), [102])
        self.assertEqual(pick("101,-101"), [])
        self.assertEqual(v.split_items("Клан общий, -AFK"), [(True, "Клан общий"), (False, "AFK")])
        today = datetime(2026, 10, 16)
        self.assertEqual(v.days_until("25.12", today), 70)
        self.assertEqual(v.days_since("01.10.2026", today), 15)
        self.assertIsNone(v.days_until("скоро", today))

    def test_variable_regex_takes_spaced_lists(self):
        from actions import VARIABLE, substitute
        self.assertEqual(VARIABLE.search("в войсе {voice:Клан общий, -AFK}!")[2], "Клан общий, -AFK")
        self.assertEqual(substitute("{random:а|а}", {"random": lambda arg: arg.split("|")[0]}), "а")


class QuotesTests(unittest.TestCase):
    def test_collect_and_pick_winner(self):
        import quotes as q

        def message(mid, author, text):
            return types.SimpleNamespace(id=mid, content=text, author=types.SimpleNamespace(id=author),
                                         channel=types.SimpleNamespace(id=5))

        gid = 777001
        a, problem = q.add_quote(gid, message(1, 10, "кот  —  это  жидкость"), 20)
        self.assertIsNone(problem)
        self.assertEqual(q.add_quote(gid, message(1, 10, "кот"), 21)[1], "quotes.already")
        self.assertEqual(q.add_quote(gid, message(2, 10, "   "), 20)[1], "quotes.no_text")
        b, _ = q.add_quote(gid, message(3, 11, "x" * 400), 20)
        pending = q.pending_quotes(gid)
        self.assertEqual([p["text"] for p in pending][0], "кот — это жидкость")
        self.assertEqual(len(pending[1]["text"]), q.MAX_TEXT)
        self.assertIsNone(q.winner(pending, {}))
        self.assertEqual(q.winner(pending, {a: 2, b: 2})["id"], a)  # ничья — кто раньше
        self.assertEqual(q.winner(pending, {a: 1, b: 3})["id"], b)
        self.assertEqual(q.parse_time("9.05"), "09:05")
        self.assertIsNone(q.parse_time("25:00"))
        q._q("DELETE FROM quotes WHERE guild_id = ?", (gid,), write=True)


class WhisperTests(unittest.TestCase):
    def test_checks_blocks_and_rate(self):
        import whispers as w
        gid = 777002
        guild = types.SimpleNamespace(id=gid)
        me = types.SimpleNamespace(id=1, bot=False)
        friend = types.SimpleNamespace(id=2, bot=False)
        self.assertIsNone(w.check(guild, me, friend, False))
        self.assertEqual(w.check(guild, me, me, False), "whispers.self")
        self.assertEqual(w.check(guild, me, types.SimpleNamespace(id=3, bot=True), False), "whispers.no_bots")
        w._q("INSERT INTO whisper_blocks (guild_id, recipient_id, sender_id) VALUES (?, 2, 1)", (gid,), write=True)
        self.assertEqual(w.check(guild, me, friend, False), "whispers.blocked")
        w.set_optout(gid, 2, True)
        self.assertEqual(w.check(guild, me, friend, False), "whispers.opted_out")
        w.set_optout(gid, 2, False)
        w._q("DELETE FROM whisper_blocks WHERE guild_id = ?", (gid,), write=True)
        now = int(time.time())
        for _ in range(w.RATE_PER_TARGET):
            w._q("INSERT INTO whispers (guild_id, sender_id, recipient_id, created_at) VALUES (?, 1, 2, ?)",
                 (gid, now), write=True)
        self.assertEqual(w.check(guild, me, friend, False), "whispers.rate_one")
        w._q("DELETE FROM whispers WHERE guild_id = ?", (gid,), write=True)
        settings = w.get_settings(gid)
        settings["allow_images"] = 0
        w.save_settings(settings)
        self.assertEqual(w.check(guild, me, friend, True), "whispers.no_images")
        w._q("DELETE FROM whisper_settings WHERE guild_id = ?", (gid,), write=True)


class WhisperAnonymityTests(unittest.TestCase):
    def test_reply_to_anonymous_never_names_the_author(self):
        import whispers as w
        g = make_guild()
        author, target = g.members[STAFF], g.members[MEMBER]
        author.display_name, target.display_name = "Эмилия", "Подруга"
        author.bot = target.bot = False
        inbox = {author.id: [], target.id: []}
        for member in (author, target):
            async def send(member=member, **kwargs):
                if getattr(member, "dm_closed", False):
                    raise discord.Forbidden(types.SimpleNamespace(status=403, reason=""), "closed")
                inbox[member.id].append(kwargs)
            member.send = send
        channel = FakeChannel(g, 9700)

        def interaction(member):
            i = FakeInteraction(g, member)
            i.channel_id, i.channel = channel.id, channel
            i.client.get_guild = lambda gid: g
            return i

        async def check():
            first = interaction(author)
            await w.send_whisper(first, target, "привет красивая", None, True, "dm")
            self.assertIn("Подруга", first.followups[-1])  # отправитель знает, кому писал
            whisper_id = w._q("SELECT MAX(id) FROM whispers", one=True)[0]
            self.assertIn("аноним", inbox[target.id][-1]["embed"].fields[0].value)
            modal = w.ReplyModal(w.get_whisper(whisper_id))
            modal.text._value = "кто ты?"
            modal.anon._values = ["no"]
            answer = interaction(target)
            await modal.on_submit(answer)
            self.assertNotIn("Эмилия", answer.followups[-1])  # ответившему автор не раскрыт
            self.assertIn("анониму", answer.followups[-1])
            self.assertEqual(len(inbox[author.id]), 1)  # ответ дошёл автору в личку
            # у автора закрыта личка — ответ в чат без имени и без пинга
            author.dm_closed = True
            answer = interaction(target)
            await modal.on_submit(answer)
            notice = channel.sent[-1]
            self.assertNotIn(str(author.id), notice.get("content") or "")
            self.assertNotIn("Эмилия", answer.followups[-1])
        run(check())


class ActivityAndCatalogTests(unittest.TestCase):
    def test_words_by_root_and_phrases(self):
        import activity
        self.assertEqual(activity.count_words("Какашка! какашку, КАКАШКАМИ и какао", {"какашк"}), {"какашк": 3})
        self.assertEqual(activity.count_words("спокойной ночи всем, спокойной ночи", {"спокойной ночи"}),
                         {"спокойной ночи": 2})
        self.assertEqual(activity.normalize_word("  Ёлка "), "елка")

    def test_counts_roundtrip(self):
        import activity
        from datetime import datetime
        gid = 777003
        activity.track_word(gid, "какашк")
        day = activity.today()
        activity._messages[(gid, day, 1, 50)] += 3
        activity._messages[(gid, day, 2, 50)] += 5
        activity._words[(gid, "какашк", day, 1)] += 2
        today = datetime.strptime(day, "%Y-%m-%d")
        rows = activity.messages_by_user(gid, None, today)
        self.assertEqual(rows, [(2, 5), (1, 3)])
        self.assertEqual(activity.messages_by_user(gid, 0, today, channels=[999]), [])
        self.assertEqual(activity.words_by_user(gid, "Какашк", 7, today), [(1, 2)])
        from database import get_connection
        connection = get_connection()
        for table in ("act_messages", "act_words", "act_tracked_words"):
            connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (gid,))
        connection.commit()
        connection.close()
        activity._tracked.pop(gid, None)

    def test_catalog_search_and_every_variable_runs(self):
        import discord as d
        from datetime import datetime, timezone
        import variable_catalog as vc
        self.assertGreaterEqual(len(vc.VARS), 90)
        self.assertEqual(len(vc.BY_NAME), len(vc.VARS))  # имена не повторяются
        self.assertEqual(vc.search("кто больше пишет")[0].name, "messages_top")
        self.assertIn("word_top", [item.name for item in vc.search("слово")[:3]])
        args = vc.parse("5, неделя, Флуд, -Спам, строка")
        self.assertEqual((args.top, args.period, args.items, args.inline), (5, 7, ["Флуд", "-Спам"], True))
        role = types.SimpleNamespace(id=5, name="Модератор", members=[], is_default=lambda: False)
        people = [types.SimpleNamespace(id=i, name=n, display_name=n, bot=b, status=d.Status.online, voice=None,
                                        joined_at=datetime(2026, 1, i, tzinfo=timezone.utc), top_role=role)
                  for i, n, b in ((1, "Аня", False), (2, "Кот", False), (3, "Бот", True))]
        role.members = people[:2]
        guild = types.SimpleNamespace(
            id=777004, name="Purr", member_count=3, members=people, premium_subscription_count=2, premium_tier=1,
            premium_subscribers=people[:1], created_at=datetime(2025, 1, 1, tzinfo=timezone.utc), owner=people[0],
            channels=[], text_channels=[], voice_channels=[], stage_channels=[],
            roles=[types.SimpleNamespace(id=1, name="@everyone", members=[], is_default=lambda: True), role],
            emojis=[], stickers=[], get_member=lambda i: next((m for m in people if m.id == i), None),
            get_channel=lambda i: None, get_role=lambda i: role if i == 5 else None)
        ctx = vc.Ctx(guild, datetime(2026, 10, 16, 12, 0), user=people[0])
        for item in vc.VARS:
            if item.compute is None:
                continue
            for example in item.examples:
                arg = example[len(item.name) + 2:-1] if ":" in example else None
                item.compute(ctx, arg)  # не падает
        self.assertEqual(vc.BY_NAME["role"].compute(ctx, "Модератор"), 2)
        self.assertEqual(vc.BY_NAME["members"].compute(ctx, "боты"), 1)
        from database import get_connection
        connection = get_connection()
        connection.execute("DELETE FROM act_tracked_words WHERE guild_id = ?", (777004,))
        connection.commit()
        connection.close()


class ImageLibraryTests(unittest.TestCase):
    def test_save_list_share(self):
        import image_library as il
        import tempfile
        import web_panel
        old = web_panel.ASSETS_DIR
        web_panel.ASSETS_DIR = pathlib.Path(tempfile.mkdtemp())
        try:
            from PIL import Image as PILImage
            buffer = io.BytesIO()
            PILImage.new("RGB", (10, 10), (255, 0, 0)).save(buffer, "PNG")
            item_id, problem = run(il.save_image(GUILD, STAFF, "Тест", "card", buffer.getvalue()))
            self.assertIsNone(problem)
            self.assertEqual(run(il.save_image(GUILD, STAFF, "x", "card", b"not an image"))[1], "library_images.bad_file")
            self.assertIn(item_id, [item["id"] for item in il.visible_images(GUILD, STAFF)])
            self.assertNotIn(item_id, [item["id"] for item in il.visible_images(GUILD, 999)])
            il._query("UPDATE image_library SET shared = 1 WHERE id = ?", (item_id,), write=True)
            self.assertIn(item_id, [item["id"] for item in il.visible_images(GUILD, 999)])
            self.assertEqual(il.read_file(il.get_image(item_id)), buffer.getvalue())
            il._query("DELETE FROM image_library WHERE id = ?", (item_id,), write=True)
        finally:
            web_panel.ASSETS_DIR = old


class UiEmojiTests(unittest.TestCase):
    def test_panel_buttons_get_doodles(self):
        import ui_emoji
        import embed_module as em
        names = sorted(set(ui_emoji.MAP.values()))
        names += ["u3_" + name[len("ui_"):] for name in names]
        ui_emoji._loaded.update({name: discord.PartialEmoji(name=name, id=100000000000000000 + n)
                                 for n, name in enumerate(names)})

        def buttons():
            view = em.EmbedEditorView(em.EmbedState(GUILD, STAFF), back_target=([], None))
            return [c for row in view.to_components() for c in row["components"] if c["type"] == 2]

        try:
            # по умолчанию — дудлы Эмилии
            items = buttons()
            back = next(c for c in items if c.get("label") == "Назад")
            self.assertEqual(back["emoji"]["name"], "ui_prev")  # «Назад» — дудл-стрелка перелистывания
            arrows = [c["emoji"]["name"] for c in items if c.get("emoji") and c["emoji"]["name"] in ("ui_prev", "ui_next")]
            self.assertEqual(arrows, ["ui_prev", "ui_prev", "ui_next"])  # «Назад», ◀ и ▶ перелистывания
            self.assertTrue(any(c.get("emoji", {}).get("name") == "ui_edit" for c in items))
            # объёмный набор — из веб-панели
            core.set_setting("ui_emoji_style", "3d")
            items = buttons()
            self.assertEqual(next(c for c in items if c.get("label") == "Назад")["emoji"]["name"], "u3_prev")
            self.assertTrue(any(c.get("emoji", {}).get("name") == "u3_edit" for c in items))
            # объёмная не загрузилась — берётся дудл
            del ui_emoji._loaded["u3_edit"]
            self.assertTrue(any(c.get("emoji", {}).get("name") == "ui_edit" for c in buttons()))
            core.set_setting("ui_emoji_style", "standard")
            names = [c.get("emoji", {}).get("name") for c in buttons()]
            self.assertNotIn("ui_back", names)
            self.assertNotIn("u3_back", names)
        finally:
            core.reset_setting("ui_emoji_style")
            ui_emoji._loaded.clear()

    def test_every_doodle_has_a_3d_icon(self):
        import ui_emoji
        doodles = {f[len("ui_"):] for f in os.listdir(ui_emoji.UI_DIR) if f.endswith(".png")}
        icons = {f[len("u3_"):] for f in os.listdir(ui_emoji.UI3D_DIR) if f.endswith(".png")}
        self.assertEqual(doodles, icons)
        forms = {f[len("doodle_"):] for f in os.listdir(ui_emoji.EMOJI_DIR) if f.startswith("doodle_")}
        forms3d = {f[len("d3_"):] for f in os.listdir(ui_emoji.DOODLE3D_DIR) if f.endswith(".png")}
        self.assertEqual(forms, forms3d)
        # каждая иконка из MAP существует
        for name in set(ui_emoji.MAP.values()):
            folder, prefix = (ui_emoji.DOODLE3D_DIR, "") if name.startswith("d3_") else (ui_emoji.UI_DIR, "")
            self.assertTrue(os.path.exists(os.path.join(folder, name + ".png")), name)

    def test_form_doodles_turn_3d(self):
        import ui_emoji
        import form_packs as fp
        ui_emoji._loaded["d3_star"] = discord.PartialEmoji(name="d3_star", id=200000000000000001)
        fp._doodles["doodle_star"] = "<:doodle_star:123456789012345678>"
        core.set_setting("ui_emoji_style", "3d")  # по умолчанию — дудлы Эмилии, 3D включают в веб-панели
        try:
            self.assertEqual(ui_emoji.doodle("<:doodle_star:123456789012345678>"), "<:d3_star:200000000000000001>")
            self.assertEqual(ui_emoji.swap("🎉"), discord.PartialEmoji.from_str("🎉").name)
            ui_emoji._loaded["d3_sun"] = discord.PartialEmoji(name="d3_sun", id=200000000000000002)
            self.assertEqual(ui_emoji.swap("☀️").name, "d3_sun")
            core.set_setting("ui_emoji_style", "doodle")
            self.assertEqual(ui_emoji.doodle("<:doodle_star:123456789012345678>"), "<:doodle_star:123456789012345678>")
        finally:
            core.reset_setting("ui_emoji_style")
            ui_emoji._loaded.clear()
            fp._doodles.clear()


class EmojiPickerTests(unittest.TestCase):
    """Эмодзи кнопки — экраном: дудлы бота, эмодзи сервера, своё."""

    def test_text_and_image(self):
        import emoji_picker as ep
        bot = types.SimpleNamespace(get_emoji=lambda _id: None)
        guild = types.SimpleNamespace(emojis=[types.SimpleNamespace(name="pepe", __str__=None)])

        class Emoji:
            name = "pepe"

            def __str__(self):
                return "<:pepe:111111111111111111>"

        guild.emojis = [Emoji()]
        i = types.SimpleNamespace(client=bot, guild=guild, response=FakeResponse())
        self.assertEqual(run(ep.from_text(i, "✨")), ("✨", None))
        self.assertEqual(run(ep.from_text(i, ":pepe:")), ("<:pepe:111111111111111111>", None))
        self.assertEqual(run(ep.from_text(i, "hello")), (None, "emoji_picker.error.unknown"))
        buffer = io.BytesIO()
        PILImage.new("RGB", (300, 100), "red").save(buffer, "PNG")
        self.assertEqual(PILImage.open(io.BytesIO(ep.prepare_image(buffer.getvalue()))).size, (128, 128))

    def test_button_flow_uses_picker(self):
        import embed_module as em
        import form_packs
        form_packs._doodles.update({f"doodle_{n:02d}": f"<:doodle_{n:02d}:{200000000000000000 + n}>" for n in range(30)})
        try:
            g = make_guild()
            state = em.EmbedState(GUILD, STAFF)
            builder = em.ButtonBuilderView(state, hub=None, back_target=None)
            modal = em.ButtonLabelModal(state, builder)
            modal.label_input._value = "Правила"
            shown = []

            def capture(i):
                async def edit_message(**kwargs):
                    i.response.done = True
                    shown.append(kwargs)
                i.response.edit_message = edit_message
                return i

            run(modal.on_submit(capture(FakeInteraction(g, g.members[STAFF]))))
            picker = shown[-1]["view"]
            selects = [c for c in picker.children if isinstance(c, discord.ui.Select)]
            self.assertEqual([len(s.options) for s in selects], [25, 5])
            selects[1]._values = [selects[1].options[0].value]
            run(selects[1].callback(capture(FakeInteraction(g, g.members[STAFF]))))
            color_view = shown[-1]["view"]
            self.assertIsInstance(color_view, em.ButtonColorView)
            self.assertEqual(color_view.pending["emoji"], "<:doodle_25:200000000000000025>")
            self.assertEqual(color_view.pending["label"], "Правила")
        finally:
            form_packs._doodles.clear()


class BotPermissionTests(unittest.TestCase):
    """Данные бота о правах неточны -> не отказываемся заранее; отказ Discord — с названием права."""

    def test_send_when_cache_says_no(self):
        import live
        g = make_guild()
        build_id = database.save_message_build(GUILD, STAFF, "Тест", "", json.dumps([{"title": "Привет"}]), "[]")

        class BotBlind(FakeChannel):
            def permissions_for(self, who):
                if who is self.guild.me:  # по кэшу бот «ничего не может»
                    return discord.Permissions.none()
                return super().permissions_for(who)

        channel = BotBlind(g, 9500)
        message, error = run(live.deliver(g, g.members[STAFF], build_id, channel, track=False))
        self.assertIsNone(error)
        self.assertEqual(len(channel.sent), 1)

        class Refuses(BotBlind):
            async def send(self, **kwargs):
                raise discord.Forbidden(types.SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

        refused = Refuses(g, 9501)
        message, error = run(live.deliver(g, g.members[STAFF], build_id, refused, track=False))
        self.assertIsNone(message)
        self.assertIn("«Встраивать ссылки»", error)
        self.assertIn("«Отправлять сообщения»", error)
        # участнику без права писать — по-прежнему нельзя
        closed = FakeChannel(g, 9502, allow=False)
        message, error = run(live.deliver(g, g.members[MEMBER], build_id, closed, track=False))
        self.assertEqual(error, core.t("embed.send.no_perm_user", channel=closed.mention))


class MessageEditActionTests(unittest.TestCase):
    """«Редактировать сообщение»: из Message Build — панель build'а, иначе — окно с текстом и embed'ом."""

    def test_routes(self):
        g = make_guild()
        build_id = database.save_message_build(GUILD, ADMIN, "Правила", "", json.dumps([{"title": "Правила"}]), "[]")
        database.save_sent_instance(build_id, 777000, 9600, GUILD, 0)
        admin = FakeInteraction(g, g.members[ADMIN])
        shown = {}

        async def send_message(content=None, **kwargs):
            shown.update(kwargs)

        admin.response.send_message = send_message
        bot_author = types.SimpleNamespace(id=g.me.id)
        message = types.SimpleNamespace(id=777000, author=bot_author, embeds=[discord.Embed(title="Правила")],
                                        content="")
        run(actions._message_edit(admin, "", target_message=message))
        import embed_module as em
        self.assertIsInstance(shown["view"], em.MessageBuildFinalView)
        self.assertTrue(shown["ephemeral"])
        # обычное сообщение бота — окно с текстом, заголовком и текстом карточки
        other = types.SimpleNamespace(id=888000, author=bot_author, embeds=[discord.Embed(title="Старое", description="d")],
                                      content="")
        admin2 = FakeInteraction(g, g.members[ADMIN])
        run(actions._message_edit(admin2, "", target_message=other))
        modal = admin2.response.modals[0]
        self.assertEqual(modal.title_input.default, "Старое")
        self.assertEqual(len(modal.children), 3)


class ConfirmationTests(unittest.TestCase):
    def test_harmless_actions_never_ask(self):
        database.set_action_dangerous("build.trigger", True)  # как было включено у Эмилии
        try:
            self.assertFalse(core.needs_confirmation("build.trigger"))
            core.ensure_default_actions()
            self.assertEqual(core.get_action("build.trigger")[2], 0)
            self.assertTrue(core.needs_confirmation("webhook.send"))
        finally:
            database.set_action_dangerous("build.trigger", False)


class ButtonSetFlowTests(unittest.TestCase):
    def test_button_set_uses_step_by_step_flow(self):
        import bot
        import embed_module as em
        g = make_guild()
        state = bot.ButtonSetState()
        view = bot.InlineButtonBuilderView(state)
        i = FakeInteraction(g, g.members[STAFF])
        run(view.add.callback(i))
        self.assertIsInstance(i.response.modals[0], em.ButtonLabelModal)
        # дальше — общий сценарий: цвет и действие выбираются кнопками и списком
        pending = {"label": "Сайт", "emoji": None, "style": "link", "action_key": None}
        run(em.commit_pending(FakeInteraction(g, g.members[STAFF]), state, pending, "buttons", view,
                              "https://example.com"))
        self.assertEqual(state.buttons[0]["value"], "https://example.com")


class AttachButtonsTests(unittest.TestCase):
    """Набор кнопок — к своему сообщению бота или переотправкой чужого."""

    def setUp(self):
        self.g = make_guild()
        self.set_id = database.save_button_set(GUILD, STAFF, "Набор", json.dumps(
            [{"label": "Сайт", "style": "link", "value": "https://example.com"}]))
        self.channel = FakeChannel(self.g, 9700)

    def message(self, author, mid):
        edits, deleted = [], []

        async def edit(**kwargs):
            edits.append(kwargs)

        async def delete():
            deleted.append(True)

        msg = types.SimpleNamespace(id=mid, author=author, webhook_id=None, components=[], content="Привет",
                                    embeds=[], attachments=[], channel=self.channel, guild=self.g,
                                    jump_url=f"https://discord.com/channels/{GUILD}/9700/{mid}", edit=edit, delete=delete)
        return msg, edits, deleted

    def test_own_message_gets_buttons(self):
        import attach_buttons as ab
        msg, edits, _ = self.message(self.g.me, 5001)
        _, _, view = actions.render_source("s", self.set_id)
        run(ab.attach_to_own(FakeInteraction(self.g, self.g.members[STAFF]), msg, view))
        self.assertEqual(edits[0]["view"].children[0].label, "Сайт")

    def test_foreign_message_reposted(self):
        import attach_buttons as ab
        author = self.g.members[STAFF]
        msg, _, deleted = self.message(author, 5002)
        i = FakeInteraction(self.g, author)
        run(ab.repost(i, self.set_id, msg, as_author=False))
        self.assertEqual(self.channel.sent[0]["content"], "Привет")
        self.assertEqual(self.channel.sent[0]["view"].children[0].label, "Сайт")
        self.assertEqual(deleted, [True])  # своё сообщение — оригинал удалён
        # чужое — от имени автора нельзя участнику без прав
        other, _, _ = self.message(self.g.members[ADMIN], 5003)
        self.assertFalse(ab.may_impersonate(FakeInteraction(self.g, self.g.members[MEMBER]), other))


class SecurityAuditTests(unittest.TestCase):
    """Находки аудита Message Build."""

    def test_private_ignores_old_restrictions(self):
        g = make_guild()
        member = FakeInteraction(g, g.members[MEMBER])
        self.assertFalse(actions.can_view(member, STAFF, "private", [], ["member"]))
        self.assertTrue(actions.can_view(member, STAFF, "restricted", [], ["member"]))

    def test_webhook_button_needs_own_saved_webhook(self):
        g = make_guild()
        staff = FakeInteraction(g, g.members[STAFF])
        url = "https://discord.com/api/webhooks/1/abc"
        self.assertEqual(actions.validate_action_value(staff, "webhook.send", url)[1], "actions.webhook_record_missing")
        rid = database.save_webhook(GUILD, ADMIN, 1, 9800, "hook", url, None)
        self.assertEqual(actions.validate_action_value(staff, "webhook.send", str(rid))[1],
                         "actions.webhook_record_missing")  # чужой вебхук
        admin = FakeInteraction(g, g.members[ADMIN])
        self.assertEqual(actions.validate_action_value(admin, "webhook.send", str(rid)), (str(rid), None))
        self.assertEqual(actions.strip_secrets([{"action_key": "webhook.send", "value": "1"}])[0]["value"], None)
        modal = actions.WebhookSendModal(url)
        self.assertNotIn(modal.url_input, modal.children)  # ссылку нажавшему не показываем

    def test_webhook_command_checks_user(self):
        import hubs
        g = make_guild()
        staff = FakeInteraction(g, g.members[STAFF])
        channel = FakeChannel(g, 9801)
        run(hubs.create_webhook_for(staff, channel, "x", None))
        self.assertEqual(staff.last, core.t("live.error.webhook_no_rights", channel=channel.mention))

    def test_history_is_manage_only_and_list_kept(self):
        import embed_module as em
        g = make_guild()
        bid = database.save_message_build(GUILD, ADMIN, "Секрет", "", json.dumps([{"title": "x"}]), "[]",
                                          visibility="public")
        member = FakeInteraction(g, g.members[MEMBER])
        run(em.MessageBuildFinalView(bid, back_target=None).history.callback(member))
        self.assertEqual(member.last, core.t("embed.edit_denied"))
        state = em.EmbedState(GUILD, STAFF)
        state.interactive = {"type": "native_select", "kind": "role"}
        self.assertEqual(state.list_options, [])
        self.assertEqual(state.interactive["type"], "native_select")  # просмотр не стирает меню

    def test_empty_build_does_not_wipe_copies(self):
        import live
        g = make_guild()
        bid = database.save_message_build(GUILD, STAFF, "Пусто", "", "[]", "[]")
        database.save_sent_instance(bid, 990001, 9802, GUILD, 0)
        self.assertEqual(run(live._resync_one(g, bid)), (0, 0, 0, 0))
        self.assertTrue(database.get_sent_instances(bid))


class FormClosingTests(unittest.TestCase):
    """Набор можно закрыть вручную или лимитом; закрытые не копятся в /forms."""

    def test_close_limit_and_hide(self):
        import hubs
        from extended_modules import open_form
        g = make_guild()
        channel = FakeChannel(g, 9900)
        form_id = database.save_form(GUILD, STAFF, "Хелпер", "", json.dumps([{"label": "Возраст"}]),
                                     visibility="public", destination_channel_id=channel.id,
                                     reviewer_user_ids_json=json.dumps([STAFF]))
        database.set_form_status(form_id, closed=True)
        member = FakeInteraction(g, g.members[MEMBER])
        run(open_form(member, form_id))
        self.assertEqual(member.last, core.t("forms.closed"))
        reviewer = FakeInteraction(g, g.members[STAFF])
        active, closed = hubs.split_closed(hubs.reviewable_forms(reviewer))
        self.assertIn(form_id, [f[0] for f, _ in closed])
        self.assertNotIn(form_id, [f[0] for f, _ in active])
        # лимит: 1 заявка -> после неё набор закрывается сам
        database.set_form_status(form_id, closed=False, max_submissions=1)
        from extended_modules import SubmissionModal
        modal = SubmissionModal(database.get_form(form_id))
        modal.answer_inputs[0]._value = "20"
        run(modal.on_submit(FakeInteraction(g, g.members[MEMBER])))
        self.assertEqual(database.get_form_status(form_id)[:2], (True, 1))


class ReviewFromPanelTests(unittest.TestCase):
    def test_panel_decision_updates_channel_card(self):
        import hubs
        g = make_guild()
        form_id = database.save_form(GUILD, STAFF, "Модер", "", json.dumps([{"label": "Возраст"}]),
                                     reviewer_user_ids_json=json.dumps([STAFF]))
        sid = database.save_submission(form_id, GUILD, MEMBER, json.dumps({"Возраст": "20"}))
        channel = FakeChannel(g, 9950)
        card_edits = []

        class Card:
            embeds = [discord.Embed(title="НОВАЯ ЗАЯВКА")]

            async def edit(self, **kwargs):
                card_edits.append(kwargs)

        async def fetch_message(mid):
            return Card()

        channel.fetch_message = fetch_message
        database.save_submission_card(sid, channel.id, 1)
        reviewer = FakeInteraction(g, g.members[STAFF])
        shown = []

        async def edit_message(**kwargs):
            reviewer.response.done = True
            shown.append(kwargs)

        reviewer.response.edit_message = edit_message
        view = hubs.SubmissionReviewView(sid, back_target=([], None))
        run(view.approve.callback(reviewer))
        self.assertIsNone(card_edits[0]["view"])  # кнопки с карточки в канале убраны
        result = shown[0]["embed"].fields[-1].value
        self.assertNotIn("{status}", result)
        self.assertIn("Одобрена", result)
        self.assertNotIn("{", shown[0]["embed"].description or "")


class BotProfileTests(unittest.TestCase):
    def test_owner_sets_server_profile(self):
        import access_module
        g = make_guild()
        edits = []

        async def edit(**kwargs):
            edits.append(kwargs)

        g.me.edit = edit
        modal = access_module.BotProfileModal(g.me)
        modal.nick._value = "Purr"
        modal.bio._value = "мурр"
        owner = FakeInteraction(g, g.members[OWNER])
        run(modal.on_submit(owner))
        self.assertEqual(edits[0]["nick"], "Purr")
        self.assertEqual(edits[0]["bio"], "мурр")
        staff = FakeInteraction(g, g.members[STAFF])
        run(modal.on_submit(staff))
        self.assertEqual(len(edits), 1)  # не владельцу — нельзя


class BotStatusTests(unittest.TestCase):
    def test_status_saved_and_applied(self):
        import access_module
        g = make_guild()
        presences = []

        async def change_presence(**kwargs):
            presences.append(kwargs)

        owner = FakeInteraction(g, g.members[OWNER])
        owner.client = types.SimpleNamespace(user=types.SimpleNamespace(id=777, name="Purr"), change_presence=change_presence)
        modal = access_module.StatusModal()
        modal.text._value = "мурчит над заявками"
        modal.mode._values = ["idle"]
        try:
            run(modal.on_submit(owner))
            self.assertEqual(presences[0]["activity"].name, "мурчит над заявками")
            self.assertEqual(presences[0]["status"], discord.Status.idle)
            self.assertEqual(access_module.apply_presence_kwargs()["status"], discord.Status.idle)
        finally:
            core.reset_setting("bot_status_text")
            core.reset_setting("bot_status_mode")


class RegistryScreenTests(unittest.TestCase):
    def test_action_registry_opens(self):
        import access_module as am
        g = make_guild()
        owner = FakeInteraction(g, g.members[OWNER])
        self.assertIn("Показать сообщение", am.action_registry_embed(owner).description)
        run(am.SettingsView(None, back_target=([], None)).registry.callback(owner))
        self.assertEqual(core.t("access.registry.line", key="x", status="", create="", use="", danger="").count("x"), 1)


class DiagnosticsTests(unittest.TestCase):
    def test_report_explains_missing_parts(self):
        import diagnostics
        import ui_emoji
        g = make_guild()
        owner = FakeInteraction(g, g.members[OWNER])
        old = os.environ.pop("WEB_PANEL_URL", None)
        ui_emoji.load_status["ui"] = {"files": 36, "loaded": 0, "errors": ["ui_add: 403 Forbidden"], "done": True}
        try:
            embed, banner = diagnostics.report(owner)
            self.assertIsNone(banner)
            self.assertIn("WEB_PANEL_URL", embed.description)
            self.assertIn("0 из 36", embed.description)
            self.assertIn("403", embed.description)
        finally:
            ui_emoji.load_status.clear()
            if old is not None:
                os.environ["WEB_PANEL_URL"] = old


class EmojiCommandTests(unittest.TestCase):
    def test_parse_and_zip(self):
        import emoji_cmd
        items = emoji_cmd.parse("<a:dance:123456789012345678> привет 😀 <:pepe:223456789012345678> <a:dance:123456789012345678>")
        self.assertEqual([name for name, _ in items], ["dance.gif", "emoji_1f600.png", "pepe.png"])
        self.assertIn("123456789012345678.gif", items[0][1])
        self.assertTrue(emoji_cmd.parse("<a:dance:123456789012345678>", as_png=True)[0][0].endswith(".png"))
        archive = emoji_cmd.zip_bytes([("a.png", b"1"), ("a.png", b"2")])
        self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(archive)).namelist()), ["a.png", "a_2.png"])


class ReferenceInboxTests(unittest.TestCase):
    """Референсы пачкой: режим сбора по сообщениям без слэш-команд."""

    def test_collect_from_messages(self):
        import reference_inbox as ri
        import logo_module
        g = make_guild()
        style_id = database.save_logo_style(GUILD, ADMIN, "Дудлы", "", "")
        buffer = io.BytesIO()
        PILImage.new("RGB", (64, 64), "red").save(buffer, "PNG")
        png = buffer.getvalue()

        class Attachment:
            filename, content_type, size = "a.png", "image/png", len(png)

            async def read(self):
                return png

        reactions, replies = [], []

        async def add_reaction(emoji):
            reactions.append(emoji)

        async def reply(text, **kwargs):
            replies.append(text)

        admin = FakeInteraction(g, g.members[ADMIN])
        admin.channel_id = 9990
        ri.start_collecting(admin, style_id)
        message = types.SimpleNamespace(
            author=g.members[ADMIN], guild=g, channel=types.SimpleNamespace(id=9990), content="",
            attachments=[Attachment() for _ in range(10)], stickers=[], add_reaction=add_reaction, reply=reply)
        g.members[ADMIN].bot = False
        old_dir = os.environ.get("LOGO_DIR")
        os.environ["LOGO_DIR"] = tempfile.mkdtemp()
        try:
            run(ri.on_message(message))
            self.assertEqual(reactions, ["✅"])
            self.assertEqual(len(logo_module.references_of(database.get_logo_style(style_id))), 10)
            self.assertIn("10", replies[0])
        finally:
            ri._collecting.clear()
            if old_dir is None:
                os.environ.pop("LOGO_DIR", None)
            else:
                os.environ["LOGO_DIR"] = old_dir


class EmojiExportTests(unittest.TestCase):
    def test_upload_adds_new_replaces_redrawn_and_stops_when_full(self):
        import emoji_export
        import emoji_sync
        created, deleted = [], []

        class Emoji(types.SimpleNamespace):
            async def delete(self, reason=None):
                deleted.append(self.name)
                guild.emojis.remove(self)

        class Guild:
            id = 4242
            emoji_limit = 30

            def __init__(self):
                self.emojis = [Emoji(name="doodle_star", animated=False, id=1),
                               Emoji(name="doodle_crown", animated=False, id=2)]

            async def create_custom_emoji(self, name, image, reason=None):
                created.append(name)
                emoji = Emoji(name=name, animated=False, id=1000 + len(created))
                self.emojis.append(emoji)
                return emoji

        guild = Guild()
        star = [data for name, path in emoji_export.files_of("forms") if name == "doodle_star"
                for data in [open(path, "rb").read()]][0]
        emoji_sync.remember("guild:4242", "doodle_star", emoji_sync.file_hash(star))  # star не менялся
        added, skipped, no_room, errors, replaced = run(emoji_export.upload(guild, "forms"))
        self.assertEqual(skipped, 1)            # doodle_star — та же картинка
        self.assertEqual(replaced, 1)           # doodle_crown — хэша нет: считаем перерисованным
        self.assertEqual(deleted, ["doodle_crown"])
        self.assertEqual(added, 28)             # 30 мест минус два занятых
        self.assertEqual(no_room, 20)
        self.assertNotIn("doodle_star", created)
        self.assertEqual(len(emoji_export.files_of("all")), 90)  # 50 дудлов форм + 40 иконок кнопок (с цифрами 1–4)
        # повторный запуск ничего не меняет
        created.clear()
        added, skipped, no_room, errors, replaced = run(emoji_export.upload(guild, "forms"))
        self.assertEqual((added, replaced, created), (0, 0, []))

    def test_remap_ids_updates_saved_emoji(self):
        import emoji_sync
        from database import get_connection
        connection = get_connection()
        connection.execute("CREATE TABLE IF NOT EXISTS remap_probe (id INTEGER PRIMARY KEY, data TEXT)")
        connection.execute("INSERT INTO remap_probe (data) VALUES (?)", ('{"emoji": "<:doodle_cat:111>"}',))
        connection.commit()
        connection.close()
        self.assertGreaterEqual(emoji_sync.remap_ids(111, 222), 1)
        connection = get_connection()
        value = connection.execute("SELECT data FROM remap_probe").fetchone()[0]
        connection.execute("DROP TABLE remap_probe")
        connection.commit()
        connection.close()
        self.assertEqual(value, '{"emoji": "<:doodle_cat:222>"}')

    def test_ping_by_user_role_or_empty_text(self):
        import emoji_export
        me = types.SimpleNamespace(id=555)
        role = types.SimpleNamespace(id=777)
        guild = types.SimpleNamespace(me=me, self_role=role)

        def msg(content="", mentions=(), roles=()):
            return types.SimpleNamespace(guild=guild, content=content, mentions=list(mentions),
                                         role_mentions=list(roles))

        self.assertTrue(emoji_export.pinged(msg(mentions=[me])))           # @Purr без текста
        self.assertTrue(emoji_export.pinged(msg(roles=[role])))            # пинг роли бота
        self.assertTrue(emoji_export.pinged(msg(content="<@555> эмодзи")))
        self.assertFalse(emoji_export.pinged(msg(content="эмодзи")))


class LongListTests(unittest.TestCase):
    def test_paged_select_turns_pages(self):
        async def scenario():
            options = [discord.SelectOption(label=f"item {n}", value=str(n)) for n in range(60)]
            picked = []

            async def on_pick(i):
                picked.append(select.values[0])

            select = core.PagedSelect(options=options, placeholder="x", on_pick=on_pick)
            view = core.PanelView()
            view.add_item(select)
            self.assertLessEqual(len(select.options), 25)
            self.assertEqual(select.options[-1].value, core.PagedSelect.NEXT)
            edited = []
            response = types.SimpleNamespace(edit_message=lambda **kw: _async(edited.append(kw)))
            select._values = [core.PagedSelect.NEXT]
            await select.callback(types.SimpleNamespace(response=response))
            self.assertEqual(select.page, 1)
            self.assertEqual(select.options[0].value, core.PagedSelect.PREV)
            self.assertEqual(select.options[1].value, "23")
            select._values = ["30"]
            await select.callback(types.SimpleNamespace(response=response))
            self.assertEqual(picked, ["30"])
        run(scenario())

    def test_panel_view_keeps_more_than_25_buttons(self):
        async def scenario():
            view = core.PanelView(back_target=("e", None))
            for n in range(40):
                view.add_item(discord.ui.Button(label=f"b{n}"))
            view.to_components()
            self.assertLessEqual(len(view.children), 25)
            self.assertEqual(len(view._pages), 8)
            labels = [b.label for page in view._pages for b in page]
            self.assertEqual(labels, [f"b{n}" for n in range(40)])
        run(scenario())


async def _async(value=None):
    return value


class BannerFontTests(unittest.TestCase):
    def test_design_fonts_and_cyrillic_fallback(self):
        import banner_render as b
        self.assertTrue(b.has_design_fonts())
        self.assertEqual(b.kind_for("dunkin", "your moderator form"), "dunkin")
        self.assertTrue(b.kind_for("drunk", "набор! набор!").startswith("fallback:"))
        self.assertEqual(b.kind_for("coolvetica", "модератор"), "coolvetica")

    def test_title_accent_never_touches_tagline(self):
        import banner_render as b
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGBA", (1200, 200)))
        tag_spec, title_spec = b.TEMPLATES["forms.public_card"]
        tag_lines, tag_font = b.fit_text(tag_spec, "набор! набор! набоооор!     подавай заявку!")
        tagline = b._layout(draw, tag_spec, tag_lines, tag_font)
        for name in ("дизайнер", "ёлка", "айтишник", "ведущий ивентов клана"):
            lines, font = b.fit_text(title_spec, name)
            placed, font, tag = b.place_title(draw, title_spec, lines, font, tagline, tag_font, 196)
            tag_boxes = [b._box(draw, piece, tag_font) for piece in tag]
            for (x, y), line in placed:
                for i, ch in enumerate(line.strip() and line):
                    if ch.isspace():
                        continue
                    box = draw.textbbox((x + font.getlength(line[:i]), y), ch, font=font, anchor="lm")
                    for t in tag_boxes:
                        overlap = box[0] < t[2] and box[2] > t[0] and box[1] < t[3] and box[3] > t[1]
                        self.assertFalse(overlap, f"{name}: «{ch}» задевает надпись")


class PackBannerTests(unittest.TestCase):
    def test_pack_gif_cycles_forms(self):
        import banner_render as b
        from PIL import Image
        data = b.pack_gif([("модератор", ""), ("artist", "напиши нам!")])
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.format, "GIF")
        self.assertGreaterEqual(image.n_frames, 2 * b.TYPE_STEPS)  # одинаковые соседние кадры GIF склеивает
        self.assertEqual(image.info.get("loop"), 0)


    def test_pack_banner_attached_to_message(self):
        import actions
        import form_packs
        original = form_packs.pack_gif_bytes

        async def fake_gif(pack_id):
            return b"GIF89a" if pack_id == 5 else None

        form_packs.pack_gif_bytes = fake_gif
        try:
            data = {"interactive": {"type": "form_packs", "packs": ["5"]}}
            parts = [{"embed": discord.Embed(title="Набор")}, {"embed": discord.Embed(title="ещё")}]
            run(form_packs.attach_banners(parts, data))
            self.assertEqual(parts[0]["embed"].image.url, "attachment://pack_banner_5.gif")
            self.assertEqual([f.filename for f in parts[0]["files"]], ["pack_banner_5.gif"])
            self.assertNotIn("files", parts[1])
            self.assertEqual(len(actions.single_message(parts)["files"]), 1)
            gone = [{"embed": discord.Embed().set_image(url=form_packs.banner_marker(9))}]
            run(form_packs.attach_banners(gone, {}))
            self.assertIsNone(gone[0]["embed"].image.url)  # пака нет — без битой картинки
            self.assertEqual(actions.check_url(form_packs.banner_marker(5)), ("attachment://pack_banner_5.gif", True))
        finally:
            form_packs.pack_gif_bytes = original

    def test_pack_gif_fits_upload_limit(self):
        import banner_render as b
        data = b.pack_gif([(f"должность {n}", "набор открыт!") for n in range(8)])
        self.assertLess(len(data), 10 * 1024 * 1024)


class ServerBannerTests(unittest.TestCase):
    def test_banner_option_hidden_without_manage_guild(self):
        import hubs
        import server_banner
        guild = types.SimpleNamespace(id=GUILD, me=types.SimpleNamespace(
            guild_permissions=discord.Permissions(manage_guild=False)))
        self.assertFalse(server_banner.available(guild))

        async def check():
            view = hubs.PicturesHome(guild)
            self.assertNotIn(view.server_banner, view.children)
            guild.me.guild_permissions = discord.Permissions(manage_guild=True)
            shown = hubs.PicturesHome(guild)
            self.assertIn(shown.server_banner, shown.children)

        run(check())
        import core
        self.assertNotIn("Баннер сервера", core.button_hints("pictures.home", hide=("server_banner",)))
        self.assertIn("Баннер сервера", core.button_hints("pictures.home"))

    def test_every_style_renders_with_lines(self):
        import banner_styles
        import server_banner as sb
        from PIL import Image
        banner = {"guild_id": 1, "base": "", "lines": [sb.clean_line(line) for line in sb.DEFAULT_LINES]}
        self.assertEqual(len(banner_styles.STYLES), 20)
        for key in banner_styles.STYLES:
            trial = dict(banner, base=f"style:{key}")
            image = Image.open(io.BytesIO(sb.render(trial, ["Сервер", "участников: 10 · в войсе: 2"])))
            self.assertEqual(image.size, sb.SIZE)

    def test_bad_line_values_are_cleaned(self):
        import server_banner as sb
        line = sb.clean_line({"text": "x" * 500, "pos": "zz", "size": "XXL", "font": "comic", "color": "red"})
        self.assertEqual((len(line["text"]), line["pos"], line["size"], line["font"], line["color"]),
                         (200, "bl", "M", "coolvetica", "#201C1E"))


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
        old = os.environ.get("WEB_PANEL_URL")
        os.environ["WEB_PANEL_URL"] = "http://bot.example:8080"
        try:
            # иконки из Figma лежат в panel_art и подключены по умолчанию
            url = core.panel_embed(i, "embed.home").thumbnail.url
            self.assertRegex(url, r"^http://bot.example:8080/art/thumbnail\.embed\.png\?v=[0-9a-f]{10}$")
            # экран расписания относится к /embed
            self.assertIn("thumbnail.embed.png", core.panel_embed(i, "automation.schedules", list="", tz="").thumbnail.url)
            self.assertIn("thumbnail.access.png", core.panel_embed(i, "access.home", level="x").thumbnail.url)
            # баннер — только свой у экрана и встаёт отдельным embed'ом над текстом
            core.set_setting("embed.home.banner", "https://cdn/home-banner.png")
            home = core.panel_embed(i, "embed.home")
            self.assertIsNone(home.image.url)
            self.assertEqual(home.banner_url, "https://cdn/home-banner.png")
            self.assertIsNone(core.panel_embed(i, "embed.editor.title" and "embed.saved").banner_url)
            kwargs = core.expand_embeds({"embed": home, "ephemeral": True})
            self.assertNotIn("embed", kwargs)
            self.assertEqual(kwargs["embeds"][0].image.url, "https://cdn/home-banner.png")
            self.assertIs(kwargs["embeds"][1], home)
            # «Назад» со снимком экрана (список embed'ов) — тоже embeds=
            self.assertEqual(core.expand_embeds({"embed": kwargs["embeds"]})["embeds"], kwargs["embeds"])
            core.set_setting("embed.home.thumbnail", "none")
            self.assertIsNone(core.panel_embed(i, "embed.home").thumbnail.url)
        finally:
            for key in ("embed.home.banner", "embed.home.thumbnail"):
                core.reset_setting(key)
            if old is None:
                os.environ.pop("WEB_PANEL_URL", None)
            else:
                os.environ["WEB_PANEL_URL"] = old

    def test_access_denied_card_and_banner_screens(self):
        g = make_guild()
        old = os.environ.get("WEB_PANEL_URL")
        os.environ["WEB_PANEL_URL"] = "http://bot.example:8080"
        try:
            i = FakeInteraction(g, g.members[MEMBER])
            message = core.message_for(i, "common.need_admin")
            self.assertIn("access_denied.banner.gif", message["embed"].banner_url)
            self.assertEqual(message["embed"].description, core.t("common.need_admin"))
            self.assertEqual(core.message_for(i, "embed.build_empty"), {"content": core.t("embed.build_empty")})
            # баннер есть только у главных экранов
            import web_panel
            keys = web_panel.editable_keys()
            self.assertIn("embed.home.banner", keys)
            self.assertIn("access_denied.banner", keys)
            self.assertNotIn("embed.buttons.banner", keys)
            core.set_setting("embed.buttons.banner", "https://cdn/x.png")
            self.assertIsNone(core.panel_embed(i, "embed.buttons", count=0, max=20, buttons="").banner_url)
        finally:
            core.reset_setting("embed.buttons.banner")
            if old is None:
                os.environ.pop("WEB_PANEL_URL", None)
            else:
                os.environ["WEB_PANEL_URL"] = old

    def test_auto_messages_have_banner_slots(self):
        """Новая заявка, итог проверки и ЛС заявителю — со своими баннерами."""
        from extended_modules import decide
        g = make_guild()
        keys = __import__("web_panel").editable_keys()
        for screen in core.AUTO_BANNER_SCREENS:
            self.assertIn(f"{screen}.banner", keys)
        form_id = database.save_form(GUILD, STAFF, "Набор", "", json.dumps([{"label": "Возраст"}]),
                                     reviewer_user_ids_json=json.dumps([STAFF]), dm_creator=1)
        sid = database.save_submission(form_id, GUILD, MEMBER, json.dumps({"Возраст": "20"}))
        dms = []

        async def dm(**kwargs):
            dms.append(kwargs)

        g.members[MEMBER].send = dm
        try:
            core.set_setting("forms.submission.banner", "https://cdn/new.png")
            core.set_setting("forms.approved.banner", "https://cdn/ok.png")
            reviewer = FakeInteraction(g, g.members[STAFF])
            card = core.panel_embed(reviewer, "forms.submission", form="Набор", user="<@1>", id=sid)
            sent = core.expand_embeds({"embed": card})["embeds"]
            self.assertEqual([e.image.url for e in sent[:1]], ["https://cdn/new.png"])
            edits = []

            async def edit(**kwargs):
                edits.append(kwargs)

            message = types.SimpleNamespace(embeds=sent, edit=edit)
            reviewer.response.done = True
            run(decide(reviewer, sid, "approved", "", message))
            banner, result = edits[0]["embeds"]
            self.assertEqual(banner.image.url, "https://cdn/ok.png")
            self.assertEqual(result.title, card.title)
            self.assertEqual(dms[0]["embeds"][0].image.url, "https://cdn/ok.png")
            self.assertIn("Набор", dms[0]["embeds"][1].description)
        finally:
            for key in ("forms.submission.banner", "forms.approved.banner"):
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
        self.assertEqual(web_panel.validate("embed.home.banner", "none"), ("none", None))
        self.assertIn("embed.home.banner", web_panel.editable_keys())
        self.assertEqual(web_panel.validate("thumbnail.embed", "art:thumbnail.embed.png"), ("art:thumbnail.embed.png", None))
        self.assertIsNotNone(web_panel.validate("thumbnail.embed", "art:../bot.db")[1])

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
        self.assertEqual(self.Image.open(io.BytesIO(icon)).size, (logo_module.ICON_SIZE, logo_module.ICON_SIZE))
        noisy = self.Image.effect_noise((900, 900), 100).convert("RGB")
        buffer = io.BytesIO()
        noisy.save(buffer, format="PNG")
        small = logo_module.role_icon_bytes(buffer.getvalue())
        self.assertIsNotNone(small)
        self.assertLessEqual(len(small), logo_module.ROLE_ICON_MAX_BYTES)

    def test_small_subject_fills_the_icon(self):
        import logo_module
        canvas = self.Image.new("RGB", (1024, 1024), "white")
        canvas.paste(self.Image.new("RGB", (120, 120), "red"), (452, 452))  # маленький значок посреди фона
        icon = self.Image.open(io.BytesIO(logo_module.fit_subject(canvas)))
        self.assertEqual(icon.size, (500, 500))
        self.assertEqual(icon.format, "PNG")
        red = sum(1 for p in icon.convert("RGB").getdata() if p[0] > 200 and p[1] < 60)
        self.assertGreater(red / (500 * 500), 0.7)  # рисунок занимает почти весь кадр

    def test_generate_uses_reference_image(self):
        import logo_module
        calls = {}

        class Client:
            async def image_to_image(self, source, prompt, negative_prompt, model):
                calls["prompt"], calls["source"] = prompt, source
                return self_image

            async def text_to_image(self, *args, **kwargs):
                calls["text"] = True
                return self_image

        self_image = self.Image.new("RGB", (1024, 1024), "white")
        self_image.paste(self.Image.new("RGB", (300, 300), "blue"), (362, 362))
        style_id = database.save_logo_style(GUILD, ADMIN, "Реф", "thick black outline", "")
        old_dir = os.environ.get("LOGO_DIR")
        os.environ["LOGO_DIR"] = tempfile.mkdtemp()
        old_client = logo_module._client
        logo_module._client = lambda: Client()
        try:
            logo_module.add_reference(database.get_logo_style(style_id), self.png)
            png = run(logo_module.generate_image("generate", database.get_logo_style(style_id), "кот"))
            self.assertNotIn("text", calls)  # не текстовая модель, а перерисовка по референсу
            self.assertIn("REFERENCE", calls["prompt"])
            self.assertIn("кот", calls["prompt"])
            self.assertEqual(self.Image.open(io.BytesIO(png)).size, (500, 500))
        finally:
            logo_module._client = old_client
            if old_dir is None:
                os.environ.pop("LOGO_DIR", None)
            else:
                os.environ["LOGO_DIR"] = old_dir

    def test_generate_by_reference_analysis(self):
        import json as json_lib
        import logo_module
        import logo_style_ai
        calls = {"edit": [], "text": []}
        analysis = {"subject": "a star", "rendering": "3D render, glossy", "perspective": "slight 3/4 from above",
                    "facing": "towards viewer", "rotation": "tilted 10 degrees clockwise", "outline": "none",
                    "format_background": "transparent", "color_character": "pastel, warm, soft contrast",
                    "prompt": "A glossy 3D star..."}
        bible = {"invariants": "glossy 3D vinyl toy look, pastel warm palette", "variations": "subjects",
                 "prompt_template": "A glossy 3D icon of {subject}, pastel", "negative": "flat 2D vector",
                 "summary_ru": "Объёмные глянцевые пастельные иконки."}

        async def fake_chat(models, content, max_tokens):
            if isinstance(content, str):
                if content.startswith("Someone asked for a Discord role icon"):
                    return "a cat wizard"
                return "```json\n" + json_lib.dumps(bible) + "\n```"
            if content[0]["text"] == logo_style_ai.ANALYZE_PROMPT:
                return json_lib.dumps(analysis)
            return "Candidate 3"

        def image():
            canvas = self.Image.new("RGB", (1024, 1024), "white")
            canvas.paste(self.Image.new("RGB", (300, 300), "blue"), (362, 362))
            return canvas

        class Client:
            async def image_to_image(self, source, prompt, negative_prompt, model):
                calls["edit"].append((prompt, negative_prompt))
                return image()

            async def text_to_image(self, prompt, **kwargs):
                calls["text"].append((prompt, kwargs["negative_prompt"]))
                return image()

        style_id = database.save_logo_style(GUILD, ADMIN, "3D", "", "")
        old_dir, old_client, old_chat = os.environ.get("LOGO_DIR"), logo_module._client, logo_style_ai._chat
        os.environ["LOGO_DIR"], os.environ["HF_TOKEN"] = tempfile.mkdtemp(), "test"
        os.environ["LOGO_BASE_DATASET"] = "off"  # основа из набора иконок — в отдельном тесте
        logo_module._client, logo_style_ai._chat = (lambda: Client()), fake_chat
        try:
            for _ in range(3):
                logo_module.add_reference(database.get_logo_style(style_id), self.png)
            row = run(logo_module.ensure_style_text(database.get_logo_style(style_id)))
            self.assertEqual(row[4], bible["invariants"])  # описание стиля — из разбора
            png = run(logo_module.generate_image("generate", row, "кот-волшебник"))
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertEqual(len(calls["edit"]), 2)  # два типичных референса
            self.assertEqual(len(calls["text"]), 1)  # и текстовая модель по библии
            self.assertIn("a cat wizard", calls["edit"][0][0])
            self.assertIn("A glossy 3D icon of a cat wizard", calls["text"][0][0])
            self.assertIn("flat 2D vector", calls["text"][0][1])
            saved = logo_style_ai.load(style_id)
            self.assertEqual(len(saved["refs"]), 3)
            self.assertEqual(saved["bible"]["summary_ru"], bible["summary_ru"])
            report = logo_style_ai.report(row)
            self.assertIn("tilted 10 degrees clockwise", report)
            self.assertIn("Объёмные", report)
        finally:
            logo_module._client, logo_style_ai._chat = old_client, old_chat
            os.environ.pop("HF_TOKEN", None)
            os.environ.pop("LOGO_BASE_DATASET", None)
            if old_dir is None:
                os.environ.pop("LOGO_DIR", None)
            else:
                os.environ["LOGO_DIR"] = old_dir

    def test_nano_banana_gets_references_and_russian_brief(self):
        import base64 as b64
        import logo_gemini
        import logo_module
        sent = []
        result = io.BytesIO()
        canvas = self.Image.new("RGB", (1024, 1024), "white")
        canvas.paste(self.Image.new("RGB", (400, 400), "green"), (300, 300))
        canvas.save(result, "PNG")

        async def fake_post(url, body, headers):
            sent.append((url, body))
            if "3.1-flash" in url:
                return 404, {"error": {"message": "models/x is not found"}}
            return 200, {"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": b64.b64encode(result.getvalue()).decode()}}]}}]}

        style_id = database.save_logo_style(GUILD, ADMIN, "Банан", "", "")
        old_dir, old_post = os.environ.get("LOGO_DIR"), logo_gemini._post
        os.environ["LOGO_DIR"], os.environ["GEMINI_API_KEY"] = tempfile.mkdtemp(), "key"
        logo_gemini._post = fake_post
        try:
            for _ in range(4):
                logo_module.add_reference(database.get_logo_style(style_id), self.png)
            self.assertIsNone(logo_module.backend_problem())
            png = run(logo_module.generate_image("generate", database.get_logo_style(style_id), "содружество"))
            self.assertEqual(self.Image.open(io.BytesIO(png)).size, (logo_module.ICON_SIZE, logo_module.ICON_SIZE))
            self.assertEqual(len(sent), 2)  # первая модель недоступна — взята следующая
            parts = sent[1][1]["contents"][0]["parts"]
            self.assertIn("содружество", parts[0]["text"])
            self.assertEqual(len(parts) - 1, 4)  # все референсы картинками
            with self.assertRaises(RuntimeError):
                async def blocked(url, body, headers):
                    return 200, {"promptFeedback": {"blockReason": "SAFETY"}}
                logo_gemini._post = blocked
                run(logo_module.generate_image("generate", database.get_logo_style(style_id), "кот"))
        finally:
            logo_gemini._post = old_post
            os.environ.pop("GEMINI_API_KEY", None)
            if old_dir is None:
                os.environ.pop("LOGO_DIR", None)
            else:
                os.environ["LOGO_DIR"] = old_dir

    def test_nano_banana_via_openrouter(self):
        import base64 as b64
        import logo_gemini
        import logo_module
        sent = []
        result = io.BytesIO()
        self.Image.new("RGB", (512, 512), "orange").save(result, "PNG")
        url = "data:image/png;base64," + b64.b64encode(result.getvalue()).decode()

        async def fake_post(endpoint, body, headers):
            sent.append((endpoint, body, headers))
            return 200, {"choices": [{"message": {"content": "", "images": [{"image_url": {"url": url}}]}}]}

        style_id = database.save_logo_style(GUILD, ADMIN, "OR", "", "")
        old_dir, old_post = os.environ.get("LOGO_DIR"), logo_gemini._post
        os.environ["LOGO_DIR"], os.environ["OPENROUTER_API_KEY"] = tempfile.mkdtemp(), "sk-or"
        logo_gemini._post = fake_post
        try:
            logo_module.add_reference(database.get_logo_style(style_id), self.png)
            png = run(logo_module.generate_image("generate", database.get_logo_style(style_id), "содружество"))
            self.assertTrue(png.startswith(b"\x89PNG"))
            endpoint, body, headers = sent[0]
            self.assertIn("openrouter.ai", endpoint)
            self.assertEqual(body["model"], "google/gemini-3.1-flash-image")
            self.assertEqual(headers["Authorization"], "Bearer sk-or")
            self.assertEqual([c["type"] for c in body["messages"][0]["content"]], ["text", "image_url"])
            self.assertIn("содружество", body["messages"][0]["content"][0]["text"])

            async def broke(endpoint, body, headers):
                return 402, {"error": {"message": "Insufficient credits"}}
            logo_gemini._post = broke
            with self.assertRaisesRegex(RuntimeError, "пополни"):
                run(logo_module.generate_image("generate", database.get_logo_style(style_id), "кот"))
        finally:
            logo_gemini._post = old_post
            os.environ.pop("OPENROUTER_API_KEY", None)
            if old_dir is None:
                os.environ.pop("LOGO_DIR", None)
            else:
                os.environ["LOGO_DIR"] = old_dir

    def test_flat_icon_base_used_under_style(self):
        import icon_base
        import logo_module
        import logo_style_ai
        rows = icon_base.parse_csv(
            "set,title,style,link,tags\n"
            "Handshake,business,Flat,https://x/1.png,\"handshake,deal,agreement,hands\"\n"
            "Golden gate bridge,landmarks,Flat,https://x/2.png,\"bridge,golden\"\n"
            "Laurel,rewards,Flat,https://x/3.png,\"laurel,wreath,victory\"\n")
        self.assertEqual([r["set"] for r in icon_base.search("a golden laurel wreath", rows)], ["Laurel"])
        self.assertEqual(icon_base.search("a dragon", rows), [])
        calls = []
        transparent = io.BytesIO()
        self.Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(transparent, "PNG")

        async def fake_fetch(url):
            return transparent.getvalue()

        async def fake_subject(brief):
            return "two hands clasped in a firm handshake"

        class Client:
            async def image_to_image(self, source, prompt, negative_prompt, model):
                calls.append((source, prompt))
                canvas = self.__class__.canvas()
                return canvas

            @staticmethod
            def canvas():
                from PIL import Image
                image = Image.new("RGB", (1024, 1024), "white")
                image.paste(Image.new("RGB", (300, 300), "red"), (362, 362))
                return image

        old = (logo_module._client, icon_base._fetch, icon_base._rows, logo_style_ai.english_subject,
               logo_style_ai.style_bible, os.environ.get("LOGO_DIR"))
        os.environ["LOGO_DIR"], os.environ["HF_TOKEN"] = tempfile.mkdtemp(), "test"
        logo_module._client, icon_base._fetch, icon_base._rows = (lambda: Client()), fake_fetch, rows
        logo_style_ai.english_subject = fake_subject

        async def no_bible(row, **kwargs):
            return None
        logo_style_ai.style_bible = no_bible
        try:
            style_id = database.save_logo_style(GUILD, ADMIN, "Глина", "soft 3D clay", "")
            png = run(logo_module.generate_image("generate", database.get_logo_style(style_id), "содружество"))
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertEqual(len(calls), 1)
            source, prompt = calls[0]
            self.assertIn("ONLY as a base", prompt)
            self.assertIn("soft 3D clay", prompt)
            base = self.Image.open(io.BytesIO(source))
            self.assertEqual(base.getpixel((5, 5)), (255, 255, 255))  # прозрачность -> белый фон
            self.assertIn("Handshake", icon_base.used["two hands clasped in a firm handshake"])
        finally:
            (logo_module._client, icon_base._fetch, icon_base._rows, logo_style_ai.english_subject,
             logo_style_ai.style_bible, old_dir) = old
            os.environ.pop("HF_TOKEN", None)
            if old_dir is None:
                os.environ.pop("LOGO_DIR", None)
            else:
                os.environ["LOGO_DIR"] = old_dir

    def test_reference_analysis_helpers(self):
        import logo_style_ai
        self.assertEqual(logo_style_ai.parse_json('ok: {"a": 1, "b": [2,],}'), {"a": 1, "b": [2]})
        self.assertIsNone(logo_style_ai.parse_json("no json"))
        voted = logo_style_ai.vote([{"rendering": "3D clay"}, {"rendering": "3D clay"}, {"rendering": "flat"}])
        self.assertIn("{subject}", voted["prompt_template"])
        self.assertIn("3D clay", voted["invariants"])
        self.assertIn("flat 2D", voted["negative"])

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


class CardCommandButtonTests(unittest.TestCase):
    def test_no_edit_button_when_card_has_command(self):
        import cards
        card = cards.get_card(cards.create_card(GUILD, STAFF, "Кики", "counter"))
        self.assertIsNotNone(run(cards.card_message(card))["view"])
        card["command"] = "kick"
        self.assertIsNone(run(cards.card_message(card))["view"])


class CounterWizardTests(unittest.TestCase):
    def test_existing_values_and_steps(self):
        import cards
        import counter_wizard as cw
        import embed_module
        guild_id = 7777
        rating = cards.get_card(cards.create_card(guild_id, STAFF, "Кики", "rating"))
        rating["data"]["rows"] = [{"name": "Kick Ass", "score": 3}]
        cards.save_card(rating)
        table = cards.get_card(cards.create_card(guild_id, STAFF, "Табло", "table"))
        table["data"]["rows"] = [["Пёс", "4", "пт"], ["Кот", "дизайн", "сб"]]
        cards.save_card(table)
        cards.create_card(guild_id, STAFF, "Победы", "counter")
        database.change_counter(guild_id, "очки", delta=5)
        names = {name for name, _, _ in cw.existing(guild_id)}
        self.assertTrue({"kickass", "пёс", "победы", "очки"} <= names)
        self.assertNotIn("кот", names)  # в строке нет числа
        self.assertEqual(cw.counter_name("Kick Ass!"), "kickass")
        committed = []
        original = embed_module.commit_pending

        async def fake_commit(interaction, state, pending, target, builder_view, raw):
            committed.append(raw)
        embed_module.commit_pending = fake_commit
        try:
            ops = cw.OpsView("kickass", None, {}, "buttons", None)
            modal = cw.AmountModal("kickass", "-", None, {}, "buttons", None)
            modal.amount._value = "3"
            run(modal.on_submit(None))
            run(ops.reset.callback(None))
            self.assertEqual(committed, ["kickass -3", "kickass =0"])
            import actions
            self.assertEqual(actions.parse_counter(committed[0]), ("kickass", "-", 3))
            self.assertTrue(cards.apply_counter(cards.get_card(rating["id"]), "kickass", "-1"))
        finally:
            embed_module.commit_pending = original


class ActionWizardTests(unittest.TestCase):
    def test_picker_is_russian_and_role_menu_needs_role_rights(self):
        import action_wizard as aw
        g = make_guild()
        st = types.SimpleNamespace(owner_id=STAFF, build_id=None)
        text = aw.pick_embed(FakeInteraction(g, g.members[STAFF]), st).description
        self.assertIn("Ответить текстом", text)
        self.assertIn("Меню ролей", text)
        self.assertNotIn("message.send", text)
        member_state = types.SimpleNamespace(owner_id=MEMBER, build_id=None)
        text = aw.pick_embed(FakeInteraction(g, g.members[MEMBER]), member_state).description
        self.assertNotIn("Меню ролей", text)  # участник не собирает кнопки, раздающие роли
        menu = aw.dump({"roles": [100], "single": False})
        self.assertEqual(actions.validate_action_value(FakeInteraction(g, g.members[MEMBER]), "select.trigger", menu)[1],
                         "actions.no_access")
        self.assertIsNone(actions.validate_action_value(FakeInteraction(g, g.members[STAFF]), "select.trigger", menu)[1])
        bad = aw.dump({"roles": [102], "single": False})
        self.assertEqual(actions.validate_action_value(FakeInteraction(g, g.members[STAFF]), "select.trigger", bad)[1],
                         "roles.dangerous")

    def test_reply_targets_and_variables(self):
        import action_wizard as aw
        g = make_guild()
        channel = FakeChannel(g, 9600)

        async def check():
            i = FakeInteraction(g, g.members[MEMBER])
            await actions.dispatch_action(i, "message.send", "Привет, {user_id}!", creator_id=STAFF)
            self.assertEqual(i.last, f"Привет, {MEMBER}!")  # старый формат: скрыто нажавшему, с переменными
            value, error = actions.validate_action_value(i, "message.send", aw.dump({"to": "channel", "text": "Всем {user_id}"}))
            self.assertIsNone(error)
            i = FakeInteraction(g, g.members[MEMBER])
            i.channel = channel
            await actions.dispatch_action(i, "message.send", value, creator_id=STAFF)
            self.assertEqual(channel.sent[-1]["content"], f"Всем {MEMBER}")
            i2 = FakeInteraction(g, g.members[MEMBER])
            i2.channel = channel
            await actions.dispatch_action(i2, "message.send", value, creator_id=STAFF)
            self.assertIn("Не так часто", i2.last)  # пауза против спама в канал
            dms = []

            async def send(text):
                dms.append(text)
            g.members[MEMBER].send = send
            i = FakeInteraction(g, g.members[MEMBER])
            await actions.dispatch_action(i, "message.send", aw.dump({"to": "dm", "text": "лично"}), creator_id=STAFF)
            self.assertEqual(dms, ["лично"])
        run(check())

    def test_role_menu_gives_and_takes_roles(self):
        import action_wizard as aw
        g = make_guild()
        member = g.members[MEMBER]
        member.guild = g
        g.me.guild_permissions = discord.Permissions(manage_roles=True)

        async def check():
            i = FakeInteraction(g, member)
            added, removed = await aw.apply_role_menu(i, [100, 105], [100], False, STAFF, exact=False)
            self.assertEqual([r.id for r in added], [100])
            added, removed = await aw.apply_role_menu(i, [100, 105], [100], False, STAFF, exact=False)
            self.assertEqual([r.id for r in removed], [100])  # выбрал ещё раз — снята
            await aw.apply_role_menu(i, [100, 105], [100], True, STAFF, exact=False)
            # роль выше бота в меню не работает — ничего не меняется
            self.assertEqual(aw.role_menu_result(await aw.apply_role_menu(i, [100, 103], [103], True, STAFF, exact=False)),
                             core.t("action_wizard.role_menu.nothing"))
            # личное меню: выбранное = итоговый набор
            added, removed = await aw.apply_role_menu(i, [100, 105], [], False, STAFF, exact=True)
            self.assertEqual([r.id for r in removed], [100])
            self.assertEqual(await aw.apply_role_menu(i, [100], [100], False, MEMBER, exact=True),
                             "actions.creator_revoked")
        run(check())

    def test_every_choice_opens_its_first_step(self):
        import action_wizard as aw
        import embed_module as em
        g = make_guild()
        st = em.EmbedState(GUILD, STAFF)
        st.build_id = database.save_message_build(GUILD, STAFF, "Я", "x", "[]", "[]")
        database.save_message_build(GUILD, STAFF, "Другой", "y", "[]", "[]")
        hub = em.InteractiveHubView(st, (discord.Embed(title="x"), None))
        builder = em.ButtonBuilderView(st, hub, (discord.Embed(title="x"), None))
        screens = {}

        async def check():
            for choice, keys, _ in aw.CHOICES:
                i = FakeInteraction(g, g.members[ADMIN])

                async def edit_message(**kwargs):
                    i.response.done = True
                    screens[choice] = kwargs
                i.response.edit_message = edit_message
                wizard = aw.Wizard(i, st, {"label": "x"}, "buttons", builder)
                await aw.START[choice](i, wizard, aw.allowed_keys(i, st))
            # «Показать сообщение»: выбрали build -> вопрос «как показать»
            i = FakeInteraction(g, g.members[ADMIN])
            shown = []

            async def edit(**kwargs):
                shown.append(kwargs)
            i.response.edit_message = edit
            wizard = aw.Wizard(i, st, {"label": "x"}, "buttons", builder)
            await aw.show_mode(i, wizard, aw.allowed_keys(i, st), "1")
            self.assertIn("Как показать", shown[0]["embed"].description)
            # «Обновить сообщения» предлагает и этот же build
            refresh = screens["refresh"]["view"]
            select = next(item for item in refresh.children if isinstance(item, discord.ui.Select))
            self.assertEqual(select.options[0].value, str(st.build_id))
        run(check())
        self.assertEqual(set(screens), {c for c, _, _ in aw.CHOICES})
        self.assertIn("Кому показать", screens["reply"]["embed"].description)

    def test_role_menu_in_message_and_descriptions(self):
        import action_wizard as aw
        view = actions.build_components("b", 5, [], {"type": "role_menu", "roles": [100, 105],
                                                     "names": ["Художник", "Staff"], "single": True})
        select = view.children[0].item
        self.assertEqual(select.custom_id, "rb:bm:b:5")
        self.assertEqual([o.label for o in select.options], ["Художник", "Staff"])
        self.assertEqual(select.max_values, 1)
        self.assertIn("<@&100>", aw.describe("role.toggle", "100"))
        self.assertIn("в канал", aw.describe("message.send", aw.dump({"to": "channel", "text": "hi"})))
        self.assertIn("ответ нажавшему", aw.describe("message.send", "hi"))
        self.assertEqual(aw.webhook_options("7"), {"id": 7, "text": ""})
        self.assertEqual(aw.webhook_options(aw.dump({"id": 7, "text": "x"})), {"id": 7, "text": "x"})
        self.assertEqual(core.action_name("build.goto"), "➡️ Шаг мастера")


class GalleryTests(unittest.TestCase):
    def test_type_and_style_galleries(self):
        import cards
        import card_templates as ct
        from PIL import Image
        for key in ct.SPECS:
            self.assertEqual(len(ct.sample_uses(key)), 3)
        self.assertEqual(ct.spec("streak")["name"], "Счётчик дней")

        async def check():
            types = cards.TypeGalleryView(0)
            self.assertEqual(len(types.keys), 4)
            self.assertEqual(types.pages, -(-len(ct.TEMPLATES) // 4))
            numbers = [c for c in types.children if isinstance(c, discord.ui.Button) and c.emoji
                       and str(c.emoji) in cards.NUMBERS]
            self.assertEqual(len(numbers), 4)
            self.assertEqual(cards.TypeGalleryView(types.pages).page, 0)  # листание по кругу
            last = cards.TypeGalleryView(types.pages - 1)
            self.assertGreaterEqual(len(last.keys), 1)
            image = Image.open(io.BytesIO(types.build()))
            self.assertGreater(image.width, 1000)
            styles = cards.StyleGalleryView("streak", page=1, back_page=0)
            self.assertEqual(len(styles.keys), 4)
            self.assertTrue(Image.open(io.BytesIO(styles.build())).height > 300)
            self.assertIn("подходит", cards.TypeGalleryView(0).line("progress"))

        run(check())


class ClanTreasuryTests(unittest.TestCase):
    CARD = {
        "title": "✅ Перевод в казну подтверждён",
        "description": "Подтвердил <@555000000000000001>.",
        "fields": [
            {"name": "Переводит", "value": "<@1222028407295049898> — участник"},
            {"name": "Клан", "value": "anhedonia `[ANH]`"},
            {"name": "Сумма", "value": "50 голды"},
            {"name": "В казну дойдёт", "value": "37 (комиссия 25%)"},
            {"name": "Респект клану", "value": "+0,05"},
            {"name": "Голду выдал", "value": "<@555000000000000002>"},
            {"name": "За что", "value": "—"},
            {"name": "За 7 дней", "value": "этот участник перевёл в клан 1 раз на 50 голды"},
        ],
    }

    def test_parse_approved_card(self):
        import clan_treasury as ct
        card, problem = ct.parse_card(self.CARD)
        self.assertIsNone(problem)
        self.assertEqual((card["tag"], card["name"], card["to_treasury"], card["gross"]), ("ANH", "anhedonia", 37, 50))
        self.assertAlmostEqual(card["respect"], 0.05)
        self.assertEqual(card["member_id"], 1222028407295049898)
        self.assertEqual(card["giver_id"], 555000000000000002)
        pending = dict(self.CARD, title="Перевод в казну клана ждёт подтверждения")
        self.assertEqual(ct.parse_card(pending), (None, "clan_treasury.not_approved"))
        self.assertEqual(ct.parse_card(dict(self.CARD, title="❌ Перевод отклонён")), (None, "clan_treasury.rejected"))
        waiting = dict(self.CARD, title="🏰 Перевод в казну — нужно подтверждение")
        self.assertEqual(ct.parse_card(waiting), (None, "clan_treasury.not_approved"))
        for title in ("⛔ Перевод аннулирован", "↩️ Отклонено — голда возвращена"):
            self.assertEqual(ct.parse_card(dict(self.CARD, title=title)), (None, "clan_treasury.rejected"))
        self.assertEqual(ct.parse_card(dict(self.CARD, fields=[]))[1], "clan_treasury.unknown_format")

    def test_fix_once_and_undo_via_mention(self):
        import clan_treasury as ct
        guild = make_guild()
        guild.self_role = None
        guild.id = 4242
        replies = []

        source = types.SimpleNamespace(id=9001, author=types.SimpleNamespace(id=31337, bot=True),
                                       channel=types.SimpleNamespace(id=5), embeds=[discord.Embed.from_dict(self.CARD)])

        def ping(author_id, text, reference=True):
            async def reply(content, **kwargs):
                replies.append(content)
            return types.SimpleNamespace(
                author=guild.members[author_id], guild=guild, content=f"<@{guild.me.id}> {text}",
                mentions=[guild.me], role_mentions=[], reply=reply,
                reference=types.SimpleNamespace(message_id=source.id, resolved=source) if reference else None)

        guild.members[OWNER].bot = False
        guild.members[MEMBER].bot = False
        os.environ["CLAN_SOURCE_BOT_ID"] = "31337"
        try:
            run(ct.on_message(ping(MEMBER, "зафиксируй")))
            self.assertIn("🔒", replies[-1])
            run(ct.on_message(ping(OWNER, "зафиксируй")))
            self.assertIn("[ANH]", replies[-1])
            self.assertEqual(ct.get_clan(4242, "anh")[2:], (37, 0.05))
            run(ct.on_message(ping(OWNER, "зафиксируй")))
            self.assertIn("уже зафиксирован", replies[-1])
            self.assertEqual(ct.get_clan(4242, "ANH")[2], 37)
            run(ct.on_message(ping(OWNER, "казна", reference=False)))
            self.assertIn("anhedonia", replies[-1])
            run(ct.on_message(ping(OWNER, "отмени")))
            self.assertEqual(ct.get_clan(4242, "ANH")[2:], (0, 0))
            run(ct.on_message(ping(OWNER, "отмени")))
            self.assertIn("уже отменена", replies[-1])
            source.author.id = 1  # не тот бот
            run(ct.on_message(ping(OWNER, "зафиксируй")))
            self.assertIn("не карточка бота сервера", replies[-1])
        finally:
            os.environ.pop("CLAN_SOURCE_BOT_ID", None)


class ClanInfoTests(unittest.TestCase):
    def test_question_by_mention_answers_topic(self):
        import clan_info
        import clan_treasury
        import emoji_export
        guild = make_guild()
        guild.self_role = None
        sent = []

        def ping(text, author=MEMBER):
            async def reply(content=None, **kwargs):
                sent.append((content, kwargs))
            guild.members[author].bot = False
            return types.SimpleNamespace(
                author=guild.members[author], guild=guild, content=f"<@{guild.me.id}> {text}",
                mentions=[guild.me], role_mentions=[], reply=reply, reference=None,
                channel=types.SimpleNamespace(id=1))

        run(clan_info.on_message(ping("как работает продление клана и что будет при долге?")))
        embed = sent[-1][1]["embed"]
        self.assertIn("ПРОДЛЕНИЕ", embed.title)
        self.assertIn("5000", embed.description)
        self.assertEqual(sent[-1][1]["view"].children[0].item.custom_id, "rb:clinfo")
        run(clan_info.on_message(ping("расскажи про кланы")))
        self.assertIn("КАК ЭТО УСТРОЕНО", sent[-1][1]["embed"].title)
        run(clan_info.on_message(ping("как пополнить казну клана?")))
        self.assertIn("КАЗНА", sent[-1][1]["embed"].title)
        # вопрос про казну — не команда казны и не перенос эмодзи
        self.assertFalse(clan_treasury.wants(ping("как пополнить казну клана?")))
        self.assertTrue(clan_treasury.wants(ping("казна ANH")))
        self.assertFalse(clan_info.wants(ping("казна ANH")))
        before = len(sent)
        run(emoji_export.on_message(ping("что даёт огонёк у кланов?")))
        self.assertEqual(len(sent), before)  # участнику не пишет «только владелец»
        run(clan_info.on_message(ping("привет")))
        self.assertEqual(len(sent), before)  # без слова «клан» молчит
        for key in ["overview"] + clan_info.TOPIC_KEYS:
            self.assertLessEqual(len(clan_info.topic_embed(key).description), 4096)
            self.assertNotIn("clan_info.", clan_info.topic_embed(key).title)


class ClanAiTests(unittest.TestCase):
    def test_neural_answer_with_rules_limits_and_fallback(self):
        import clan_info
        guild = make_guild()
        guild.self_role = None
        sent, calls = [], []

        def ping(text, author=MEMBER, reference=None):
            async def reply(content=None, **kwargs):
                sent.append((content, kwargs))
            guild.members[author].bot = False
            return types.SimpleNamespace(
                author=guild.members[author], guild=guild, content=f"<@{guild.me.id}> {text}",
                mentions=[guild.me], role_mentions=[], reply=reply, reference=reference,
                channel=types.SimpleNamespace(id=1))

        async def fake_post(url, body, headers):
            calls.append((url, body))
            return 200, {"candidates": [{"content": {"parts": [{"text": "Продление — **5000** раз в 30 дней."}]}}]}

        original = clan_info._post
        clan_info._post = fake_post
        os.environ["GEMINI_API_KEY"] = "test"
        clan_info._last_question.clear()
        clan_info._daily.clear()
        try:
            run(clan_info.on_message(ping("сколько стоит продление клана?")))
            embed = sent[-1][1]["embed"]
            self.assertIn("5000", embed.description)
            system = calls[-1][1]["systemInstruction"]["parts"][0]["text"]
            self.assertIn("ПРОДЛЕНИЕ", system)  # правила кланов — база ответа
            self.assertIn("сколько стоит продление", calls[-1][1]["contents"][-1]["parts"][0]["text"])
            # уточнение ответом на ответ Purr: без слова «клан», с прошлым ответом в контексте
            answer = types.SimpleNamespace(author=guild.me, embeds=[embed])
            clan_info._last_question.clear()
            run(clan_info.on_message(ping("а если на 5-й день?", reference=types.SimpleNamespace(resolved=answer))))
            self.assertEqual(calls[-1][1]["contents"][0]["role"], "model")
            # пауза между вопросами
            run(clan_info.on_message(ping("а огонёк у кланов?")))
            self.assertIn("Подожди", sent[-1][0])
            self.assertEqual(len(calls), 2)
            # нейросеть молчит — карточка темы
            async def broken(url, body, headers):
                return 500, {}
            clan_info._post = broken
            clan_info._last_question.clear()
            run(clan_info.on_message(ping("что даёт огонёк клану?")))
            self.assertIn("ОГОНЁК", sent[-1][1]["embed"].title)
        finally:
            clan_info._post = original
            os.environ.pop("GEMINI_API_KEY", None)


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
                em.CustomListBuilderView(st, hub, back),
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
                ex.RoleMenuModal([1, 2]), bot.ButtonSetNameModal(bs, back),
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
        # docs/STRUCTURE.md: 6 команд вместо 12
        self.assertEqual(names, sorted(["embed", "library", "interactive", "forms", "logo", "eminemovna", "webhook",
                                        "emoji", "pictures", "whisper", "В референсы стиля",
                                    "В цитаты дня", "Шепнуть"]))


if __name__ == "__main__":
    unittest.main()
