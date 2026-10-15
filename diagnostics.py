"""
«Бункер → Диагностика»: почему не видно баннеров и дудлов на запущенном боте.
Показывает версию кода, адрес веб-панели (через него Discord берёт картинки),
баннер /embed и сколько дудлов бот смог залить в свои эмодзи.
"""

import os
import subprocess

import discord

import core
from core import t, panel_embed

ROOT = os.path.dirname(os.path.abspath(__file__))


def code_version():
    """Последний коммит (если на хостинге есть .git) или дата самого свежего файла бота."""
    try:
        out = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%h · %cd", "--date=format:%d.%m %H:%M"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    import datetime
    newest = max(os.path.getmtime(os.path.join(ROOT, f)) for f in os.listdir(ROOT) if f.endswith(".py"))
    return t("diag.version_files", date=datetime.datetime.fromtimestamp(newest).strftime("%d.%m %H:%M"))


def report(interaction):
    """-> (embed с отчётом, URL баннера /embed для проверки или None)."""
    import ui_emoji
    import web_panel
    lines = [t("diag.version", version=code_version())]
    url = web_panel.panel_url()
    lines.append(t("diag.panel_ok", url=url) if url else t("diag.panel_missing"))
    banner = core.resolve_banner(interaction, "embed.home")
    if banner:
        lines.append(t("diag.banner_ok", url=banner))
    elif not os.path.isfile(os.path.join(ROOT, "panel_art", "embed.home.banner.png")):
        lines.append(t("diag.banner_no_file"))
    else:
        lines.append(t("diag.banner_no_url"))
    if not ui_emoji.enabled():
        lines.append(t("diag.emoji_off"))
    for folder, label in (("ui", t("diag.emoji_ui")), ("emoji", t("diag.emoji_forms"))):
        state = ui_emoji.load_status.get(folder)
        if state is None:
            lines.append(t("diag.emoji_not_started", what=label))
            continue
        line = t("diag.emoji_state", what=label, loaded=state["loaded"], files=state["files"],
                 done=t("diag.done") if state["done"] else t("diag.in_progress"))
        if state["errors"]:
            line += "\n" + t("diag.emoji_errors", errors="; ".join(state["errors"][:3])[:600])
        lines.append(line)
    embed = panel_embed(interaction, "diag", description="\n".join(lines), hints=False)
    return embed, banner


async def send_report(interaction):
    embed, banner = report(interaction)
    embeds = [embed]
    if banner:
        # если Discord покажет эту картинку — значит, он может брать картинки с веб-панели
        embeds.append(discord.Embed(description=t("diag.banner_test"), color=core.embed_color()).set_image(url=banner))
    await interaction.response.send_message(embeds=embeds, ephemeral=True)
