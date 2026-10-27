"""
/emoji — скачать эмодзи файлом: анимированные — GIF, обычные — PNG.

- emoji: одно или несколько эмодзи подряд (с любого сервера, где их видно, или
  обычные 😀 — они придут картинкой Twemoji, как в Discord);
- формат: авто (GIF у анимированных, PNG у остальных) или всегда PNG;
- все_эмодзи_сервера: все эмодзи этого сервера одним ZIP-архивом.

Файлы видны только тебе.
"""

import io
import re
import zipfile

import discord
from discord import app_commands

import core
from core import t

core.COMMAND_MIN_LEVELS.setdefault("emoji", "member")

CUSTOM = re.compile(r"<(a?):(\w{2,32}):(\d{15,25})>")
CDN = "https://cdn.discordapp.com/emojis/{id}.{ext}?size=512&quality=lossless"
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{code}.png"
MAX_FILES = 10          # вложений в одном сообщении Discord
MAX_EMOJI_BYTES = 8 * 1024 * 1024


def parse(text, as_png=False):
    """-> [(имя файла, url)] в порядке появления: свои/чужие эмодзи и обычные."""
    import preview_render
    found = []
    for match in CUSTOM.finditer(text or ""):
        animated, name, emoji_id = match[1] == "a", match[2], match[3]
        ext = "gif" if animated and not as_png else "png"
        found.append((match.start(), f"{name}.{ext}", CDN.format(id=emoji_id, ext=ext)))
    rest = CUSTOM.sub(lambda m: " " * len(m[0]), text or "")
    for start, _, seq in preview_render.emoji_matches(rest):
        code = preview_render.twemoji_code(seq)
        found.append((start, f"emoji_{code}.png", TWEMOJI.format(code=code)))
    seen, result = set(), []
    for _, name, url in sorted(found):
        if url not in seen:
            seen.add(url)
            result.append((name, url))
    return result


async def download(items):
    """-> [(имя, bytes)] — то, что удалось скачать."""
    files = []
    for name, url in items:
        data = await core.fetch_bytes(url, timeout=10, max_bytes=MAX_EMOJI_BYTES)
        if data:
            files.append((name, data))
    return files


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        used = set()
        for name, data in files:
            base, dot, ext = name.rpartition(".")
            unique, n = name, 1
            while unique in used:
                n += 1
                unique = f"{base}_{n}.{ext}"
            used.add(unique)
            archive.writestr(unique, data)
    return buffer.getvalue()


async def send_files(interaction, files):
    """По 10 файлов в сообщении; много — одним ZIP (если влезает в лимит сервера)."""
    limit = getattr(interaction.guild, "filesize_limit", 10 * 1024 * 1024)
    if len(files) > MAX_FILES:
        archive = zip_bytes(files)
        if len(archive) <= limit:
            await interaction.followup.send(t("emoji_cmd.zip", count=len(files)),
                                            file=discord.File(io.BytesIO(archive), filename="emoji.zip"), ephemeral=True)
            return
    for start in range(0, len(files), MAX_FILES):
        chunk = files[start:start + MAX_FILES]
        await interaction.followup.send(
            t("emoji_cmd.done", count=len(chunk)) if start == 0 else None,
            files=[discord.File(io.BytesIO(data), filename=name) for name, data in chunk], ephemeral=True)


def register(bot):
    @bot.tree.command(name="emoji", description="Скачать эмодзи файлом: анимированные — GIF, обычные — PNG")
    @app_commands.describe(emoji="Эмодзи (можно несколько подряд, с любого сервера)",
                           png="Всегда PNG (у анимированных — первый кадр)",
                           all_server="Все эмодзи этого сервера одним архивом")
    @app_commands.rename(png="только_png", all_server="все_эмодзи_сервера")
    async def emoji_command(interaction, emoji: str = None, png: bool = False, all_server: bool = False):
        # залить эмодзи бота на сервер — теперь /eminemovna → «Эмодзи бота»
        if not await core.require_command_access(interaction, "emoji"):
            return
        if all_server:
            items = [(f"{e.name}.{'gif' if e.animated and not png else 'png'}",
                      CDN.format(id=e.id, ext="gif" if e.animated and not png else "png"))
                     for e in interaction.guild.emojis]
        else:
            items = parse(emoji, as_png=png)
        if not items:
            await interaction.response.send_message(t("emoji_cmd.none"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        files = await download(items)
        if not files:
            await interaction.followup.send(t("emoji_cmd.failed"), ephemeral=True)
            return
        await send_files(interaction, files)
