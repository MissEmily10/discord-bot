"""
logo_module.py
==============
/logo — генератор иконок для ролей Discord в заранее заданном стиле.

Стиль (logo_styles) = текстовое описание + референс-иконки + (опционально)
LoRA, обученная на этих референсах. Режимы:
- "С нуля": короткое ТЗ -> text-to-image (FLUX; с LoRA стиля, если задана);
- "Из макета": присланная иконка/набросок -> image-to-image (модель
  редактирования перерисовывает макет в стиле);
- "Описать стиль": vision-модель смотрит на референсы и пишет описание стиля;
- "Датасет": архив референсов + подписи для обучения LoRA (самый точный стиль).
Результат: PNG 512x512 для Discord-иконки роли, кнопки "Поставить на роль"
(с подсказкой подходящих ролей по названию), "Ещё вариант", "Убрать фон".

Генерация идёт через Hugging Face Inference Providers (huggingface_hub).
Настройки — .env (см. README): HF_TOKEN, LOGO_PROVIDER, LOGO_TEXT_MODEL,
LOGO_EDIT_MODEL, LOGO_VISION_MODEL, LOGO_BG_MODEL, LOGO_DIR, LOGO_HOURLY_LIMIT.
Промпты — в каталоге текстов (logo.prompt.*), их можно править в веб-панели.
Без HF_TOKEN или без пакетов huggingface_hub/Pillow бот работает, а /logo
объясняет, чего не хватает.
"""

import asyncio
import base64
import difflib
import io
import json
import logging
import os
import pathlib
import time
import zipfile

import discord
from discord import app_commands

import core
from core import PanelView, Modal, t, panel_embed, get_user_level
from actions import say
from database import (
    save_logo_style, update_logo_style, set_default_logo_style, get_logo_style,
    get_logo_styles, delete_logo_style, save_logo_generation, get_logo_generation,
    count_recent_logo_generations,
)

_log = logging.getLogger(__name__)

ICON_SIZE = 512             # Discord рекомендует ≥64; храним с запасом, отдаём ≤256 КБ
ROLE_ICON_MAX_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_REFERENCES = 60
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

core.COMMAND_MIN_LEVELS.setdefault("logo", "staff")


# =========================
# CONFIG
# =========================

