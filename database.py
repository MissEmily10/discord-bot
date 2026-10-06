import json
import os
import sqlite3
import time

# Путь к БД можно переопределить (например, на volume хостинга); по умолчанию —
# bot.db в рабочей папке процесса, как и раньше.
DATABASE_NAME = os.getenv("DATABASE_PATH", "bot.db")

# Строка bot_settings с этим guild_id — общие для всего бота настройки
# (тексты, цвета, thumbnails). Остальные guild_id — наследие старого /design.
GLOBAL_SETTINGS_ID = 0


def _now():
    return int(time.time())


def _json_value(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def get_connection():
    connection = sqlite3.connect(DATABASE_NAME)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database():
    connection = get_connection()
    cursor = connection.cursor()

    # Existing access tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS command_access (
            guild_id INTEGER NOT NULL,
            command_name TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            expires_at INTEGER,
            PRIMARY KEY (guild_id, command_name, user_id)
        )
    """)

    cursor.execute("PRAGMA table_info(command_access)")
    columns = [row[1] for row in cursor.fetchall()]
    if "expires_at" not in columns:
        cursor.execute("""
            ALTER TABLE command_access
            ADD COLUMN expires_at INTEGER
        """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_members (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            access_level TEXT NOT NULL DEFAULT 'member',
            expires_at INTEGER,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS role_access (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            access_level TEXT NOT NULL,
            expires_at INTEGER,
            PRIMARY KEY (guild_id, role_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS denied_users (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    # Message Build storage.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS message_builds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            name TEXT,
            content TEXT,
            embeds_json TEXT NOT NULL DEFAULT '[]',
            buttons_json TEXT NOT NULL DEFAULT '[]',
            visibility TEXT NOT NULL DEFAULT 'private',
            category TEXT NOT NULL DEFAULT 'general',
            allowed_role_ids_json TEXT NOT NULL DEFAULT '[]',
            visibility_levels_json TEXT NOT NULL DEFAULT '[]',
            interactive_json TEXT NOT NULL DEFAULT 'null',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    cursor.execute("PRAGMA table_info(message_builds)")
    mb_columns = {row[1] for row in cursor.fetchall()}
    if "allowed_role_ids_json" not in mb_columns:
        cursor.execute("""
            ALTER TABLE message_builds
            ADD COLUMN allowed_role_ids_json TEXT NOT NULL DEFAULT '[]'
        """)
    if "visibility_levels_json" not in mb_columns:
        cursor.execute("""
            ALTER TABLE message_builds
            ADD COLUMN visibility_levels_json TEXT NOT NULL DEFAULT '[]'
        """)
    if "interactive_json" not in mb_columns:
        cursor.execute("""
            ALTER TABLE message_builds
            ADD COLUMN interactive_json TEXT NOT NULL DEFAULT 'null'
        """)
    if "settings_json" not in mb_columns:
        # живое обновление, родитель стиля, варианты по ролям
        cursor.execute("ALTER TABLE message_builds ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")

    # Saved button sets are independent from message builds.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS button_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            buttons_json TEXT NOT NULL DEFAULT '[]',
            visibility TEXT NOT NULL DEFAULT 'private',
            category TEXT NOT NULL DEFAULT 'general',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    # Bot-wide runtime settings — backend for the future /design command.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            guild_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (guild_id, key)
        )
    """)

    # Action registry — granular permissions for what a button/select can do.
    # min_level — кто может СОЗДАТЬ кнопку с этим действием,
    # use_level — кто может НАЖАТЬ такую кнопку.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS action_registry (
            action_key TEXT PRIMARY KEY,
            min_level TEXT NOT NULL DEFAULT 'member',
            dangerous INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            use_level TEXT NOT NULL DEFAULT 'member'
        )
    """)
    cursor.execute("PRAGMA table_info(action_registry)")
    if "use_level" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute("""
            ALTER TABLE action_registry
            ADD COLUMN use_level TEXT NOT NULL DEFAULT 'member'
        """)
        # раньше уровень проверялся у нажавшего — для этих действий
        # нажимать по-прежнему могут только админы
        cursor.execute("""
            UPDATE action_registry SET use_level = 'admin'
            WHERE action_key IN ('message.edit', 'webhook.send')
        """)

    # Sent instances — makes Message Build a "living object": every real
    # message the bot sends from a build is remembered here, so it can be
    # live-edited, re-triggered, or tracked later.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_instances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            build_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            sent_at INTEGER NOT NULL,
            part_index INTEGER
        )
    """)
    cursor.execute("PRAGMA table_info(sent_instances)")
    if "part_index" not in {row[1] for row in cursor.fetchall()}:
        # номер сообщения внутри одной отправки (0 — начало новой отправки);
        # у старых записей NULL — для них работает запасная логика
        cursor.execute("ALTER TABLE sent_instances ADD COLUMN part_index INTEGER")

    # Старый /design сохранял 5 значений на конкретный сервер. Теперь
    # настройки общие: переносим их в глобальные, не перетирая уже заданные.
    cursor.execute("""
        INSERT OR IGNORE INTO bot_settings (guild_id, key, value)
        SELECT ?, key, value FROM bot_settings WHERE guild_id != ?
    """, (GLOBAL_SETTINGS_ID, GLOBAL_SETTINGS_ID))
    cursor.execute("""
        DELETE FROM bot_settings WHERE guild_id != ?
    """, (GLOBAL_SETTINGS_ID,))

    connection.commit()
    connection.close()

    _ensure_extended_tables()


# =========================
# COMMAND ACCESS
# =========================

