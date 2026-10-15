"""
Перерисованные эмодзи: бот помнит, какая картинка залита под каждым именем
(хэш файла), и если PNG в panel_art/emoji поменялся — заменяет эмодзи:
удаляет старый и заливает новый. У нового эмодзи другой ID, поэтому бот
сразу правит сохранённые ссылки «<:имя:старый_id>» во всех таблицах базы
(эмодзи форм в паках, кнопки в Message Build, наборы кнопок…).

scope: "app" — эмодзи приложения (хранилище бота), "guild:<id>" — эмодзи
сервера (перенос через emoji_export).
"""

import hashlib
import logging

from database import get_connection

_log = logging.getLogger(__name__)


def ensure_table():
    connection = get_connection()
    connection.execute("""CREATE TABLE IF NOT EXISTS emoji_hashes (
        scope TEXT NOT NULL, name TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (scope, name)
    )""")
    connection.commit()
    connection.close()


def file_hash(data):
    return hashlib.sha256(data).hexdigest()[:20]


def stored_hash(scope, name):
    ensure_table()
    connection = get_connection()
    row = connection.execute("SELECT hash FROM emoji_hashes WHERE scope = ? AND name = ?", (scope, name)).fetchone()
    connection.close()
    return row[0] if row else None


def remember(scope, name, digest):
    ensure_table()
    connection = get_connection()
    connection.execute("INSERT OR REPLACE INTO emoji_hashes (scope, name, hash) VALUES (?, ?, ?)",
                       (scope, name, digest))
    connection.commit()
    connection.close()


def is_changed(scope, name, data):
    """Картинка под этим именем не та, что заливали (или бот её ещё не запоминал)."""
    return stored_hash(scope, name) != file_hash(data)


def remap_ids(old_id, new_id):
    """«:старый_id>» -> «:новый_id>» во всех текстовых колонках базы. -> сколько строк поправлено."""
    if not old_id or not new_id or old_id == new_id:
        return 0
    old, new = f":{old_id}>", f":{new_id}>"
    changed = 0
    connection = get_connection()
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            for column in connection.execute(f'PRAGMA table_info("{table}")').fetchall():
                name, kind = column[1], (column[2] or "").upper()
                if kind and "TEXT" not in kind and "CHAR" not in kind:
                    continue
                cursor = connection.execute(
                    f'UPDATE "{table}" SET "{name}" = REPLACE("{name}", ?, ?) WHERE "{name}" LIKE ?',
                    (old, new, f"%{old}%"))
                changed += cursor.rowcount
        connection.commit()
    finally:
        connection.close()
    if changed:
        _log.info("emoji %s -> %s: %s rows updated", old_id, new_id, changed)
    return changed