def cfg(name, default=None):
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def logo_dir():
    path = pathlib.Path(cfg("LOGO_DIR", "logo_files")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def hourly_limit():
    try:
        return int(cfg("LOGO_HOURLY_LIMIT", "10"))
    except ValueError:
        return 10


def backend_problem():
    """Ключ каталога с причиной, почему генерация недоступна, или None."""
    try:
        import huggingface_hub  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return "logo.no_packages"
    if not cfg("HF_TOKEN"):
        return "logo.no_token"
    return None


def _client():
    from huggingface_hub import AsyncInferenceClient

    return AsyncInferenceClient(provider=cfg("LOGO_PROVIDER", "fal-ai"), api_key=cfg("HF_TOKEN"), timeout=180)


# =========================
# ИЗОБРАЖЕНИЯ
# =========================

def to_icon_png(image, size=ICON_SIZE):
    """PIL.Image -> квадратный PNG (центр-кроп), с сохранением прозрачности."""
    from PIL import Image

    image = image.convert("RGBA")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def role_icon_bytes(png_bytes):
    """Ужать до лимита иконки роли (256 КБ): уменьшаем размер, затем палитра."""
    from PIL import Image

    if len(png_bytes) <= ROLE_ICON_MAX_BYTES:
        return png_bytes
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    for size in (256, 192, 128):
        for quantize in (False, True):
            candidate = image.resize((size, size), Image.LANCZOS)
            if quantize:
                candidate = candidate.quantize(colors=128, method=Image.FASTOCTREE)
            buffer = io.BytesIO()
            candidate.save(buffer, format="PNG", optimize=True)
            if buffer.tell() <= ROLE_ICON_MAX_BYTES:
                return buffer.getvalue()
    return None


def open_image(data):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    return image


async def read_attachment(attachment):
    if attachment is None:
        return None, "logo.no_image"
    if (attachment.content_type or "").split(";")[0] not in IMAGE_TYPES:
        return None, "logo.bad_image"
    if attachment.size > MAX_UPLOAD_BYTES:
        return None, "logo.image_too_large"
    data = await attachment.read()
    try:
        open_image(data)
    except Exception:  # noqa: BLE001 — любой битый файл
        return None, "logo.bad_image"
    return data, None


def store_generation(guild_id, user_id, style_id, mode, prompt, png_bytes, source_bytes=None):
    folder = logo_dir() / "generated" / str(guild_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{int(time.time() * 1000)}_{user_id}.png"
    path.write_bytes(png_bytes)
    if source_bytes:
        # исходный макет — чтобы "Ещё вариант" перерисовывал его, а не предыдущий результат
        source_path(path).write_bytes(source_bytes)
    return save_logo_generation(guild_id, user_id, style_id, mode, prompt, str(path))


def source_path(result_path):
    result_path = pathlib.Path(result_path)
    return result_path.with_name(result_path.stem + ".src.png")


def references_of(style_row):
    return [p for p in json.loads(style_row[9] or "[]") if pathlib.Path(p).exists()]


def add_reference(style_row, data):
    refs = json.loads(style_row[9] or "[]")
    if len(refs) >= MAX_REFERENCES:
        return False
    folder = logo_dir() / "references" / str(style_row[0])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{int(time.time() * 1000)}_{len(refs)}.png"
    path.write_bytes(to_icon_png(open_image(data)))
    refs.append(str(path))
    update_logo_style(style_row[0], references_json=json.dumps(refs))
    return True


# =========================
# ГЕНЕРАЦИЯ
# =========================

def build_prompt(mode, style_row, brief):
    style_text = (style_row[4] if style_row else "") or t("logo.prompt.default_style")
    key = "logo.prompt.generate" if mode == "generate" else "logo.prompt.stylize"
    return t(key, brief=brief or "", style=style_text, base=t("logo.prompt.base"))


def negative_prompt(style_row):
    parts = [t("logo.prompt.negative"), (style_row[5] if style_row else "") or ""]
    return ", ".join(p for p in parts if p)


async def generate_image(mode, style_row, brief, source_bytes=None):
    """-> PNG bytes. Исключения пробрасываются (их показывает вызывающий код)."""
    client = _client()
    prompt = build_prompt(mode, style_row, brief)
    if mode == "generate":
        # LoRA стиля — это и есть модель для text-to-image (репозиторий на HF)
        model = (style_row and (style_row[7] or style_row[6])) or cfg("LOGO_TEXT_MODEL", "black-forest-labs/FLUX.1-dev")
        image = await client.text_to_image(
            prompt, negative_prompt=negative_prompt(style_row), width=1024, height=1024, model=model,
        )
    else:
        model = cfg("LOGO_EDIT_MODEL", "black-forest-labs/FLUX.1-Kontext-dev")
        image = await client.image_to_image(
            source_bytes, prompt=prompt, negative_prompt=negative_prompt(style_row), model=model,
        )
    return to_icon_png(image)


async def remove_background(png_bytes):
    """Сегментация фона моделью RMBG и применение маски как альфа-канала."""
    client = _client()
    segments = await client.image_segmentation(png_bytes, model=cfg("LOGO_BG_MODEL", "briaai/RMBG-2.0"))
    if not segments:
        raise RuntimeError("empty segmentation")
    image = open_image(png_bytes).convert("RGBA")
    mask = segments[0].mask
    if isinstance(mask, str):  # некоторые провайдеры отдают base64
        mask = open_image(base64.b64decode(mask))
    image.putalpha(mask.convert("L").resize(image.size))
    return to_icon_png(image)


async def describe_style(style_row):
    """Vision-модель смотрит на референсы и пишет короткое описание стиля для промпта."""
    refs = references_of(style_row)[:8]
    if not refs:
        return None
    content = [{"type": "text", "text": t("logo.prompt.describe")}]
    for path in refs:
        encoded = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
    from huggingface_hub import AsyncInferenceClient

    client = AsyncInferenceClient(api_key=cfg("HF_TOKEN"), timeout=120)
    response = await client.chat_completion(
        messages=[{"role": "user", "content": content}],
        model=cfg("LOGO_VISION_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct"),
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()


def build_dataset_zip(style_row):
    """Архив для обучения LoRA: картинки + подписи с триггер-словом стиля."""
    trigger = f"rbstyle{style_row[0]}"
    caption = f"{trigger}, {style_row[4] or 'icon'}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(references_of(style_row), start=1):
            archive.write(path, f"{index:03d}.png")
            archive.writestr(f"{index:03d}.txt", caption)
        archive.writestr("README.txt", t("logo.dataset_readme", trigger=trigger))
    buffer.seek(0)
    return buffer, trigger


def import_zip(style_row, data):
    """Добавить референсы из zip. Защита от zip-бомб: лимиты на число и размер."""
    added = skipped = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()][:MAX_REFERENCES * 2]
        for member in members:
            if not member.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) or member.file_size > MAX_UPLOAD_BYTES:
                skipped += 1
                continue
            try:
                if add_reference(get_logo_style(style_row[0]), archive.read(member)):
                    added += 1
                else:
                    skipped += 1
            except Exception:  # noqa: BLE001 — битая картинка в архиве
                skipped += 1
    return added, skipped


# =========================
# ПРАВА
# =========================

def can_manage_styles(interaction):
    return core.is_owner(interaction) or core.level_value(get_user_level(interaction)) >= core.level_value("admin")


def rate_limited(interaction):
    if core.is_owner(interaction):
        return False
    return count_recent_logo_generations(interaction.user.id, int(time.time()) - 3600) >= hourly_limit()


def role_icon_problem(guild, role, member):
    if role is None:
        return "roles.not_found"
    if "ROLE_ICONS" not in guild.features:
        return "logo.no_role_icons"
    if not guild.me.guild_permissions.manage_roles:
        return "actions.role.no_manage_roles"
    if role.is_default() or role.managed:
        return "roles.managed"
    if role >= guild.me.top_role:
        return "roles.above_bot"
    if core.is_owner_id(member.id) or member.id == guild.owner_id:
        return None
    if not member.guild_permissions.manage_roles:
        return "logo.need_manage_roles"
    if role >= member.top_role:
        return "roles.above_actor"
    return None


def suggest_roles(guild, brief, member, limit=3):
    """Роли, чьё название похоже на ТЗ, и которые этот участник может менять."""
    brief = (brief or "").lower()
    if not brief:
        return []
    scored = []
    for role in guild.roles:
        if role_icon_problem(guild, role, member):
            continue
        name = role.name.lower()
        score = difflib.SequenceMatcher(None, brief, name).ratio()
        if name in brief or any(word and word in name for word in brief.split() if len(word) > 3):
            score += 0.5
        scored.append((score, role))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [role for score, role in scored[:limit] if score >= 0.45]


# =========================
# РЕЗУЛЬТАТ: постоянные кнопки
# =========================

def generation_file(generation_id):
    row = get_logo_generation(generation_id)
    if not row or not pathlib.Path(row[6]).exists():
        return None, None
    return row, pathlib.Path(row[6]).read_bytes()


class LogoActionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rb:lg:(?P<gid>\d+):(?P<act>apply|again|nobg)"):
    LABELS = {"apply": ("logo.result.apply", "🎭", discord.ButtonStyle.success),
              "again": ("logo.result.again", "🔁", discord.ButtonStyle.secondary),
              "nobg": ("logo.result.nobg", "✂️", discord.ButtonStyle.secondary)}

    def __init__(self, generation_id, act, *, item=None):
        if item is None:
            key, emoji, style = self.LABELS[act]
            item = discord.ui.Button(label=t(key)[:80], emoji=emoji, style=style, custom_id=f"rb:lg:{generation_id}:{act}")
        super().__init__(item)
        self.generation_id = generation_id
        self.act = act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["gid"]), match["act"], item=item)

    async def callback(self, interaction):
        row, data = generation_file(self.generation_id)
        if row is None or interaction.guild is None or row[1] != interaction.guild.id:
            await say(interaction, "logo.result.gone")
            return
        if row[2] != interaction.user.id and not can_manage_styles(interaction):
            await say(interaction, "logo.result.not_yours")
            return
        if self.act == "apply":
            await interaction.response.send_message(
                t("logo.apply.prompt"), view=ApplyRoleView(self.generation_id, interaction, row[5]), ephemeral=True,
            )
            return
        problem = backend_problem()
        if problem:
            await say(interaction, problem)
            return
        if rate_limited(interaction):
            await say(interaction, "logo.rate_limited", limit=hourly_limit())
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        style_row = get_logo_style(row[3]) if row[3] else None
        try:
            if self.act == "nobg":
                png = await remove_background(data)
                mode = "nobg"
            else:
                mode = row[4] if row[4] in ("generate", "stylize") else "generate"
                source = None
                if mode == "stylize":
                    src = source_path(row[6])
                    source = src.read_bytes() if src.exists() else data
                png = await generate_image(mode, style_row, row[5], source_bytes=source)
        except Exception as error:  # noqa: BLE001 — ошибки провайдера показываем текстом
            _log.warning("logo generation failed: %s", error)
            await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
            return
        await send_result(interaction, style_row, mode, row[5], png, source_bytes=source if self.act == "again" else None)