def add_command_access(guild_id, command_name, user_id, expires_at=None):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO command_access
        (guild_id, command_name, user_id, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, command_name, user_id)
        DO UPDATE SET expires_at = excluded.expires_at
    """, (guild_id, command_name, user_id, expires_at))

    connection.commit()
    connection.close()


def get_command_access(guild_id, command_name):
    connection = get_connection()
    cursor = connection.cursor()
    now = int(time.time())

    cursor.execute("""
        DELETE FROM command_access
        WHERE expires_at IS NOT NULL
        AND expires_at <= ?
    """, (now,))

    cursor.execute("""
        SELECT user_id
        FROM command_access
        WHERE guild_id = ?
        AND command_name = ?
        AND (expires_at IS NULL OR expires_at > ?)
    """, (guild_id, command_name, now))

    users = [row[0] for row in cursor.fetchall()]
    connection.commit()
    connection.close()
    return users


def get_command_access_details(guild_id, command_name):
    connection = get_connection()
    cursor = connection.cursor()
    now = int(time.time())

    cursor.execute("""
        DELETE FROM command_access
        WHERE expires_at IS NOT NULL
        AND expires_at <= ?
    """, (now,))

    cursor.execute("""
        SELECT user_id, expires_at
        FROM command_access
        WHERE guild_id = ?
        AND command_name = ?
        AND (expires_at IS NULL OR expires_at > ?)
    """, (guild_id, command_name, now))

    users = cursor.fetchall()
    connection.commit()
    connection.close()
    return users


def remove_command_access(guild_id, command_name, user_id):
    connection = get_connection()
    connection.execute("""
        DELETE FROM command_access
        WHERE guild_id = ? AND command_name = ? AND user_id = ?
    """, (guild_id, command_name, user_id))
    connection.commit()
    connection.close()


# =========================
# ACCESS LEVELS
# =========================

def set_access_level(guild_id, user_id, access_level, expires_at=None):
    connection = get_connection()
    connection.execute("""
        INSERT INTO bot_members
        (guild_id, user_id, access_level, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET
            access_level = excluded.access_level,
            expires_at = excluded.expires_at
    """, (guild_id, user_id, access_level, expires_at))
    connection.commit()
    connection.close()


def get_access_level(guild_id, user_id):
    connection = get_connection()
    cursor = connection.cursor()
    now = int(time.time())

    cursor.execute("""
        SELECT access_level, expires_at
        FROM bot_members
        WHERE guild_id = ? AND user_id = ?
    """, (guild_id, user_id))

    result = cursor.fetchone()

    if result is None:
        connection.close()
        return "member"

    access_level, expires_at = result

    if expires_at is not None and expires_at <= now:
        cursor.execute("""
            UPDATE bot_members
            SET access_level = 'member', expires_at = NULL
            WHERE guild_id = ? AND user_id = ?
        """, (guild_id, user_id))
        connection.commit()
        connection.close()
        return "member"

    connection.close()
    return access_level


def get_member_info(guild_id, user_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT access_level, expires_at
        FROM bot_members
        WHERE guild_id = ? AND user_id = ?
    """, (guild_id, user_id))

    result = cursor.fetchone()
    connection.close()

    if result is None:
        return "member", None

    return result


def remove_member(guild_id, user_id):
    connection = get_connection()
    connection.execute("""
        DELETE FROM bot_members
        WHERE guild_id = ? AND user_id = ?
    """, (guild_id, user_id))
    connection.commit()
    connection.close()


# =========================
# ROLE AND DENIAL ACCESS
# =========================

def set_role_access(guild_id, role_id, access_level, expires_at=None):
    connection = get_connection()
    connection.execute("""
        INSERT INTO role_access (guild_id, role_id, access_level, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, role_id)
        DO UPDATE SET access_level = excluded.access_level,
                      expires_at = excluded.expires_at
    """, (guild_id, role_id, access_level, expires_at))
    connection.commit()
    connection.close()


def remove_role_access(guild_id, role_id):
    connection = get_connection()
    connection.execute("""
        DELETE FROM role_access
        WHERE guild_id = ? AND role_id = ?
    """, (guild_id, role_id))
    connection.commit()
    connection.close()


def get_all_role_access(guild_id):
    connection = get_connection()
    rows = connection.execute("""
        SELECT role_id, access_level, expires_at
        FROM role_access
        WHERE guild_id = ?
    """, (guild_id,)).fetchall()
    connection.close()
    return rows


def deny_user(guild_id, user_id, reason=None):
    connection = get_connection()
    connection.execute("""
        INSERT INTO denied_users (guild_id, user_id, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET reason = excluded.reason,
                      created_at = excluded.created_at
    """, (guild_id, user_id, reason, int(time.time())))
    connection.commit()
    connection.close()


def is_user_denied(guild_id, user_id):
    connection = get_connection()
    row = connection.execute("""
        SELECT reason
        FROM denied_users
        WHERE guild_id = ? AND user_id = ?
    """, (guild_id, user_id)).fetchone()
    connection.close()
    # Запрет без причины (reason = NULL) — всё равно запрет: возвращаем "",
    # чтобы проверки `is not None` его видели.
    if row is None:
        return None
    return row[0] if row[0] is not None else ""


def undeny_user(guild_id, user_id):
    connection = get_connection()
    connection.execute("""
        DELETE FROM denied_users
        WHERE guild_id = ? AND user_id = ?
    """, (guild_id, user_id))
    connection.commit()
    connection.close()


def get_denied_users(guild_id):
    connection = get_connection()
    rows = connection.execute("""
        SELECT user_id, reason, created_at
        FROM denied_users
        WHERE guild_id = ?
        ORDER BY created_at DESC
    """, (guild_id,)).fetchall()
    connection.close()
    return rows


# =========================
# MESSAGE BUILDS
# =========================

def save_message_build(
    guild_id,
    owner_id,
    name,
    content,
    embeds_json,
    buttons_json,
    visibility="private",
    category="general",
    allowed_role_ids_json="[]",
    visibility_levels_json="[]",
    interactive_json="null",
):
    now = int(time.time())
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO message_builds
        (guild_id, owner_id, name, content, embeds_json, buttons_json,
         visibility, category, allowed_role_ids_json, visibility_levels_json,
         interactive_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        guild_id, owner_id, name, content, embeds_json, buttons_json,
        visibility, category, allowed_role_ids_json, visibility_levels_json,
        interactive_json, now, now
    ))

    build_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return build_id


def get_message_build(build_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, guild_id, owner_id, name, content, embeds_json,
               buttons_json, visibility, category, allowed_role_ids_json,
               visibility_levels_json, interactive_json, created_at, updated_at
        FROM message_builds
        WHERE id = ?
    """, (build_id,))
    result = cursor.fetchone()
    connection.close()
    return result


def get_message_builds(guild_id, owner_id=None, include_public=True):
    connection = get_connection()
    cursor = connection.cursor()

    if owner_id is None:
        cursor.execute("""
            SELECT id, owner_id, name, visibility, category, updated_at
            FROM message_builds
            WHERE guild_id = ?
            ORDER BY updated_at DESC
        """, (guild_id,))
    elif include_public:
        cursor.execute("""
            SELECT id, owner_id, name, visibility, category, updated_at
            FROM message_builds
            WHERE guild_id = ?
              AND (owner_id = ? OR visibility = 'public')
            ORDER BY updated_at DESC
        """, (guild_id, owner_id))
    else:
        cursor.execute("""
            SELECT id, owner_id, name, visibility, category, updated_at
            FROM message_builds
            WHERE guild_id = ? AND owner_id = ?
            ORDER BY updated_at DESC
        """, (guild_id, owner_id))

    rows = cursor.fetchall()
    connection.close()
    return rows


def update_message_build(
    build_id,
    name,
    content,
    embeds_json,
    buttons_json,
    visibility,
    category,
    allowed_role_ids_json,
    visibility_levels_json,
    interactive_json,
):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE message_builds
        SET name = ?, content = ?, embeds_json = ?, buttons_json = ?,
            visibility = ?, category = ?, allowed_role_ids_json = ?,
            visibility_levels_json = ?, interactive_json = ?, updated_at = ?
        WHERE id = ?
    """, (
        name, content, embeds_json, buttons_json, visibility, category,
        allowed_role_ids_json, visibility_levels_json, interactive_json,
        _now(), build_id,
    ))
    connection.commit()
    changed = cursor.rowcount > 0
    connection.close()
    return changed


