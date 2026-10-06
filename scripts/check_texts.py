"""
Проверка каталога texts.py против кода.

Запуск из корня проекта:
    python3 scripts/check_texts.py

Ищет в модулях бота:
- t("ключ"), say(x, "ключ"), deny(x, "ключ") — ключ должен быть в каталоге;
- panel_embed(x, "экран") — нужен "экран.title" (если не передан title=);
- классы с texts = "префикс" — подписи кнопок-методов и полей модалок
  (<префикс>.<имя>) и заголовок модалки (<префикс>.title).
Ключи, собранные через f-строку (t(f"level.{x}")), не проверяются.
Код выхода 1, если чего-то не хватает.
"""

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from texts import CATALOG  # noqa: E402

MODULES = ["core.py", "access_module.py", "embed_module.py", "extended_modules.py", "bot.py"]
KEY_ARG = {"t": 0, "say": 1, "deny": 1}


def literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def func_name(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def is_button_decorator(dec):
    target = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(target, ast.Attribute) and target.attr == "button"


def is_text_input(value):
    return isinstance(value, ast.Call) and func_name(value) == "TextInput"


def class_texts_prefix(cls):
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(tg, ast.Name) and tg.id == "texts" for tg in stmt.targets):
            return literal(stmt.value)
    return None


def is_modal(cls):
    return any(
        (isinstance(b, ast.Name) and b.id == "Modal") or (isinstance(b, ast.Attribute) and b.attr == "Modal")
        for b in cls.bases
    )


def check_module(path, missing):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = func_name(node)
            if name in KEY_ARG and len(node.args) > KEY_ARG[name]:
                key = literal(node.args[KEY_ARG[name]])
                if key is not None and key not in CATALOG:
                    missing.append((path.name, node.lineno, key))
            if name == "panel_embed" and len(node.args) > 1:
                context = literal(node.args[1])
                has_title = any(kw.arg == "title" for kw in node.keywords)
                if context is not None and not has_title and f"{context}.title" not in CATALOG:
                    missing.append((path.name, node.lineno, f"{context}.title"))
        if isinstance(node, ast.ClassDef):
            prefix = class_texts_prefix(node)
            if not prefix:
                continue
            modal = is_modal(node)
            if modal and f"{prefix}.title" not in CATALOG:
                missing.append((path.name, node.lineno, f"{prefix}.title"))
            for stmt in node.body:
                if isinstance(stmt, ast.AsyncFunctionDef) and any(is_button_decorator(d) for d in stmt.decorator_list):
                    if stmt.name == "cancel":
                        continue  # подпись из nav_cancel
                    if f"{prefix}.{stmt.name}" not in CATALOG:
                        missing.append((path.name, stmt.lineno, f"{prefix}.{stmt.name}"))
                targets = []
                if isinstance(stmt, ast.Assign) and is_text_input(stmt.value):
                    targets = [tg.id for tg in stmt.targets if isinstance(tg, ast.Name)]
                for field in targets:
                    if f"{prefix}.{field}" not in CATALOG:
                        missing.append((path.name, stmt.lineno, f"{prefix}.{field}"))


def main():
    missing = []
    for name in MODULES:
        check_module(ROOT / name, missing)
    # в однострочниках вида `a=TextInput(...); b=TextInput(...)` поля — отдельные Assign,
    # ast.walk их тоже видит, так что отдельной обработки не нужно.
    seen = set()
    for module, line, key in missing:
        if key in seen:
            continue
        seen.add(key)
        print(f"{module}:{line}: нет ключа {key}")
    print(f"Ключей в каталоге: {len(CATALOG)}. Не хватает: {len(seen)}.")
    return 1 if seen else 0


if __name__ == "__main__":
    sys.exit(main())