class ApplyRoleView(PanelView):
    def __init__(self, generation_id, interaction, brief):
        super().__init__(timeout=300)
        self.generation_id = generation_id
        for role in suggest_roles(interaction.guild, brief, interaction.user):
            button = discord.ui.Button(label=role.name[:80], emoji="✨", style=discord.ButtonStyle.primary, row=0)

            async def cb(i, role=role):
                await apply_icon(i, self.generation_id, role)

            button.callback = cb
            self.add_item(button)
        select = discord.ui.RoleSelect(placeholder=t("logo.apply.placeholder")[:150], row=1)

        async def picked(i):
            await apply_icon(i, self.generation_id, select.values[0])

        select.callback = picked
        self.add_item(select)


async def apply_icon(interaction, generation_id, role):
    row, data = generation_file(generation_id)
    if row is None:
        await say(interaction, "logo.result.gone")
        return
    role = interaction.guild.get_role(role.id)
    problem = role_icon_problem(interaction.guild, role, interaction.user)
    if problem:
        await say(interaction, problem)
        return
    icon = role_icon_bytes(data)
    if icon is None:
        await say(interaction, "logo.apply.too_large")
        return
    try:
        await role.edit(display_icon=icon, reason=f"/logo by {interaction.user.id}")
    except discord.HTTPException as error:
        await say(interaction, "logo.apply.failed", error=str(error)[:200])
        return
    core.audit(interaction, "logo.applied", "role", role.id, f"generation={generation_id}")
    await say(interaction, "logo.apply.done", role=role.mention)