def delete_message_build(build_id, owner_id=None):
    """owner_id=None — удалить независимо от владельца (проверка прав — в вызывающем коде)."""
    connection = get_connection()
    cursor = connection.cursor()
    if owner_id is None:
        cursor.execute("DELETE FROM message_builds WHERE id = ?", (build_id,))
    else:
        cursor.execute("""
            DELETE FROM message_builds
            WHERE id = ? AND owner_id = ?
        """, (build_id, owner_id))
    changed = cursor.rowcount > 0
    if changed:
        cursor.execute("DELETE FROM sent_instances WHERE build_id = ?", (build_id,))
        cursor.execute("DELETE FROM build_versions WHERE build_id = ?", (build_id,))
        cursor.execute("DELETE FROM build_schedules WHERE build_id = ?", (build_id,))
        cursor.execute("DELETE FROM build_triggers WHERE build_id = ?", (build_id,))
    connection.commit()
    connection.close()
    return changed


# =========================
# BUILD VERSIONS
# =========================

BUILD_VERSIONS_KEPT = 25
# Поля build'а, которые попадают в снимок (в порядке аргументов update_message_build).
BUILD_SNAPSHOT_FIELDS = (
    "name", "content", "embeds_json", "buttons_json", "visibility", "category",
    "allowed_role_ids_json", "visibility_levels_json", "interactive_json",
)


def build_snapshot(row):
    """Строка get_message_build -> словарь полей для update_message_build."""
    return dict(zip(BUILD_SNAPSHOT_FIELDS, row[3:12]))


def save_build_version(build_id, user_id):
    """Запомнить текущее состояние build'а (до правки). Хранятся последние BUILD_VERSIONS_KEPT."""
    row = get_message_build(build_id)
    if not row:
        return None
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO build_versions (build_id, user_id, snapshot_json, created_at)
        VALUES (?, ?, ?, ?)
    """, (build_id, user_id, json.dumps(build_snapshot(row), ensure_ascii=False), _now()))
    version_id = cursor.lastrowid
    cursor.execute("""
        DELETE FROM build_versions WHERE build_id = ? AND id NOT IN (
            SELECT id FROM build_versions WHERE build_id = ? ORDER BY id DESC LIMIT ?
        )
    """, (build_id, build_id, BUILD_VERSIONS_KEPT))
    connection.commit()
    connection.close()
    return version_id


def get_build_versions(build_id):
    """-> [(id, user_id, created_at)], новые первыми."""
    connection = get_connection()
    rows = connection.execute("""
        SELECT id, user_id, created_at FROM build_versions WHERE build_id = ? ORDER BY id DESC
    """, (build_id,)).fetchall()
    connection.close()
    return rows


def get_build_version(version_id):
    """-> (id, build_id, user_id, snapshot dict, created_at) или None."""
    connection = get_connection()
    row = connection.execute("""
        SELECT id, build_id, user_id, snapshot_json, created_at FROM build_versions WHERE id = ?
    """, (version_id,)).fetchone()
    connection.close()
    if not row:
        return None
    return row[0], row[1], row[2], _json_value(row[3], {}), row[4]


# =========================
# SENT INSTANCES (living Message Build)
# =========================

def save_sent_instance(build_id, message_id, channel_id, guild_id, part_index=None):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO sent_instances (build_id, message_id, channel_id, guild_id, sent_at, part_index)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (build_id, message_id, channel_id, guild_id, _now(), part_index))
    connection.commit()
    connection.close()


def get_sent_instances(build_id):
    connection = get_connection()
    rows = connection.execute("""
        SELECT message_id, channel_id, guild_id, sent_at, part_index
        FROM sent_instances
        WHERE build_id = ?
        ORDER BY id ASC
    """, (build_id,)).fetchall()
    connection.close()
    return rows


def delete_sent_instance(message_id):
    connection = get_connection()
    connection.execute("DELETE FROM sent_instances WHERE message_id = ?", (message_id,))
    connection.commit()
    connection.close()


# =========================
# BUTTON SETS
# =========================

def save_button_set(
    guild_id,
    owner_id,
    name,
    buttons_json,
    visibility="private",
    category="general",
):
    now = int(time.time())
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO button_sets
        (guild_id, owner_id, name, buttons_json, visibility, category,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        guild_id, owner_id, name, buttons_json, visibility, category,
        now, now
    ))

    set_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return set_id


def get_button_set(set_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT id, guild_id, owner_id, name, buttons_json,
               visibility, category, created_at, updated_at
        FROM button_sets
        WHERE id = ?
    """, (set_id,))
    result = cursor.fetchone()
    connection.close()
    return result


def get_button_sets(guild_id, owner_id=None, include_public=True):
    connection = get_connection()
    cursor = connection.cursor()

    if owner_id is None:
        cursor.execute("""
            SELECT id, owner_id, name, visibility, category, updated_at
            FROM button_sets
            WHERE guild_id = ?
            ORDER BY updated_at DESC
        """, (guild_id,))
    elif include_public:
        cursor.execute("""
            SELECT id, owner_id, name, visibility, category, updated_at
            FROM button_sets
            WHERE guild_id = ?
              AND (owner_id = ? OR visibility = 'public')
            ORDER BY updated_at DESC
        """, (guild_id, owner_id))
    else:
        cursor.execute("""
            SELECT id, owner_id, name, visibility, category, updated_at
            FROM button_sets
            WHERE guild_id = ? AND owner_id = ?
            ORDER BY updated_at DESC
        """, (guild_id, owner_id))

    rows = cursor.fetchall()
    connection.close()
    return rows


def delete_button_set(set_id, owner_id=None):
    connection = get_connection()
    cursor = connection.cursor()
    if owner_id is None:
        cursor.execute("DELETE FROM button_sets WHERE id = ?", (set_id,))
    else:
        cursor.execute("""
            DELETE FROM button_sets
            WHERE id = ? AND owner_id = ?
        """, (set_id, owner_id))
    connection.commit()
    changed = cursor.rowcount > 0
    connection.close()
    return changed


# =========================
# BOT SETTINGS (бэкенд под /design)
# =========================

def get_setting(guild_id, key):
    connection = get_connection()
    row = connection.execute("""
        SELECT value FROM bot_settings WHERE guild_id = ? AND key = ?
    """, (guild_id or 0, key)).fetchone()
    connection.close()
    return row[0] if row else None


