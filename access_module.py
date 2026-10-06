"""
access_module.py
=================
/access — переработанный Access Manager.

Изменения относительно старой версии:
- Убрана пустая вкладка "История" (общий audit-лог будет отдельным
  модулем позже, audit_logs в БД уже готова под это).
- "Настройки" — теперь настоящая вкладка, доступна только владельцу
  ("бункер-подвал"): бэкап bot.db, быстрый список денаев, reload,
  управление action_registry.
- "Временный уровень" больше не отдельная кнопка — назначение уровня
  всегда идёт через шаг "навсегда / на срок", без слова "временный" в UI.
- Deny пользователя теперь требует подтверждения (как webhook delete).
- CommandAccessView обновляет список через edit_message, а не шлёт
  отдельные ephemeral-сообщения поверх панели.
- Все панели наследуются от core.PanelView — кнопка "Назад" везде,
  где есть куда возвращаться.
- Все тексты — из каталога texts.py (core.t / core.panel_embed).
"""

import time
from datetime import datetime

import discord

import core
from core import (
    PanelView, Modal, t, panel_embed, level_label,
    is_owner, get_user_level, can_manage_access, can_view_access,
    can_assign_level, can_manage_user, get_setting, set_setting, reset_setting,
    ensure_default_actions, get_actions, audit,
)
import database
from database import (
    add_command_access, get_command_access_details,
    remove_command_access, set_access_level,
    get_member_info, set_role_access, remove_role_access,
    deny_user, is_user_denied, undeny_user, get_denied_users,
    set_action_enabled, set_action_min_level, set_action_use_level, set_action_dangerous,
    get_audit_logs,
)


def format_expiration(expires_at):
    if expires_at is None:
        return t("access.expires_never")
    return datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y %H:%M")


def get_bot_commands(bot):
    return [
        (command.name, command.description or t("access.command.no_description"))
        for command in bot.tree.get_commands()
    ]


async def deny(interaction, key):
    await interaction.response.send_message(t(key), ephemeral=True)


# ============================================================
# ГЛАВНЫЙ ЭКРАН
# ============================================================