async def send_result(interaction, style_row, mode, brief, png, source_bytes=None):
    generation_id = store_generation(interaction.guild.id, interaction.user.id, style_row[0] if style_row else None,
                                     mode, brief, png, source_bytes)
    core.audit(interaction, "logo.generated", "logo", generation_id, mode)
    view = discord.ui.View(timeout=None)
    for act in ("apply", "again", "nobg"):
        view.add_item(LogoActionButton(generation_id, act))
    embed = panel_embed(
        interaction, "logo.result",
        style=style_row[3] if style_row else t("logo.no_style"), mode=t(f"logo.mode.{mode}"), brief=brief or "—",
    )
    embed.set_image(url="attachment://icon.png")
    await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(png), "icon.png"), view=view, ephemeral=True)


DYNAMIC_ITEMS = [LogoActionButton]


# =========================
# СТИЛИ: панель
# =========================

def style_embed(interaction, row):
    return panel_embed(
        interaction, "logo.style_card", title=row[3],
        id=row[0], prompt=(row[4] or "—")[:1500], negative=row[5] or "—",
        model=row[6] or t("logo.default_model"), lora=row[7] or "—",
        refs=len(references_of(row)), default=t("common.yes") if row[10] else t("common.no"),
    )


class StyleModal(Modal, title="СТИЛЬ ИКОНОК"):
    texts = "logo.style_modal"

    name = discord.ui.TextInput(label="Название стиля", max_length=80)
    prompt = discord.ui.TextInput(label="Описание стиля (для нейросети)", style=discord.TextStyle.paragraph, required=False, max_length=1500)
    negative = discord.ui.TextInput(label="Чего избегать", required=False, max_length=500)
    lora = discord.ui.TextInput(label="LoRA (репозиторий HF), если есть", required=False, max_length=200)
    model = discord.ui.TextInput(label="Своя модель text-to-image", required=False, max_length=200)

    def __init__(self, row=None):
        super().__init__()
        self.row = row
        if row:
            self.name.default = row[3]
            self.prompt.default = row[4]
            self.negative.default = row[5]
            self.lora.default = row[7] or ""
            self.model.default = row[6] or ""

    async def on_submit(self, interaction):
        fields = dict(
            name=self.name.value.strip(), prompt=self.prompt.value.strip(), negative_prompt=self.negative.value.strip(),
            lora=self.lora.value.strip() or None, model=self.model.value.strip() or None,
        )
        if self.row:
            update_logo_style(self.row[0], **fields)
            style_id = self.row[0]
        else:
            style_id = save_logo_style(interaction.guild.id, interaction.user.id, fields["name"], fields["prompt"],
                                       fields["negative_prompt"], fields["model"], fields["lora"])
            if len(get_logo_styles(interaction.guild.id)) == 1:
                set_default_logo_style(interaction.guild.id, style_id)
        core.audit(interaction, "logo.style_saved", "logo_style", style_id, fields["name"])
        row = get_logo_style(style_id)
        await interaction.response.send_message(embed=style_embed(interaction, row), view=StyleActions(style_id), ephemeral=True)