def get_all_settings(guild_id):
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT key, value FROM bot_settings WHERE guild_id = ?
        """, (guild_id or 0,)).fetchall()
    except sqlite3.OperationalError:
        # таблицы ещё нет — init_database() не успел отработать
        rows = []
    connection.close()
    return {key: value for key, value in rows if value is not None}


def delete_setting(guild_id, key):
    connection = get_connection()
    connection.execute("""
        DELETE FROM bot_settings WHERE guild_id = ? AND key = ?
    """, (guild_id or 0, key))
    connection.commit()
    connection.close()


def set_setting(guild_id, key, value):
    connection = get_connection()
    connection.execute("""
        INSERT INTO bot_settings (guild_id, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value
    """, (guild_id or 0, key, str(value)))
    connection.commit()
    connection.close()


# =========================
# ACTION REGISTRY
# =========================

# строка реестра: (action_key, min_level, dangerous, description, enabled, use_level)

def upsert_action(action_key, min_level, dangerous, description, enabled=1, use_level="member"):
    connection = get_connection()
    connection.execute("""
        INSERT INTO action_registry (action_key, min_level, dangerous, description, enabled, use_level)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(action_key) DO UPDATE SET
            min_level = excluded.min_level,
            dangerous = excluded.dangerous,
            description = excluded.description,
            enabled = excluded.enabled,
            use_level = excluded.use_level
    """, (action_key, min_level, int(dangerous), description, int(enabled), use_level))
    connection.commit()
    connection.close()


def get_action(action_key):
    connection = get_connection()
    row = connection.execute("""
        SELECT action_key, min_level, dangerous, description, enabled, use_level
        FROM action_registry WHERE action_key = ?
    """, (action_key,)).fetchone()
    connection.close()
    return row


def get_actions(enabled_only=False):
    connection = get_connection()
    rows = connection.execute("""
        SELECT action_key, min_level, dangerous, description, enabled, use_level
        FROM action_registry
    """ + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY action_key").fetchall()
    connection.close()
    return rows


def set_action_enabled(action_key, enabled):
    connection = get_connection()
    connection.execute("""
        UPDATE action_registry SET enabled = ? WHERE action_key = ?
    """, (int(enabled), action_key))
    connection.commit()
    connection.close()


def set_action_min_level(action_key, min_level):
    connection = get_connection()
    connection.execute("""
        UPDATE action_registry SET min_level = ? WHERE action_key = ?
    """, (min_level, action_key))
    connection.commit()
    connection.close()


def set_action_use_level(action_key, use_level):
    connection = get_connection()
    connection.execute("""
        UPDATE action_registry SET use_level = ? WHERE action_key = ?
    """, (use_level, action_key))
    connection.commit()
    connection.close()


def set_action_dangerous(action_key, dangerous):
    connection = get_connection()
    connection.execute("""
        UPDATE action_registry SET dangerous = ? WHERE action_key = ?
    """, (int(dangerous), action_key))
    connection.commit()
    connection.close()


# =========================
# EXTENDED MODULE STORAGE
# =========================

def _ensure_extended_tables():
    connection = get_connection()
    c = connection.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS forms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        name TEXT NOT NULL, description TEXT, questions_json TEXT NOT NULL,
        visibility TEXT NOT NULL DEFAULT 'private', category TEXT NOT NULL DEFAULT 'general',
        allowed_role_ids_json TEXT NOT NULL DEFAULT '[]', destination_channel_id INTEGER,
        reviewer_role_ids_json TEXT NOT NULL DEFAULT '[]', reviewer_user_ids_json TEXT NOT NULL DEFAULT '[]',
        post_action TEXT NOT NULL DEFAULT 'review', post_role_id INTEGER, target_role_id INTEGER,
        form_type TEXT NOT NULL DEFAULT 'custom', dm_creator INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS form_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, form_id INTEGER NOT NULL,
        guild_id INTEGER NOT NULL, applicant_id INTEGER NOT NULL,
        answers_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'pending',
        reviewer_id INTEGER, review_reason TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        name TEXT NOT NULL, template_type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
        visibility TEXT NOT NULL DEFAULT 'private', category TEXT NOT NULL DEFAULT 'general',
        allowed_role_ids_json TEXT NOT NULL DEFAULT '[]', logo_url TEXT, is_favorite INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    c.execute("PRAGMA table_info(templates)")
    tpl_columns = {row[1] for row in c.fetchall()}
    legacy_template_columns = tpl_columns.copy()
    if "owner_id" not in tpl_columns:
        c.execute("ALTER TABLE templates ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 0")
    if "template_type" not in tpl_columns:
        c.execute("ALTER TABLE templates ADD COLUMN template_type TEXT NOT NULL DEFAULT 'message'")
    if "payload_json" not in tpl_columns:
        c.execute("ALTER TABLE templates ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
    if "allowed_role_ids_json" not in tpl_columns:
        c.execute("ALTER TABLE templates ADD COLUMN allowed_role_ids_json TEXT NOT NULL DEFAULT '[]'")
    if "logo_url" not in tpl_columns:
        c.execute("ALTER TABLE templates ADD COLUMN logo_url TEXT")
    if "is_favorite" not in tpl_columns:
        c.execute("ALTER TABLE templates ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
    if "created_by" in legacy_template_columns:
        legacy_rows = c.execute(
            "SELECT id, created_by, content, embeds_json, buttons_json FROM templates WHERE owner_id=0"
        ).fetchall()
        for template_id, owner_id, content, embeds_json, buttons_json in legacy_rows:
            payload = {
                "content": content or "",
                "embeds": _json_value(embeds_json, []),
                "buttons": _json_value(buttons_json, []),
            }
            c.execute(
                "UPDATE templates SET owner_id=?, payload_json=? WHERE id=?",
                (owner_id or 0, json.dumps(payload, ensure_ascii=False), template_id),
            )
    c.execute("""CREATE TABLE IF NOT EXISTS webhooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        webhook_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, name TEXT NOT NULL,
        url TEXT NOT NULL, avatar_url TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS webhook_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        webhook_id INTEGER NOT NULL, action TEXT NOT NULL, channel_id INTEGER, details TEXT, created_at INTEGER NOT NULL
    )""")
    c.execute("PRAGMA table_info(forms)")
    form_columns = {row[1] for row in c.fetchall()}
    if "target_role_id" not in form_columns:
        c.execute("ALTER TABLE forms ADD COLUMN target_role_id INTEGER")
    if "form_type" not in form_columns:
        c.execute("ALTER TABLE forms ADD COLUMN form_type TEXT NOT NULL DEFAULT 'custom'")
    if "dm_creator" not in form_columns:
        c.execute("ALTER TABLE forms ADD COLUMN dm_creator INTEGER NOT NULL DEFAULT 0")
    c.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
        action TEXT NOT NULL, target_type TEXT, target_id TEXT, details TEXT, created_at INTEGER NOT NULL
    )""")
    # Избранное — у каждого своё. Раньше это был один флаг на шаблон:
    # любой, кто видел шаблон, переключал "избранное" для всех.
    c.execute("""CREATE TABLE IF NOT EXISTS template_favorites (
        user_id INTEGER NOT NULL, template_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, template_id)
    )""")
    c.execute("""INSERT OR IGNORE INTO template_favorites (user_id, template_id, created_at)
        SELECT owner_id, id, ? FROM templates WHERE is_favorite = 1""", (_now(),))
    c.execute("UPDATE templates SET is_favorite = 0 WHERE is_favorite = 1")
    # Меню ролей (self-roles): участник сам выбирает роли из заданного списка.
    c.execute("""CREATE TABLE IF NOT EXISTS role_menus (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        name TEXT NOT NULL, placeholder TEXT, roles_json TEXT NOT NULL DEFAULT '[]',
        max_values INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    # История Message Build: снимок перед каждым сохранением — можно откатить.
    c.execute("""CREATE TABLE IF NOT EXISTS build_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, build_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL, created_at INTEGER NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_build_versions_build ON build_versions (build_id, id)")
    # Расписание: build уходит в канал/ветку/форум в заданное время (и повторяется).
    c.execute("""CREATE TABLE IF NOT EXISTS build_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, build_id INTEGER NOT NULL,
        owner_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, next_run INTEGER NOT NULL,
        interval_minutes INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
        last_run INTEGER, last_error TEXT, created_at INTEGER NOT NULL
    )""")
    # Триггеры: событие на сервере -> build уходит туда, где событие случилось (или в заданный канал).
    c.execute("""CREATE TABLE IF NOT EXISTS build_triggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, build_id INTEGER NOT NULL,
        owner_id INTEGER NOT NULL, event TEXT NOT NULL, pattern TEXT,
        watch_channel_id INTEGER, target_channel_id INTEGER,
        cooldown_seconds INTEGER NOT NULL DEFAULT 30, enabled INTEGER NOT NULL DEFAULT 1,
        last_fired INTEGER, last_error TEXT, created_at INTEGER NOT NULL
    )""")
    # Счётчики для {counter:имя}: меняются кнопками, обновляют связанные build'ы.
    c.execute("""CREATE TABLE IF NOT EXISTS counters (
        guild_id INTEGER NOT NULL, name TEXT NOT NULL, value INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL, PRIMARY KEY (guild_id, name)
    )""")
    # Лого-генератор: стили (референсы + промпт + модель/LoRA) и история генераций.
    c.execute("""CREATE TABLE IF NOT EXISTS logo_styles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        name TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '', negative_prompt TEXT NOT NULL DEFAULT '',
        model TEXT, lora TEXT, strength REAL NOT NULL DEFAULT 0.65,
        references_json TEXT NOT NULL DEFAULT '[]', is_default INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS logo_generations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        style_id INTEGER, mode TEXT NOT NULL, prompt TEXT, file_path TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    connection.commit(); connection.close()


def save_form(guild_id, owner_id, name, description, questions_json, visibility='private', category='general',
              allowed_role_ids_json='[]', destination_channel_id=None, reviewer_role_ids_json='[]',
              reviewer_user_ids_json='[]', post_action='review', post_role_id=None, target_role_id=None,
              form_type='custom', dm_creator=0):
    _ensure_extended_tables(); now=_now(); connection=get_connection(); c=connection.cursor()
    c.execute("""INSERT INTO forms (guild_id,owner_id,name,description,questions_json,visibility,category,
        allowed_role_ids_json,destination_channel_id,reviewer_role_ids_json,reviewer_user_ids_json,post_action,
        post_role_id,target_role_id,form_type,dm_creator,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (guild_id,owner_id,name,description,questions_json,visibility,category,
        allowed_role_ids_json,destination_channel_id,reviewer_role_ids_json,reviewer_user_ids_json,post_action,post_role_id,
        target_role_id,form_type,int(dm_creator),now,now))
    rid=c.lastrowid; connection.commit(); connection.close(); return rid


def get_form(form_id):
    _ensure_extended_tables(); connection=get_connection(); row=connection.execute("""SELECT id,guild_id,owner_id,name,description,questions_json,visibility,category,
        allowed_role_ids_json,destination_channel_id,reviewer_role_ids_json,reviewer_user_ids_json,post_action,post_role_id,target_role_id,
        form_type,dm_creator,created_at,updated_at FROM forms WHERE id=?""",(form_id,)).fetchone(); connection.close(); return row


def get_forms(guild_id, owner_id=None, include_public=True):
    _ensure_extended_tables(); connection=get_connection();
    if owner_id is None:
        rows=connection.execute("SELECT id,owner_id,name,visibility,category,allowed_role_ids_json,updated_at FROM forms WHERE guild_id=? ORDER BY updated_at DESC",(guild_id,)).fetchall()
    elif include_public:
        rows=connection.execute("SELECT id,owner_id,name,visibility,category,allowed_role_ids_json,updated_at FROM forms WHERE guild_id=? AND (owner_id=? OR visibility='public') ORDER BY updated_at DESC",(guild_id,owner_id)).fetchall()
    else:
        rows=connection.execute("SELECT id,owner_id,name,visibility,category,allowed_role_ids_json,updated_at FROM forms WHERE guild_id=? AND owner_id=? ORDER BY updated_at DESC",(guild_id,owner_id)).fetchall()
    connection.close(); return rows


def save_submission(form_id,guild_id,applicant_id,answers_json):
    _ensure_extended_tables(); connection=get_connection(); c=connection.cursor(); now=_now()
    c.execute("INSERT INTO form_submissions (form_id,guild_id,applicant_id,answers_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",(form_id,guild_id,applicant_id,answers_json,'pending',now,now))
    rid=c.lastrowid; connection.commit(); connection.close(); return rid


def get_submission(submission_id):
    _ensure_extended_tables(); connection=get_connection(); row=connection.execute("SELECT id,form_id,guild_id,applicant_id,answers_json,status,reviewer_id,review_reason,created_at,updated_at FROM form_submissions WHERE id=?",(submission_id,)).fetchone(); connection.close(); return row


def review_submission(submission_id, reviewer_id, status, reason=''):
    _ensure_extended_tables(); connection=get_connection(); connection.execute("UPDATE form_submissions SET status=?,reviewer_id=?,review_reason=?,updated_at=? WHERE id=?",(status,reviewer_id,reason,_now(),submission_id)); connection.commit(); connection.close()


def save_template(guild_id,owner_id,name,template_type,payload_json,visibility='private',category='general',allowed_role_ids_json='[]',logo_url=None):
    _ensure_extended_tables(); now=_now(); connection=get_connection(); c=connection.cursor()
    c.execute("PRAGMA table_info(templates)")
    columns = {row[1] for row in c.fetchall()}
    if "created_by" in columns:
        c.execute("INSERT INTO templates (guild_id,owner_id,created_by,name,template_type,payload_json,visibility,category,allowed_role_ids_json,logo_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(guild_id,owner_id,owner_id,name,template_type,payload_json,visibility,category,allowed_role_ids_json,logo_url,now,now))
    else:
        c.execute("INSERT INTO templates (guild_id,owner_id,name,template_type,payload_json,visibility,category,allowed_role_ids_json,logo_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(guild_id,owner_id,name,template_type,payload_json,visibility,category,allowed_role_ids_json,logo_url,now,now))
    rid=c.lastrowid; connection.commit(); connection.close(); return rid


def get_template(template_id):
    _ensure_extended_tables(); connection=get_connection(); row=connection.execute("SELECT id,guild_id,owner_id,name,template_type,payload_json,visibility,category,allowed_role_ids_json,logo_url,is_favorite,created_at,updated_at FROM templates WHERE id=?",(template_id,)).fetchone(); connection.close(); return row


def get_templates(guild_id,owner_id=None,template_type=None,include_public=True,favorites_only=False,working_role_ids=None):
    _ensure_extended_tables(); connection=get_connection(); clauses=['guild_id=?']; params=[guild_id]
    if template_type: clauses.append('template_type=?'); params.append(template_type)
    if favorites_only: clauses.append('is_favorite=1')
    if owner_id is not None:
        if include_public: clauses.append("(owner_id=? OR visibility='public')")
        else: clauses.append('owner_id=?')
        params.append(owner_id)
    rows=connection.execute("SELECT id,owner_id,name,template_type,payload_json,visibility,category,allowed_role_ids_json,logo_url,is_favorite,updated_at FROM templates WHERE "+' AND '.join(clauses)+' ORDER BY updated_at DESC',params).fetchall()
    connection.close()
    if working_role_ids:
        role_set = set(working_role_ids)
        import json as _json
        rows = [r for r in rows if role_set & set(_json.loads(r[7] or '[]'))]
    return rows


def set_template_favorite(template_id, is_favorite):
    _ensure_extended_tables(); connection=get_connection(); connection.execute("UPDATE templates SET is_favorite=? WHERE id=?",(int(is_favorite),template_id)); connection.commit(); connection.close()


def delete_template(template_id,owner_id):
    _ensure_extended_tables(); connection=get_connection(); c=connection.cursor(); c.execute('DELETE FROM templates WHERE id=? AND owner_id=?',(template_id,owner_id)); changed=c.rowcount>0; connection.commit(); connection.close(); return changed


def save_webhook(guild_id,owner_id,webhook_id,channel_id,name,url,avatar_url=None):
    _ensure_extended_tables(); now=_now(); connection=get_connection(); c=connection.cursor(); c.execute('INSERT INTO webhooks (guild_id,owner_id,webhook_id,channel_id,name,url,avatar_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',(guild_id,owner_id,webhook_id,channel_id,name,url,avatar_url,now,now)); rid=c.lastrowid; connection.commit(); connection.close(); return rid


def get_webhook(record_id):
    _ensure_extended_tables(); connection=get_connection(); row=connection.execute('SELECT id,guild_id,owner_id,webhook_id,channel_id,name,url,avatar_url,created_at,updated_at FROM webhooks WHERE id=?',(record_id,)).fetchone(); connection.close(); return row


def get_webhooks(guild_id,owner_id=None):
    _ensure_extended_tables(); connection=get_connection();
    if owner_id is None: rows=connection.execute('SELECT id,owner_id,webhook_id,channel_id,name,avatar_url,created_at,updated_at FROM webhooks WHERE guild_id=? ORDER BY updated_at DESC',(guild_id,)).fetchall()
    else: rows=connection.execute('SELECT id,owner_id,webhook_id,channel_id,name,avatar_url,created_at,updated_at FROM webhooks WHERE guild_id=? AND owner_id=? ORDER BY updated_at DESC',(guild_id,owner_id)).fetchall()
    connection.close(); return rows


def update_webhook_record(record_id,name=None,channel_id=None,url=None,avatar_url=None,webhook_id=None):
    row=get_webhook(record_id)
    if not row: return False
    vals=(webhook_id if webhook_id is not None else row[3],name if name is not None else row[5],channel_id if channel_id is not None else row[4],url if url is not None else row[6],avatar_url if avatar_url is not None else row[7],_now(),record_id)
    connection=get_connection(); connection.execute('UPDATE webhooks SET webhook_id=?,name=?,channel_id=?,url=?,avatar_url=?,updated_at=? WHERE id=?',vals); connection.commit(); connection.close(); return True


def delete_webhook_record(record_id):
    _ensure_extended_tables(); connection=get_connection(); c=connection.cursor(); c.execute('DELETE FROM webhooks WHERE id=?',(record_id,)); changed=c.rowcount>0; connection.commit(); connection.close(); return changed


def log_webhook_event(guild_id,owner_id,webhook_id,action,channel_id=None,details=None):
    _ensure_extended_tables(); connection=get_connection(); connection.execute('INSERT INTO webhook_history (guild_id,owner_id,webhook_id,action,channel_id,details,created_at) VALUES (?,?,?,?,?,?,?)',(guild_id,owner_id,webhook_id,action,channel_id,details,_now())); connection.commit(); connection.close()


def get_webhook_history(guild_id,webhook_id=None,limit=20):
    _ensure_extended_tables(); connection=get_connection();
    if webhook_id is None: rows=connection.execute('SELECT owner_id,webhook_id,action,channel_id,details,created_at FROM webhook_history WHERE guild_id=? ORDER BY id DESC LIMIT ?',(guild_id,limit)).fetchall()
    else: rows=connection.execute('SELECT owner_id,webhook_id,action,channel_id,details,created_at FROM webhook_history WHERE guild_id=? AND webhook_id=? ORDER BY id DESC LIMIT ?',(guild_id,webhook_id,limit)).fetchall()
    connection.close(); return rows


def log_audit(guild_id, actor_id, action, target_type=None, target_id=None, details=None):
    _ensure_extended_tables()
    connection = get_connection()
    connection.execute("""
        INSERT INTO audit_logs
        (guild_id, actor_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (guild_id, actor_id, action, target_type, str(target_id) if target_id is not None else None,
           details, _now()))
    connection.commit()
    connection.close()


def get_audit_logs(guild_id, limit=50):
    _ensure_extended_tables()
    connection = get_connection()
    rows = connection.execute("""
        SELECT actor_id, action, target_type, target_id, details, created_at
        FROM audit_logs
        WHERE guild_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (guild_id, limit)).fetchall()
    connection.close()
    return rows


# =========================
# FORMS: удаление и защита от дублей
# =========================

def delete_form(form_id):
    _ensure_extended_tables(); connection = get_connection(); c = connection.cursor()
    c.execute("DELETE FROM forms WHERE id=?", (form_id,)); changed = c.rowcount > 0
    connection.commit(); connection.close(); return changed


def get_pending_submission(form_id, applicant_id):
    _ensure_extended_tables(); connection = get_connection()
    row = connection.execute(
        "SELECT id FROM form_submissions WHERE form_id=? AND applicant_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (form_id, applicant_id),
    ).fetchone()
    connection.close(); return row[0] if row else None


# =========================
# TEMPLATE FAVORITES (у каждого пользователя свои)
# =========================

def is_template_favorite(user_id, template_id):
    _ensure_extended_tables(); connection = get_connection()
    row = connection.execute("SELECT 1 FROM template_favorites WHERE user_id=? AND template_id=?", (user_id, template_id)).fetchone()
    connection.close(); return row is not None


def set_user_template_favorite(user_id, template_id, is_favorite):
    _ensure_extended_tables(); connection = get_connection()
    if is_favorite:
        connection.execute("INSERT OR IGNORE INTO template_favorites (user_id, template_id, created_at) VALUES (?,?,?)", (user_id, template_id, _now()))
    else:
        connection.execute("DELETE FROM template_favorites WHERE user_id=? AND template_id=?", (user_id, template_id))
    connection.commit(); connection.close()


def get_user_favorite_template_ids(user_id):
    _ensure_extended_tables(); connection = get_connection()
    rows = connection.execute("SELECT template_id FROM template_favorites WHERE user_id=?", (user_id,)).fetchall()
    connection.close(); return {row[0] for row in rows}


# =========================
# ROLE MENUS
# =========================

def save_role_menu(guild_id, owner_id, name, placeholder, roles_json, max_values):
    _ensure_extended_tables(); now = _now(); connection = get_connection(); c = connection.cursor()
    c.execute("""INSERT INTO role_menus (guild_id, owner_id, name, placeholder, roles_json, max_values, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)""", (guild_id, owner_id, name, placeholder, roles_json, max_values, now, now))
    rid = c.lastrowid; connection.commit(); connection.close(); return rid


def get_role_menu(menu_id):
    _ensure_extended_tables(); connection = get_connection()
    row = connection.execute("""SELECT id, guild_id, owner_id, name, placeholder, roles_json, max_values, created_at, updated_at
        FROM role_menus WHERE id=?""", (menu_id,)).fetchone()
    connection.close(); return row


def get_role_menus(guild_id):
    _ensure_extended_tables(); connection = get_connection()
    rows = connection.execute("""SELECT id, owner_id, name, roles_json, max_values, updated_at
        FROM role_menus WHERE guild_id=? ORDER BY updated_at DESC""", (guild_id,)).fetchall()
    connection.close(); return rows


def delete_role_menu(menu_id):
    _ensure_extended_tables(); connection = get_connection(); c = connection.cursor()
    c.execute("DELETE FROM role_menus WHERE id=?", (menu_id,)); changed = c.rowcount > 0
    connection.commit(); connection.close(); return changed


# =========================
# LOGO STYLES / GENERATIONS
# =========================
# строка стиля: (id, guild_id, owner_id, name, prompt, negative_prompt, model, lora,
#                strength, references_json, is_default, created_at, updated_at)

_LOGO_STYLE_COLUMNS = ("id, guild_id, owner_id, name, prompt, negative_prompt, model, lora, "
                       "strength, references_json, is_default, created_at, updated_at")


def save_logo_style(guild_id, owner_id, name, prompt="", negative_prompt="", model=None, lora=None, strength=0.65):
    _ensure_extended_tables(); now = _now(); connection = get_connection(); c = connection.cursor()
    c.execute("""INSERT INTO logo_styles (guild_id, owner_id, name, prompt, negative_prompt, model, lora, strength,
        references_json, is_default, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'[]',0,?,?)""",
        (guild_id, owner_id, name, prompt, negative_prompt, model, lora, strength, now, now))
    rid = c.lastrowid; connection.commit(); connection.close(); return rid


def update_logo_style(style_id, **fields):
    allowed = {"name", "prompt", "negative_prompt", "model", "lora", "strength", "references_json"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    _ensure_extended_tables(); connection = get_connection(); c = connection.cursor()
    assignments = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE logo_styles SET {assignments}, updated_at=? WHERE id=?", (*fields.values(), _now(), style_id))
    changed = c.rowcount > 0; connection.commit(); connection.close(); return changed


def set_default_logo_style(guild_id, style_id):
    _ensure_extended_tables(); connection = get_connection()
    connection.execute("UPDATE logo_styles SET is_default = (id = ?) WHERE guild_id = ?", (style_id, guild_id))
    connection.commit(); connection.close()


def get_logo_style(style_id):
    _ensure_extended_tables(); connection = get_connection()
    row = connection.execute(f"SELECT {_LOGO_STYLE_COLUMNS} FROM logo_styles WHERE id=?", (style_id,)).fetchone()
    connection.close(); return row


def get_logo_styles(guild_id):
    _ensure_extended_tables(); connection = get_connection()
    rows = connection.execute(f"SELECT {_LOGO_STYLE_COLUMNS} FROM logo_styles WHERE guild_id=? ORDER BY is_default DESC, name",
                              (guild_id,)).fetchall()
    connection.close(); return rows


def delete_logo_style(style_id):
    _ensure_extended_tables(); connection = get_connection(); c = connection.cursor()
    c.execute("DELETE FROM logo_styles WHERE id=?", (style_id,)); changed = c.rowcount > 0
    connection.commit(); connection.close(); return changed


def save_logo_generation(guild_id, user_id, style_id, mode, prompt, file_path):
    _ensure_extended_tables(); connection = get_connection(); c = connection.cursor()
    c.execute("""INSERT INTO logo_generations (guild_id, user_id, style_id, mode, prompt, file_path, created_at)
        VALUES (?,?,?,?,?,?,?)""", (guild_id, user_id, style_id, mode, prompt, file_path, _now()))
    rid = c.lastrowid; connection.commit(); connection.close(); return rid


def get_logo_generation(generation_id):
    _ensure_extended_tables(); connection = get_connection()
    row = connection.execute("""SELECT id, guild_id, user_id, style_id, mode, prompt, file_path, created_at
        FROM logo_generations WHERE id=?""", (generation_id,)).fetchone()
    connection.close(); return row


def count_recent_logo_generations(user_id, since):
    _ensure_extended_tables(); connection = get_connection()
    row = connection.execute("SELECT COUNT(*) FROM logo_generations WHERE user_id=? AND created_at>=?", (user_id, since)).fetchone()
    connection.close(); return row[0]


# =========================
# BUILD SETTINGS
# =========================

def get_build_settings(build_id):
    connection = get_connection()
    row = connection.execute("SELECT settings_json FROM message_builds WHERE id = ?", (build_id,)).fetchone()
    connection.close()
    value = _json_value(row[0], {}) if row else {}
    return value if isinstance(value, dict) else {}


def set_build_settings(build_id, settings):
    connection = get_connection()
    connection.execute(
        "UPDATE message_builds SET settings_json = ? WHERE id = ?",
        (json.dumps(settings, ensure_ascii=False), build_id),
    )
    connection.commit()
    connection.close()


def get_builds_with_settings():
    """-> [(id, guild_id, settings dict)] у кого настройки не пустые."""
    connection = get_connection()
    rows = connection.execute(
        "SELECT id, guild_id, settings_json FROM message_builds WHERE settings_json NOT IN ('{}', '', 'null')"
    ).fetchall()
    connection.close()
    result = []
    for build_id, guild_id, raw in rows:
        value = _json_value(raw, {})
        if isinstance(value, dict) and value:
            result.append((build_id, guild_id, value))
    return result


def get_child_builds(parent_id):
    return [bid for bid, _, settings in get_builds_with_settings() if settings.get("parent_id") == parent_id]


# =========================
# SCHEDULES
# =========================

_SCHEDULE_COLUMNS = "id, guild_id, build_id, owner_id, channel_id, next_run, interval_minutes, enabled, last_run, last_error"


def add_schedule(guild_id, build_id, owner_id, channel_id, next_run, interval_minutes=0):
    _ensure_extended_tables()
    connection = get_connection()
    cursor = connection.execute("""
        INSERT INTO build_schedules (guild_id, build_id, owner_id, channel_id, next_run, interval_minutes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (guild_id, build_id, owner_id, channel_id, next_run, interval_minutes, _now()))
    schedule_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return schedule_id


def get_schedules(build_id=None, guild_id=None):
    _ensure_extended_tables()
    connection = get_connection()
    if build_id is not None:
        rows = connection.execute(f"SELECT {_SCHEDULE_COLUMNS} FROM build_schedules WHERE build_id = ? ORDER BY next_run", (build_id,)).fetchall()
    else:
        rows = connection.execute(f"SELECT {_SCHEDULE_COLUMNS} FROM build_schedules WHERE guild_id = ? ORDER BY next_run", (guild_id,)).fetchall()
    connection.close()
    return rows


def get_schedule(schedule_id):
    _ensure_extended_tables()
    connection = get_connection()
    row = connection.execute(f"SELECT {_SCHEDULE_COLUMNS} FROM build_schedules WHERE id = ?", (schedule_id,)).fetchone()
    connection.close()
    return row


def get_due_schedules(now):
    _ensure_extended_tables()
    connection = get_connection()
    rows = connection.execute(
        f"SELECT {_SCHEDULE_COLUMNS} FROM build_schedules WHERE enabled = 1 AND next_run <= ? ORDER BY next_run", (now,)
    ).fetchall()
    connection.close()
    return rows


def update_schedule(schedule_id, **fields):
    allowed = {"next_run", "interval_minutes", "enabled", "last_run", "last_error", "channel_id"}
    keys = [key for key in fields if key in allowed]
    if not keys:
        return
    connection = get_connection()
    connection.execute(
        f"UPDATE build_schedules SET {', '.join(f'{key} = ?' for key in keys)} WHERE id = ?",
        (*[fields[key] for key in keys], schedule_id),
    )
    connection.commit()
    connection.close()


def delete_schedule(schedule_id):
    connection = get_connection()
    connection.execute("DELETE FROM build_schedules WHERE id = ?", (schedule_id,))
    connection.commit()
    connection.close()


# =========================
# TRIGGERS
# =========================

_TRIGGER_COLUMNS = ("id, guild_id, build_id, owner_id, event, pattern, watch_channel_id, target_channel_id, "
                    "cooldown_seconds, enabled, last_fired, last_error")


def add_trigger(guild_id, build_id, owner_id, event, pattern=None, watch_channel_id=None,
                target_channel_id=None, cooldown_seconds=30):
    _ensure_extended_tables()
    connection = get_connection()
    cursor = connection.execute("""
        INSERT INTO build_triggers (guild_id, build_id, owner_id, event, pattern, watch_channel_id,
            target_channel_id, cooldown_seconds, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (guild_id, build_id, owner_id, event, pattern, watch_channel_id, target_channel_id, cooldown_seconds, _now()))
    trigger_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return trigger_id


def get_triggers(build_id=None, guild_id=None, event=None):
    _ensure_extended_tables()
    connection = get_connection()
    if build_id is not None:
        rows = connection.execute(f"SELECT {_TRIGGER_COLUMNS} FROM build_triggers WHERE build_id = ? ORDER BY id", (build_id,)).fetchall()
    else:
        rows = connection.execute(
            f"SELECT {_TRIGGER_COLUMNS} FROM build_triggers WHERE guild_id = ? AND enabled = 1 AND event = ? ORDER BY id",
            (guild_id, event),
        ).fetchall()
    connection.close()
    return rows


def get_trigger(trigger_id):
    _ensure_extended_tables()
    connection = get_connection()
    row = connection.execute(f"SELECT {_TRIGGER_COLUMNS} FROM build_triggers WHERE id = ?", (trigger_id,)).fetchone()
    connection.close()
    return row


def update_trigger(trigger_id, **fields):
    allowed = {"enabled", "last_fired", "last_error"}
    keys = [key for key in fields if key in allowed]
    if not keys:
        return
    connection = get_connection()
    connection.execute(
        f"UPDATE build_triggers SET {', '.join(f'{key} = ?' for key in keys)} WHERE id = ?",
        (*[fields[key] for key in keys], trigger_id),
    )
    connection.commit()
    connection.close()


def delete_trigger(trigger_id):
    connection = get_connection()
    connection.execute("DELETE FROM build_triggers WHERE id = ?", (trigger_id,))
    connection.commit()
    connection.close()


# =========================
# COUNTERS / STATS
# =========================

def get_counters(guild_id):
    _ensure_extended_tables()
    connection = get_connection()
    rows = connection.execute("SELECT name, value FROM counters WHERE guild_id = ?", (guild_id,)).fetchall()
    connection.close()
    return dict(rows)


def change_counter(guild_id, name, delta=0, set_to=None):
    """Изменить счётчик (создаётся с 0). -> новое значение."""
    _ensure_extended_tables()
    connection = get_connection()
    connection.execute(
        "INSERT OR IGNORE INTO counters (guild_id, name, value, updated_at) VALUES (?, ?, 0, ?)",
        (guild_id, name, _now()),
    )
    if set_to is not None:
        connection.execute("UPDATE counters SET value = ?, updated_at = ? WHERE guild_id = ? AND name = ?",
                           (set_to, _now(), guild_id, name))
    else:
        connection.execute("UPDATE counters SET value = value + ?, updated_at = ? WHERE guild_id = ? AND name = ?",
                           (delta, _now(), guild_id, name))
    value = connection.execute("SELECT value FROM counters WHERE guild_id = ? AND name = ?", (guild_id, name)).fetchone()[0]
    connection.commit()
    connection.close()
    return value


def get_submission_stats(guild_id, form_id=None):
    """-> {"total", "pending", "approved", "rejected", "last": (form_name, applicant_id, created_at) | None}"""
    _ensure_extended_tables()
    connection = get_connection()
    where, params = "s.guild_id = ?", [guild_id]
    if form_id is not None:
        where += " AND s.form_id = ?"
        params.append(form_id)
    counts = dict(connection.execute(
        f"SELECT s.status, COUNT(*) FROM form_submissions s WHERE {where} GROUP BY s.status", params
    ).fetchall())
    last = connection.execute(f"""
        SELECT f.name, s.applicant_id, s.created_at FROM form_submissions s
        LEFT JOIN forms f ON f.id = s.form_id WHERE {where} ORDER BY s.id DESC LIMIT 1
    """, params).fetchone()
    connection.close()
    return {
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "last": last,
    }