class AccessHomeView(PanelView):

    texts = "access.home"

    def __init__(self, bot):
        super().__init__(back_target=None)
        self.bot = bot

    @discord.ui.button(label="Пользователи", emoji="👥", style=discord.ButtonStyle.secondary)
    async def users_button(self, interaction, button):
        if not can_view_access(interaction):
            await deny(interaction, "text_access_denied")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.users"),
            view=UserManagementView(back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Команды", emoji="🧩", style=discord.ButtonStyle.primary)
    async def commands_button(self, interaction, button):
        if not can_view_access(interaction):
            await deny(interaction, "text_access_denied")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.commands"),
            view=CommandSelectView(self.bot, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Доступ по ролям", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def role_access_button(self, interaction, button):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.roles"),
            view=RoleAccessView(back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Запреты", emoji="🚫", style=discord.ButtonStyle.danger)
    async def denials_button(self, interaction, button):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        await interaction.response.edit_message(
            embed=denial_list_embed(interaction),
            view=DenialAccessView(interaction, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Журнал", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def audit_button(self, interaction, button):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        await interaction.response.edit_message(
            embed=audit_embed(interaction),
            view=PanelView(back_target=(interaction.message.embeds[0], self)),
        )

    @discord.ui.button(label="⚙️ Настройки", style=discord.ButtonStyle.secondary, row=1)
    async def settings_button(self, interaction, button):
        if not is_owner(interaction):
            await deny(interaction, "common.owner_only")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.settings"),
            view=SettingsView(self.bot, back_target=(interaction.message.embeds[0], self))
        )


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

class UserManagementView(PanelView):

    def __init__(self, back_target):
        super().__init__(back_target=back_target)
        self.user_select = discord.ui.UserSelect(placeholder=t("common.pick_user")[:150], min_values=1, max_values=1)
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

    async def user_selected(self, interaction):
        user = self.user_select.values[0]
        level, expires_at = get_member_info(interaction.guild.id, user.id)

        embed = panel_embed(
            interaction, "access.user",
            user=user.mention, level=level_label(level), expires=format_expiration(expires_at),
        )
        await interaction.response.edit_message(
            embed=embed,
            view=UserLevelView(user.id, user.mention, back_target=(interaction.message.embeds[0], self))
        )


class UserLevelView(PanelView):
    """Выбор уровня. Дальше — отдельный экран длительности (без слова 'временный')."""

    texts = "access.user"

    def __init__(self, user_id, user_mention, back_target):
        super().__init__(back_target=back_target)
        self.user_id = user_id
        self.user_mention = user_mention

    async def go_duration(self, interaction, level):
        if not can_assign_level(interaction, level) or not can_manage_user(interaction, self.user_id):
            await deny(interaction, "access.user.cannot_assign")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.duration", user=self.user_mention, level=level_label(level)),
            view=LevelDurationView(self.user_id, self.user_mention, level, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Ограниченный", style=discord.ButtonStyle.secondary)
    async def limited_button(self, interaction, button):
        await self.go_duration(interaction, "limited")

    @discord.ui.button(label="Обычный участник", style=discord.ButtonStyle.secondary)
    async def member_button(self, interaction, button):
        await self.go_duration(interaction, "member")

    @discord.ui.button(label="Staff", style=discord.ButtonStyle.primary)
    async def staff_button(self, interaction, button):
        await self.go_duration(interaction, "staff")

    @discord.ui.button(label="Администратор", style=discord.ButtonStyle.success)
    async def admin_button(self, interaction, button):
        await self.go_duration(interaction, "admin")


class LevelDurationCustomModal(Modal, title="СВОЙ СРОК"):

    texts = "access.duration_modal"

    hours = discord.ui.TextInput(label="На сколько часов", placeholder="Например: 12", max_length=6)

    def __init__(self, user_id, user_mention, level):
        super().__init__()
        self.user_id = user_id
        self.user_mention = user_mention
        self.level = level

    async def on_submit(self, interaction):
        try:
            hours = int(self.hours.value.strip())
            if hours <= 0:
                raise ValueError
        except ValueError:
            await deny(interaction, "access.duration_modal.bad_hours")
            return

        if not can_assign_level(interaction, self.level) or not can_manage_user(interaction, self.user_id):
            await deny(interaction, "access.user.cannot_assign")
            return
        expires_at = int(time.time() + hours * 3600)
        set_access_level(interaction.guild.id, self.user_id, self.level, expires_at)
        audit(interaction, "access.level_set", "user", self.user_id, f"{self.level} for {hours}h")
        await interaction.response.send_message(
            t("access.duration.done_hours", user=self.user_mention, level=level_label(self.level), hours=hours),
            ephemeral=True
        )


class LevelDurationView(PanelView):

    texts = "access.duration"

    def __init__(self, user_id, user_mention, level, back_target):
        super().__init__(back_target=back_target)
        self.user_id = user_id
        self.user_mention = user_mention
        self.level = level

    async def apply(self, interaction, seconds):
        if not can_assign_level(interaction, self.level) or not can_manage_user(interaction, self.user_id):
            await deny(interaction, "access.user.cannot_assign")
            return
        expires_at = int(time.time() + seconds) if seconds else None
        set_access_level(interaction.guild.id, self.user_id, self.level, expires_at)
        audit(interaction, "access.level_set", "user", self.user_id, f"{self.level} until {expires_at or 'forever'}")
        until = t("access.duration.forever_word") if seconds is None else format_expiration(expires_at)
        await interaction.response.send_message(
            t("access.duration.done", user=self.user_mention, level=level_label(self.level), until=until),
            ephemeral=True
        )

    @discord.ui.button(label="Навсегда", emoji="♾️", style=discord.ButtonStyle.success)
    async def forever(self, interaction, button):
        await self.apply(interaction, None)

    @discord.ui.button(label="1 день", style=discord.ButtonStyle.secondary)
    async def one_day(self, interaction, button):
        await self.apply(interaction, 86400)

    @discord.ui.button(label="7 дней", style=discord.ButtonStyle.secondary)
    async def one_week(self, interaction, button):
        await self.apply(interaction, 604800)

    @discord.ui.button(label="Свой срок", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def custom(self, interaction, button):
        await interaction.response.send_modal(
            LevelDurationCustomModal(self.user_id, self.user_mention, self.level)
        )


# ============================================================
# КОМАНДЫ
# ============================================================

class CommandSelectView(PanelView):

    def __init__(self, bot, back_target):
        super().__init__(back_target=back_target)
        self.bot = bot

        for command_name, description in get_bot_commands(bot):
            button = discord.ui.Button(label=f"/{command_name}", style=discord.ButtonStyle.secondary)

            async def command_callback(interaction, command_name=command_name, description=description):
                embed = command_access_embed(interaction, command_name, description)
                await interaction.response.edit_message(
                    embed=embed,
                    view=CommandAccessView(command_name, description, back_target=(interaction.message.embeds[0], self))
                )

            button.callback = command_callback
            self.add_item(button)


def command_access_embed(interaction, command_name, description):
    details = get_command_access_details(interaction.guild.id, command_name)
    access_text = "\n".join(
        f"<@{user_id}> — {format_expiration(expires_at)}" for user_id, expires_at in details
    ) or t("access.command.no_users")

    required = core.COMMAND_MIN_LEVELS.get(command_name)
    level_text = level_label(required) if required else t("access.command.level_unset")

    return panel_embed(
        interaction, "access.command",
        command=command_name, description=description, level=level_text, users=access_text,
    )


class CommandAccessView(PanelView):
    """
    Все действия обновляют ЭТУ ЖЕ панель через edit_message — список
    сразу видно свежим, без отдельных всплывающих сообщений.
    """

    texts = "access.command"

    def __init__(self, command_name, description, back_target):
        super().__init__(back_target=back_target)
        self.command_name = command_name
        self.description = description

    @discord.ui.button(label="Добавить", emoji="➕", style=discord.ButtonStyle.success)
    async def add_user(self, interaction, button):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.command_add", command=self.command_name),
            view=CommandUserPickView(self.command_name, self.description, "add", back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Убрать", emoji="➖", style=discord.ButtonStyle.danger)
    async def remove_user(self, interaction, button):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.command_remove", command=self.command_name),
            view=CommandUserPickView(self.command_name, self.description, "remove", back_target=(interaction.message.embeds[0], self))
        )


class CommandUserPickView(PanelView):

    def __init__(self, command_name, description, mode, back_target):
        super().__init__(back_target=back_target)
        self.command_name = command_name
        self.description = description
        self.mode = mode
        self.user_select = discord.ui.UserSelect(placeholder=t("common.pick_user")[:150], min_values=1, max_values=1)
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

    async def user_selected(self, interaction):
        user = self.user_select.values[0]

        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        if self.mode == "remove":
            remove_command_access(interaction.guild.id, self.command_name, user.id)
            audit(interaction, "access.command_revoked", "user", user.id, self.command_name)
            await interaction.response.edit_message(
                embed=command_access_embed(interaction, self.command_name, self.description),
                view=CommandAccessView(self.command_name, self.description, back_target=self.back_target)
            )
            return

        # add -> спрашиваем срок
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.command_duration", command=self.command_name, user=user.mention),
            view=CommandDurationView(self.command_name, self.description, user.id, user.mention, back_target=(interaction.message.embeds[0], self))
        )


class CommandDurationView(PanelView):

    texts = "access.duration"

    def __init__(self, command_name, description, user_id, user_mention, back_target):
        super().__init__(back_target=back_target)
        self.command_name = command_name
        self.description = description
        self.user_id = user_id
        self.user_mention = user_mention

    async def apply(self, interaction, seconds):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        expires_at = int(time.time() + seconds) if seconds else None
        add_command_access(interaction.guild.id, self.command_name, self.user_id, expires_at)
        audit(interaction, "access.command_granted", "user", self.user_id, self.command_name)
        await interaction.response.edit_message(
            embed=command_access_embed(interaction, self.command_name, self.description),
            view=CommandAccessView(self.command_name, self.description, back_target=self.back_target)
        )

    @discord.ui.button(label="Навсегда", emoji="♾️", style=discord.ButtonStyle.success)
    async def forever(self, interaction, button):
        await self.apply(interaction, None)

    @discord.ui.button(label="1 день", style=discord.ButtonStyle.secondary)
    async def one_day(self, interaction, button):
        await self.apply(interaction, 86400)

    @discord.ui.button(label="7 дней", style=discord.ButtonStyle.secondary)
    async def one_week(self, interaction, button):
        await self.apply(interaction, 604800)


# ============================================================
# ДОСТУП ПО РОЛЯМ
# ============================================================

class RoleAccessView(PanelView):

    def __init__(self, back_target):
        super().__init__(back_target=back_target)
        select = discord.ui.RoleSelect(placeholder=t("common.pick_role")[:150], min_values=1, max_values=1)
        select.callback = self.selected
        self.add_item(select)

    async def selected(self, interaction):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        role = self.children[0].values[0]
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.role", role=role.mention),
            view=RoleLevelAccessView(role.id, role.mention, back_target=(interaction.message.embeds[0], self))
        )


class RoleLevelAccessView(PanelView):

    texts = "access.role"

    def __init__(self, role_id, mention, back_target):
        super().__init__(back_target=back_target)
        self.role_id = role_id
        self.mention = mention

    def embed(self, interaction):
        return panel_embed(interaction, "access.role", role=self.mention)

    async def apply(self, interaction, level):
        # уровень роли получают ВСЕ её обладатели — назначать можно только то,
        # что ты вправе назначить человеку (admin — только владелец)
        if not can_manage_access(interaction) or not can_assign_level(interaction, level):
            await deny(interaction, "access.user.cannot_assign")
            return
        set_role_access(interaction.guild.id, self.role_id, level, None)
        audit(interaction, "access.role_level_set", "role", self.role_id, level)
        await interaction.response.edit_message(
            embed=self.embed(interaction),
            view=self,
        )

    @discord.ui.button(label="Ограниченный", style=discord.ButtonStyle.secondary)
    async def limited(self, interaction, button):
        await self.apply(interaction, "limited")

    @discord.ui.button(label="Пользователь", style=discord.ButtonStyle.secondary)
    async def member(self, interaction, button):
        await self.apply(interaction, "member")

    @discord.ui.button(label="Staff", style=discord.ButtonStyle.primary)
    async def staff(self, interaction, button):
        await self.apply(interaction, "staff")

    @discord.ui.button(label="Администратор", style=discord.ButtonStyle.success)
    async def admin(self, interaction, button):
        await self.apply(interaction, "admin")

    @discord.ui.button(label="Убрать", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        if not can_manage_access(interaction):
            await deny(interaction, "common.not_enough_rights")
            return
        remove_role_access(interaction.guild.id, self.role_id)
        audit(interaction, "access.role_level_removed", "role", self.role_id)
        await interaction.response.edit_message(
            embed=self.embed(interaction),
            view=self,
        )


# ============================================================
# ЗАПРЕТЫ (deny) — с подтверждением
# ============================================================

def denial_list_embed(interaction):
    rows = get_denied_users(interaction.guild.id)
    text = "\n".join(
        f"<@{uid}> — {reason or t('access.denials.no_reason')}" for uid, reason, _ in rows
    ) or t("access.denials.empty")
    return panel_embed(interaction, "access.denials", users=text)


class DenialAccessView(PanelView):

    def __init__(self, interaction, back_target):
        super().__init__(back_target=back_target)
        select = discord.ui.UserSelect(placeholder=t("common.pick_user")[:150], min_values=1, max_values=1)
        select.callback = self.selected
        self.add_item(select)

    async def selected(self, interaction):
        if not can_manage_access(interaction):
            await deny(interaction, "common.need_admin")
            return
        user = self.children[0].values[0]
        if not can_manage_user(interaction, user.id):
            await deny(interaction, "access.user.cannot_assign")
            return
        reason = is_user_denied(interaction.guild.id, user.id)

        if reason is not None:
            # уже забанен — снимаем сразу, это не опасное действие
            undeny_user(interaction.guild.id, user.id)
            audit(interaction, "access.undenied", "user", user.id)
            await interaction.response.edit_message(
                embed=denial_list_embed(interaction),
                view=DenialAccessView(interaction, back_target=self.back_target)
            )
            return

        # бан — требует подтверждения
        await interaction.response.edit_message(
            embed=panel_embed(interaction, "access.deny_confirm", danger=True, user=user.mention),
            view=DenyConfirmView(user.id, user.mention, back_target=(interaction.message.embeds[0], self))
        )


class DenyConfirmView(PanelView):

    texts = "access.deny_confirm"

    def __init__(self, user_id, user_mention, back_target):
        super().__init__(back_target=back_target)
        self.user_id = user_id
        self.user_mention = user_mention

    @discord.ui.button(label="Подтвердить запрет", emoji="🚫", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not can_manage_user(interaction, self.user_id):
            await deny(interaction, "access.user.cannot_assign")
            return
        deny_user(interaction.guild.id, self.user_id, t("access.denials.default_reason"))
        audit(interaction, "access.denied", "user", self.user_id)
        await interaction.response.edit_message(
            embed=denial_list_embed(interaction),
            view=DenialAccessView(interaction, back_target=self.back_target)
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        embed, view = self.back_target
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# НАСТРОЙКИ (owner-only "бункер-подвал")
# ============================================================

async def ensure_owner(interaction):
    if is_owner(interaction):
        return True
    await deny(interaction, "common.owner_only")
    return False


class SettingsView(PanelView):

    texts = "access.settings"

    def __init__(self, bot, back_target):
        super().__init__(back_target=back_target)
        self.bot = bot

    @discord.ui.button(label="Бэкап bot.db", emoji="💾", style=discord.ButtonStyle.secondary)
    async def backup(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        try:
            await interaction.response.send_message(
                file=discord.File(database.DATABASE_NAME, filename=f"bot_backup_{int(time.time())}.db"),
                ephemeral=True
            )
        except FileNotFoundError:
            await deny(interaction, "access.settings.no_db")
        except discord.HTTPException:
            # файл больше лимита загрузки Discord
            await deny(interaction, "access.settings.backup_too_large")

    @discord.ui.button(label="Денаи", emoji="🚫", style=discord.ButtonStyle.secondary)
    async def denials(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=denial_list_embed(interaction),
            view=DenialAccessView(interaction, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Action Registry", emoji="🔑", style=discord.ButtonStyle.primary)
    async def registry(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        ensure_default_actions()
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=ActionRegistryView(back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Перезапуск", emoji="🔄", style=discord.ButtonStyle.danger)
    async def restart(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        # Discord-бот не может сам себя перезапустить как процесс —
        # это должен делать внешний менеджер процессов (systemd/pm2/docker restart).
        # bot.close() корректно завершает соединение, а менеджер поднимает заново.
        await deny(interaction, "access.settings.restarting")
        await self.bot.close()


# ============================================================
# /design
# ============================================================

DESIGN_KEYS = ("embed_color", "danger_color", "text_access_denied", "nav_back", "nav_cancel")


def design_embed(interaction):
    return panel_embed(interaction, "design", **{key: get_setting(key) for key in DESIGN_KEYS})


class DesignModal(Modal, title="DESIGN SETTINGS"):

    texts = "design.modal"

    embed_color_input = discord.ui.TextInput(label="Основной HEX цвет", max_length=8)
    danger_color_input = discord.ui.TextInput(label="Опасный HEX цвет", max_length=8)
    denied_input = discord.ui.TextInput(label="Текст отказа", max_length=200)
    back_input = discord.ui.TextInput(label="Текст кнопки Назад", max_length=80)
    cancel_input = discord.ui.TextInput(label="Текст кнопки Отмена", max_length=80)

    def __init__(self):
        super().__init__()
        self.embed_color_input.default = get_setting("embed_color")
        self.danger_color_input.default = get_setting("danger_color")
        self.denied_input.default = get_setting("text_access_denied")
        self.back_input.default = get_setting("nav_back")
        self.cancel_input.default = get_setting("nav_cancel")

    @staticmethod
    def valid_color(value):
        value = value.strip().lower().replace("#", "")
        if value.startswith("0x"):
            value = value[2:]
        if len(value) != 6:
            return None
        try:
            int(value, 16)
        except ValueError:
            return None
        return "0x" + value.upper()

    async def on_submit(self, interaction):
        primary = self.valid_color(self.embed_color_input.value)
        danger = self.valid_color(self.danger_color_input.value)
        if not primary or not danger:
            await deny(interaction, "design.bad_color")
            return
        set_setting("embed_color", primary)
        set_setting("danger_color", danger)
        # пустое поле = вернуть фразу по умолчанию
        for key, field in (
            ("text_access_denied", self.denied_input),
            ("nav_back", self.back_input),
            ("nav_cancel", self.cancel_input),
        ):
            if field.value.strip():
                set_setting(key, field.value.strip())
            else:
                reset_setting(key)
        await interaction.response.edit_message(embed=design_embed(interaction), view=DesignView())


class DesignView(PanelView):

    texts = "design"

    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)

    @discord.ui.button(label="Изменить", emoji="🎨", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        if await ensure_owner(interaction):
            await interaction.response.send_modal(DesignModal())

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        if await ensure_owner(interaction):
            await interaction.response.edit_message(embed=design_embed(interaction), view=DesignView())

    @discord.ui.button(label="Сбросить", emoji="↩️", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        for key in DESIGN_KEYS:
            reset_setting(key)
        await interaction.response.edit_message(embed=design_embed(interaction), view=DesignView())


# ============================================================
# ACTION REGISTRY
# ============================================================

def audit_embed(interaction):
    rows = get_audit_logs(interaction.guild.id, limit=20)
    lines = [
        t("access.audit.line", time=f"<t:{created_at}:R>", actor=f"<@{actor_id}>", action=action,
          target=f"{target_type or ''} {target_id or ''}".strip(), details=details or "")
        for actor_id, action, target_type, target_id, details, created_at in rows
    ]
    return panel_embed(interaction, "access.audit", entries=("\n".join(lines) or t("access.audit.empty"))[:3900])


def action_registry_embed(interaction):
    rows = get_actions()
    lines = []
    for key, min_level, dangerous, description, enabled, use_level in rows:
        status = "🟢" if enabled else "🔴"
        danger = "⚠️" if dangerous else ""
        lines.append(t("access.registry.line", status=status, key=key, create=level_label(min_level), use=level_label(use_level), danger=danger))
    text = "\n".join(lines) or t("access.registry.empty")
    return panel_embed(interaction, "access.registry", actions=text)


class ActionRegistryView(PanelView):
    """Выбор конкретного action для правки — чтобы не городить 20 кнопок сразу."""

    def __init__(self, back_target):
        super().__init__(back_target=back_target)
        rows = get_actions()
        for key, min_level, dangerous, description, enabled, use_level in rows[:20]:
            button = discord.ui.Button(label=key, style=discord.ButtonStyle.secondary)

            async def callback(interaction, key=key):
                await interaction.response.edit_message(
                    embed=panel_embed(interaction, "access.action", title=key),
                    view=ActionEditView(key, back_target=(interaction.message.embeds[0], self))
                )

            button.callback = callback
            self.add_item(button)


class ActionEditView(PanelView):

    texts = "access.action"

    def __init__(self, action_key, back_target):
        super().__init__(back_target=back_target)
        self.action_key = action_key

    async def set_level(self, interaction, level):
        if not await ensure_owner(interaction):
            return
        set_action_min_level(self.action_key, level)
        audit(interaction, "registry.min_level", "action", self.action_key, level)
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=self.back_target[1],
        )

    @discord.ui.button(label="Member", row=0, style=discord.ButtonStyle.secondary)
    async def member(self, interaction, button):
        await self.set_level(interaction, "member")

    @discord.ui.button(label="Staff", row=0, style=discord.ButtonStyle.secondary)
    async def staff(self, interaction, button):
        await self.set_level(interaction, "staff")

    @discord.ui.button(label="Admin", row=0, style=discord.ButtonStyle.secondary)
    async def admin(self, interaction, button):
        await self.set_level(interaction, "admin")

    @discord.ui.button(label="Включить", emoji="🟢", row=1, style=discord.ButtonStyle.success)
    async def enable(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        set_action_enabled(self.action_key, True)
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=self.back_target[1],
        )

    @discord.ui.button(label="Выключить", emoji="🔴", row=1, style=discord.ButtonStyle.danger)
    async def disable(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        set_action_enabled(self.action_key, False)
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=self.back_target[1],
        )

    @discord.ui.button(label="Подтверждение вкл/выкл", emoji="⚠️", row=1, style=discord.ButtonStyle.secondary)
    async def toggle_dangerous(self, interaction, button):
        if not await ensure_owner(interaction):
            return
        row = core.get_action(self.action_key)
        set_action_dangerous(self.action_key, not (row and row[2]))
        await interaction.response.edit_message(embed=action_registry_embed(interaction), view=self.back_target[1])

    async def set_use_level(self, interaction, level):
        if not await ensure_owner(interaction):
            return
        set_action_use_level(self.action_key, level)
        audit(interaction, "registry.use_level", "action", self.action_key, level)
        await interaction.response.edit_message(embed=action_registry_embed(interaction), view=self.back_target[1])

    @discord.ui.button(label="Жмёт: Member", row=2, style=discord.ButtonStyle.secondary)
    async def use_member(self, interaction, button):
        await self.set_use_level(interaction, "member")

    @discord.ui.button(label="Жмёт: Staff", row=2, style=discord.ButtonStyle.secondary)
    async def use_staff(self, interaction, button):
        await self.set_use_level(interaction, "staff")

    @discord.ui.button(label="Жмёт: Admin", row=2, style=discord.ButtonStyle.secondary)
    async def use_admin(self, interaction, button):
        await self.set_use_level(interaction, "admin")


# ============================================================
# КОМАНДЫ /access и /design
# ============================================================

def register_access(bot):
    @bot.tree.command(name="design", description="Настройка оформления бота")
    async def design(interaction):
        if not is_owner(interaction):
            await deny(interaction, "common.owner_only")
            return
        await interaction.response.send_message(embed=design_embed(interaction), view=DesignView(), ephemeral=True)

    @bot.tree.command(name="access", description="Управление доступом к функциям бота")
    @discord.app_commands.guild_only()
    async def access(interaction):
        if interaction.guild is None:
            await deny(interaction, "common.only_in_guild")
            return
        if not can_view_access(interaction):
            await deny(interaction, "text_access_denied")
            return

        embed = panel_embed(interaction, "access.home", level=level_label(get_user_level(interaction)))
        await interaction.response.send_message(embed=embed, view=AccessHomeView(bot), ephemeral=True)