class StyleActions(PanelView):
    texts = "logo.style_actions"

    def __init__(self, style_id, back_target=None):
        super().__init__(back_target=back_target)
        self.style_id = style_id

    async def _row(self, interaction):
        row = get_logo_style(self.style_id)
        if not row or row[1] != interaction.guild.id:
            await say(interaction, "logo.style_missing")
            return None
        if not can_manage_styles(interaction):
            await say(interaction, "common.need_admin")
            return None
        return row

    @discord.ui.button(label="Изменить", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        row = await self._row(interaction)
        if row:
            await interaction.response.send_modal(StyleModal(row))

    @discord.ui.button(label="По умолчанию", emoji="⭐", style=discord.ButtonStyle.secondary)
    async def make_default(self, interaction, button):
        row = await self._row(interaction)
        if row:
            set_default_logo_style(interaction.guild.id, self.style_id)
            await interaction.response.edit_message(embed=style_embed(interaction, get_logo_style(self.style_id)), view=self)

    @discord.ui.button(label="Описать по референсам", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def describe(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        problem = backend_problem()
        if problem:
            await say(interaction, problem)
            return
        if not references_of(row):
            await say(interaction, "logo.no_references")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            text = await describe_style(row)
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
            return
        update_logo_style(self.style_id, prompt=text[:1500])
        await interaction.followup.send(embed=style_embed(interaction, get_logo_style(self.style_id)), ephemeral=True)

    @discord.ui.button(label="Датасет для LoRA", emoji="📦", style=discord.ButtonStyle.secondary, row=1)
    async def dataset(self, interaction, button):
        row = await self._row(interaction)
        if not row:
            return
        if not references_of(row):
            await say(interaction, "logo.no_references")
            return
        buffer, trigger = build_dataset_zip(row)
        try:
            await interaction.response.send_message(
                t("logo.dataset_ready", trigger=trigger),
                file=discord.File(buffer, f"style_{row[0]}_dataset.zip"), ephemeral=True,
            )
        except discord.HTTPException:
            await say(interaction, "access.settings.backup_too_large")

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction, button):
        row = await self._row(interaction)
        if row:
            delete_logo_style(self.style_id)
            core.audit(interaction, "logo.style_deleted", "logo_style", self.style_id, row[3])
            await interaction.response.edit_message(embed=panel_embed(interaction, "logo.style_deleted"), view=None)


class StylesHome(PanelView):
    texts = "logo.styles"

    def __init__(self, interaction):
        super().__init__()
        for index, row in enumerate(get_logo_styles(interaction.guild.id)[:15]):
            marker = " ⭐" if row[10] else ""
            button = discord.ui.Button(label=f"{row[3][:60]}{marker} · #{row[0]}", style=discord.ButtonStyle.secondary, row=1 + index // 5)

            async def cb(i, style_id=row[0]):
                style = get_logo_style(style_id)
                if not style:
                    await say(i, "logo.style_missing")
                    return
                await i.response.edit_message(embed=style_embed(i, style), view=StyleActions(style_id, back_target=(i.message.embeds[0], self)))

            button.callback = cb
            self.add_item(button)

    @discord.ui.button(label="Новый стиль", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def create(self, interaction, button):
        if not can_manage_styles(interaction):
            await say(interaction, "common.need_admin")
            return
        await interaction.response.send_modal(StyleModal())


# =========================
# /logo
# =========================

async def style_autocomplete(interaction, current):
    if interaction.guild is None:
        return []
    current = (current or "").lower()
    return [
        app_commands.Choice(name=f"{row[3][:90]} (#{row[0]})", value=row[0])
        for row in get_logo_styles(interaction.guild.id)
        if current in row[3].lower()
    ][:25]


def pick_style(guild_id, style_id):
    if style_id:
        row = get_logo_style(style_id)
        return row if row and row[1] == guild_id else None
    rows = get_logo_styles(guild_id)
    return rows[0] if rows else None  # первым идёт стиль по умолчанию


async def _precheck(interaction):
    if not await core.require_command_access(interaction, "logo"):
        return False
    problem = backend_problem()
    if problem:
        await say(interaction, problem)
        return False
    if rate_limited(interaction):
        await say(interaction, "logo.rate_limited", limit=hourly_limit())
        return False
    return True


def register_logo(bot):
    group = app_commands.Group(name="logo", description="Иконки для ролей в твоём стиле", guild_only=True)

    @group.command(name="generate", description="Сгенерировать иконку с нуля по короткому ТЗ")
    @app_commands.describe(brief="Что изобразить, коротко", style="Стиль (по умолчанию — основной)")
    @app_commands.autocomplete(style=style_autocomplete)
    async def generate(interaction, brief: app_commands.Range[str, 2, 300], style: int = None):
        if not await _precheck(interaction):
            return
        style_row = pick_style(interaction.guild.id, style)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            png = await generate_image("generate", style_row, brief)
        except Exception as error:  # noqa: BLE001
            _log.warning("logo generation failed: %s", error)
            await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
            return
        await send_result(interaction, style_row, "generate", brief, png)

    @group.command(name="stylize", description="Перерисовать твой макет иконки в выбранном стиле")
    @app_commands.describe(image="Макет или набросок иконки", brief="Что важно сохранить/добавить", style="Стиль")
    @app_commands.autocomplete(style=style_autocomplete)
    async def stylize(interaction, image: discord.Attachment, brief: str = "", style: int = None):
        if not await _precheck(interaction):
            return
        data, error = await read_attachment(image)
        if error:
            await say(interaction, error)
            return
        style_row = pick_style(interaction.guild.id, style)
        await interaction.response.defer(ephemeral=True, thinking=True)
        source = to_icon_png(open_image(data), 1024)
        try:
            png = await generate_image("stylize", style_row, brief[:300], source_bytes=source)
        except Exception as error:  # noqa: BLE001
            _log.warning("logo stylize failed: %s", error)
            await interaction.followup.send(t("logo.failed", error=str(error)[:300]), ephemeral=True)
            return
        await send_result(interaction, style_row, "stylize", brief[:300], png, source_bytes=source)

    @group.command(name="styles", description="Стили иконок: создать, описать, датасет для LoRA")
    async def styles(interaction):
        if not await core.require_command_access(interaction, "logo"):
            return
        await interaction.response.send_message(embed=panel_embed(interaction, "logo.styles"), view=StylesHome(interaction), ephemeral=True)

    @group.command(name="reference", description="Добавить иконку-референс в стиль (можно zip-архив)")
    @app_commands.describe(style="Стиль", file="PNG/JPG/WEBP или zip с иконками")
    @app_commands.autocomplete(style=style_autocomplete)
    async def reference(interaction, style: int, file: discord.Attachment):
        if not await core.require_command_access(interaction, "logo"):
            return
        if not can_manage_styles(interaction):
            await say(interaction, "common.need_admin")
            return
        row = pick_style(interaction.guild.id, style)
        if row is None:
            await say(interaction, "logo.style_missing")
            return
        try:
            import PIL  # noqa: F401
        except ImportError:
            await say(interaction, "logo.no_packages")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if file.filename.lower().endswith(".zip"):
            if file.size > 50 * 1024 * 1024:
                await interaction.followup.send(t("logo.image_too_large"), ephemeral=True)
                return
            try:
                added, skipped = await asyncio.to_thread(import_zip, row, await file.read())
            except zipfile.BadZipFile:
                await interaction.followup.send(t("logo.bad_image"), ephemeral=True)
                return
        else:
            data, error = await read_attachment(file)
            if error:
                await interaction.followup.send(t(error), ephemeral=True)
                return
            added, skipped = (1, 0) if add_reference(row, data) else (0, 1)
        total = len(references_of(get_logo_style(row[0])))
        core.audit(interaction, "logo.references_added", "logo_style", row[0], f"+{added}")
        await interaction.followup.send(t("logo.references_added", added=added, skipped=skipped, total=total, max=MAX_REFERENCES), ephemeral=True)

    bot.tree.add_command(group)
