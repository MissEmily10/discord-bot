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
"""

import io
import time
from datetime import datetime

import discord

import core
from core import (
    ACCESS_LABELS, ACCESS_LEVELS, PanelView, level_value,
    is_owner, get_user_level, can_manage_access, can_view_access,
    can_assign_level, embed_color, danger_color, get_setting, set_setting,
    ensure_default_actions, get_actions, is_action_allowed,
)
from database import (
    add_command_access, get_command_access, get_command_access_details,
    remove_command_access, set_access_level, get_access_level,
    get_member_info, set_role_access, remove_role_access, get_all_role_access,
    deny_user, is_user_denied, undeny_user, get_denied_users,
    set_action_enabled, set_action_min_level,
)


def E(interaction, title, description=""):
    return discord.Embed(title=title, description=description, color=embed_color(
        interaction.guild.id if interaction.guild else None
    ))


def format_expiration(expires_at):
    if expires_at is None:
        return "Постоянный"
    return datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y %H:%M")


def get_bot_commands(bot):
    return [
        (command.name, command.description or "Без описания")
        for command in bot.tree.get_commands()
    ]


# ============================================================
# ГЛАВНЫЙ ЭКРАН
# ============================================================

class AccessHomeView(PanelView):

    def __init__(self, bot):
        super().__init__(back_target=None)
        self.bot = bot

    @discord.ui.button(label="Пользователи", emoji="👥", style=discord.ButtonStyle.secondary)
    async def users_button(self, interaction, button):
        if not can_view_access(interaction):
            await interaction.response.send_message(get_setting(interaction.guild.id, "text_access_denied"), ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "ПОЛЬЗОВАТЕЛИ", "Выберите пользователя для просмотра и изменения уровня доступа."),
            view=UserManagementView(back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Команды", emoji="🧩", style=discord.ButtonStyle.primary)
    async def commands_button(self, interaction, button):
        if not can_view_access(interaction):
            await interaction.response.send_message(get_setting(interaction.guild.id, "text_access_denied"), ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "КОМАНДЫ", "Выберите команду для просмотра и настройки доступа."),
            view=CommandSelectView(self.bot, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Доступ по ролям", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def role_access_button(self, interaction, button):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Нужны права администратора.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "ДОСТУП ПО РОЛЯМ", "Выберите Discord-роль и назначьте ей уровень доступа."),
            view=RoleAccessView(back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Запреты", emoji="🚫", style=discord.ButtonStyle.danger)
    async def denials_button(self, interaction, button):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Нужны права администратора.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=denial_list_embed(interaction),
            view=DenialAccessView(interaction, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="⚙️ Настройки", style=discord.ButtonStyle.secondary, row=1)
    async def settings_button(self, interaction, button):
        if not is_owner(interaction):
            await interaction.response.send_message("Настройки доступны только владельцу.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "⚙️ БУНКЕР", "Owner-only инструменты бота."),
            view=SettingsView(self.bot, back_target=(interaction.message.embeds[0], self))
        )


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

class UserManagementView(PanelView):

    def __init__(self, back_target):
        super().__init__(back_target=back_target)
        self.user_select = discord.ui.UserSelect(placeholder="Выберите пользователя", min_values=1, max_values=1)
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

    async def user_selected(self, interaction):
        user = self.user_select.values[0]
        level, expires_at = get_member_info(interaction.guild.id, user.id)

        embed = E(
            interaction, "ПОЛЬЗОВАТЕЛЬ",
            f"**Пользователь:** {user.mention}\n\n"
            f"**Уровень:** {ACCESS_LABELS.get(level, level)}\n"
            f"**Действует до:** {format_expiration(expires_at)}"
        )
        await interaction.response.edit_message(
            embed=embed,
            view=UserLevelView(user.id, user.mention, back_target=(interaction.message.embeds[0], self))
        )


class UserLevelView(PanelView):
    """Выбор уровня. Дальше — отдельный экран длительности (без слова 'временный')."""

    def __init__(self, user_id, user_mention, back_target):
        super().__init__(back_target=back_target)
        self.user_id = user_id
        self.user_mention = user_mention

    async def go_duration(self, interaction, level):
        if not can_assign_level(interaction, level):
            await interaction.response.send_message("У вас недостаточно прав для назначения этого уровня.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "СРОК ДЕЙСТВИЯ", f"{self.user_mention} → {ACCESS_LABELS[level]}\n\nНа какой срок?"),
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


class LevelDurationCustomModal(discord.ui.Modal, title="СВОЙ СРОК"):
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
            await interaction.response.send_message("Введи целое число часов больше нуля.", ephemeral=True)
            return

        expires_at = int(time.time() + hours * 3600)
        set_access_level(interaction.guild.id, self.user_id, self.level, expires_at)
        await interaction.response.send_message(
            f"{self.user_mention} → {ACCESS_LABELS[self.level]} на {hours} ч.", ephemeral=True
        )


class LevelDurationView(PanelView):

    def __init__(self, user_id, user_mention, level, back_target):
        super().__init__(back_target=back_target)
        self.user_id = user_id
        self.user_mention = user_mention
        self.level = level

    async def apply(self, interaction, seconds):
        expires_at = int(time.time() + seconds) if seconds else None
        set_access_level(interaction.guild.id, self.user_id, self.level, expires_at)
        await interaction.response.send_message(
            f"{self.user_mention} → {ACCESS_LABELS[self.level]} "
            f"({'навсегда' if seconds is None else format_expiration(expires_at)})",
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
    ) or "Индивидуального доступа нет."

    required = core.COMMAND_MIN_LEVELS.get(command_name)
    level_text = ACCESS_LABELS.get(required, "Не задан")

    return E(
        interaction, "COMMAND ACCESS",
        f"**Команда:** `/{command_name}`\n**Описание:** {description}\n\n"
        f"**Минимальный уровень:** {level_text}\n\n**Индивидуальный доступ:**\n{access_text}"
    )


class CommandAccessView(PanelView):
    """
    Все действия обновляют ЭТУ ЖЕ панель через edit_message — список
    сразу видно свежим, без отдельных всплывающих сообщений.
    """

    def __init__(self, command_name, description, back_target):
        super().__init__(back_target=back_target)
        self.command_name = command_name
        self.description = description

    async def refresh(self, interaction):
        await interaction.response.edit_message(
            embed=command_access_embed(interaction, self.command_name, self.description),
            view=self
        )

    @discord.ui.button(label="Добавить", emoji="➕", style=discord.ButtonStyle.success)
    async def add_user(self, interaction, button):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Нужны права администратора.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "ДОБАВИТЬ ДОСТУП", f"Выберите пользователя для `/{self.command_name}`."),
            view=CommandUserPickView(self.command_name, self.description, "add", back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Убрать", emoji="➖", style=discord.ButtonStyle.danger)
    async def remove_user(self, interaction, button):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Нужны права администратора.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=E(interaction, "УБРАТЬ ДОСТУП", f"Выберите пользователя, у которого нужно убрать доступ к `/{self.command_name}`."),
            view=CommandUserPickView(self.command_name, self.description, "remove", back_target=(interaction.message.embeds[0], self))
        )


class CommandUserPickView(PanelView):

    def __init__(self, command_name, description, mode, back_target):
        super().__init__(back_target=back_target)
        self.command_name = command_name
        self.description = description
        self.mode = mode
        self.user_select = discord.ui.UserSelect(placeholder="Выберите пользователя", min_values=1, max_values=1)
        self.user_select.callback = self.user_selected
        self.add_item(self.user_select)

    async def user_selected(self, interaction):
        user = self.user_select.values[0]

        if self.mode == "remove":
            remove_command_access(interaction.guild.id, self.command_name, user.id)
            await interaction.response.edit_message(
                embed=command_access_embed(interaction, self.command_name, self.description),
                view=CommandAccessView(self.command_name, self.description, back_target=self.back_target)
            )
            return

        # add -> спрашиваем срок
        await interaction.response.edit_message(
            embed=E(interaction, "СРОК ДОСТУПА", f"На какой срок выдать доступ к `/{self.command_name}` для {user.mention}?"),
            view=CommandDurationView(self.command_name, self.description, user.id, user.mention, back_target=(interaction.message.embeds[0], self))
        )


class CommandDurationView(PanelView):

    def __init__(self, command_name, description, user_id, user_mention, back_target):
        super().__init__(back_target=back_target)
        self.command_name = command_name
        self.description = description
        self.user_id = user_id
        self.user_mention = user_mention

    async def apply(self, interaction, seconds):
        expires_at = int(time.time() + seconds) if seconds else None
        add_command_access(interaction.guild.id, self.command_name, self.user_id, expires_at)
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
        select = discord.ui.RoleSelect(placeholder="Выберите роль", min_values=1, max_values=1)
        select.callback = self.selected
        self.add_item(select)

    async def selected(self, interaction):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Нужны права администратора.", ephemeral=True)
            return
        role = self.children[0].values[0]
        await interaction.response.edit_message(
            embed=E(interaction, "ДОСТУП РОЛИ", f"{role.mention}\n\nВыберите уровень доступа для обладателей этой роли."),
            view=RoleLevelAccessView(role.id, role.mention, back_target=(interaction.message.embeds[0], self))
        )


class RoleLevelAccessView(PanelView):

    def __init__(self, role_id, mention, back_target):
        super().__init__(back_target=back_target)
        self.role_id = role_id
        self.mention = mention

    def embed(self, interaction):
        return E(
            interaction,
            "ДОСТУП РОЛИ",
            f"{self.mention}\n\nВыберите уровень доступа для обладателей этой роли.",
        )

    async def apply(self, interaction, level):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return
        set_role_access(interaction.guild.id, self.role_id, level, None)
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
            await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
            return
        remove_role_access(interaction.guild.id, self.role_id)
        await interaction.response.edit_message(
            embed=self.embed(interaction),
            view=self,
        )


# ============================================================
# ЗАПРЕТЫ (deny) — с подтверждением
# ============================================================

def denial_list_embed(interaction):
    rows = get_denied_users(interaction.guild.id)
    text = "\n".join(f"<@{uid}> — {reason or 'без причины'}" for uid, reason, _ in rows) or "Никто не забанен."
    return E(interaction, "ЗАПРЕТ ДОСТУПА", f"Запрет полностью блокирует использование бота пользователем.\n\n{text}")


class DenialAccessView(PanelView):

    def __init__(self, interaction, back_target):
        super().__init__(back_target=back_target)
        select = discord.ui.UserSelect(placeholder="Выберите пользователя", min_values=1, max_values=1)
        select.callback = self.selected
        self.add_item(select)

    async def selected(self, interaction):
        if not can_manage_access(interaction):
            await interaction.response.send_message("Нужны права администратора.", ephemeral=True)
            return
        user = self.children[0].values[0]
        reason = is_user_denied(interaction.guild.id, user.id)

        if reason is not None:
            # уже забанен — снимаем сразу, это не опасное действие
            undeny_user(interaction.guild.id, user.id)
            await interaction.response.edit_message(
                embed=denial_list_embed(interaction),
                view=DenialAccessView(interaction, back_target=self.back_target)
            )
            return

        # бан — требует подтверждения
        await interaction.response.edit_message(
            embed=E(interaction, "⚠️ ПОДТВЕРДИ ЗАПРЕТ", f"{user.mention} будет полностью заблокирован в боте. Продолжить?"),
            view=DenyConfirmView(user.id, user.mention, back_target=(interaction.message.embeds[0], self))
        )


class DenyConfirmView(PanelView):

    def __init__(self, user_id, user_mention, back_target):
        super().__init__(back_target=back_target)
        self.user_id = user_id
        self.user_mention = user_mention

    @discord.ui.button(label="Подтвердить запрет", emoji="🚫", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        deny_user(interaction.guild.id, self.user_id, "Access Manager")
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

class SettingsView(PanelView):

    def __init__(self, bot, back_target):
        super().__init__(back_target=back_target)
        self.bot = bot

    async def ensure_owner(self, interaction):
        if is_owner(interaction):
            return True
        await interaction.response.send_message("Настройки доступны только владельцу.", ephemeral=True)
        return False

    @discord.ui.button(label="Бэкап bot.db", emoji="💾", style=discord.ButtonStyle.secondary)
    async def backup(self, interaction, button):
        if not await self.ensure_owner(interaction):
            return
        try:
            await interaction.response.send_message(
                file=discord.File("bot.db", filename=f"bot_backup_{int(time.time())}.db"),
                ephemeral=True
            )
        except FileNotFoundError:
            await interaction.response.send_message("bot.db не найден.", ephemeral=True)

    @discord.ui.button(label="Денаи", emoji="🚫", style=discord.ButtonStyle.secondary)
    async def denials(self, interaction, button):
        if not await self.ensure_owner(interaction):
            return
        await interaction.response.edit_message(
            embed=denial_list_embed(interaction),
            view=DenialAccessView(interaction, back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Action Registry", emoji="🔑", style=discord.ButtonStyle.primary)
    async def registry(self, interaction, button):
        if not await self.ensure_owner(interaction):
            return
        ensure_default_actions()
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=ActionRegistryView(back_target=(interaction.message.embeds[0], self))
        )

    @discord.ui.button(label="Перезапуск", emoji="🔄", style=discord.ButtonStyle.danger)
    async def restart(self, interaction, button):
        if not await self.ensure_owner(interaction):
            return
        # Discord-бот не может сам себя перезапустить как процесс —
        # это должен делать внешний менеджер процессов (systemd/pm2/docker restart).
        # bot.close() корректно завершает соединение, а менеджер поднимает заново.
        await interaction.response.send_message("Останавливаю бота. Процесс-менеджер должен поднять его заново.", ephemeral=True)
        await self.bot.close()


DESIGN_DEFAULTS = {
    "embed_color": "0x5865F2",
    "danger_color": "0xED4245",
    "text_access_denied": "РЕПЛИКА ОС — ДОСТУП ЗАПРЕЩЁН",
    "nav_back": "◀️ Назад",
    "nav_cancel": "❌ Отмена",
}


def design_embed(interaction):
    guild_id = interaction.guild.id
    return E(
        interaction,
        "DESIGN",
        "Настройки оформления для этого сервера.\n\n"
        f"Основной цвет: `{get_setting(guild_id, 'embed_color')}`\n"
        f"Опасный цвет: `{get_setting(guild_id, 'danger_color')}`\n"
        f"Отказ в доступе: {get_setting(guild_id, 'text_access_denied')}\n"
        f"Назад: {get_setting(guild_id, 'nav_back')}\n"
        f"Отмена: {get_setting(guild_id, 'nav_cancel')}",
    )


class DesignModal(discord.ui.Modal, title="DESIGN SETTINGS"):
    embed_color_input = discord.ui.TextInput(label="Основной HEX цвет", max_length=8)
    danger_color_input = discord.ui.TextInput(label="Опасный HEX цвет", max_length=8)
    denied_input = discord.ui.TextInput(label="Текст отказа", max_length=200)
    back_input = discord.ui.TextInput(label="Текст кнопки Назад", max_length=80)
    cancel_input = discord.ui.TextInput(label="Текст кнопки Отмена", max_length=80)

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id
        self.embed_color_input.default = get_setting(guild_id, "embed_color")
        self.danger_color_input.default = get_setting(guild_id, "danger_color")
        self.denied_input.default = get_setting(guild_id, "text_access_denied")
        self.back_input.default = get_setting(guild_id, "nav_back")
        self.cancel_input.default = get_setting(guild_id, "nav_cancel")

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
            await interaction.response.send_message("Цвета должны быть в формате `#RRGGBB`.", ephemeral=True)
            return
        set_setting(self.guild_id, "embed_color", primary)
        set_setting(self.guild_id, "danger_color", danger)
        set_setting(self.guild_id, "text_access_denied", self.denied_input.value.strip() or DESIGN_DEFAULTS["text_access_denied"])
        set_setting(self.guild_id, "nav_back", self.back_input.value.strip() or DESIGN_DEFAULTS["nav_back"])
        set_setting(self.guild_id, "nav_cancel", self.cancel_input.value.strip() or DESIGN_DEFAULTS["nav_cancel"])
        await interaction.response.edit_message(embed=design_embed(interaction), view=DesignView())


class DesignView(PanelView):
    def __init__(self, back_target=None):
        super().__init__(back_target=back_target)

    async def ensure_owner(self, interaction):
        if is_owner(interaction):
            return True
        await interaction.response.send_message("Настройки доступны только владельцу.", ephemeral=True)
        return False

    @discord.ui.button(label="Изменить", emoji="🎨", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        if await self.ensure_owner(interaction):
            await interaction.response.send_modal(DesignModal(interaction.guild.id))

    @discord.ui.button(label="Предпросмотр", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        if await self.ensure_owner(interaction):
            await interaction.response.edit_message(embed=design_embed(interaction), view=self)

    @discord.ui.button(label="Сбросить", emoji="↩️", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        if not await self.ensure_owner(interaction):
            return
        for key, value in DESIGN_DEFAULTS.items():
            set_setting(interaction.guild.id, key, value)
        await interaction.response.edit_message(embed=design_embed(interaction), view=self)


def action_registry_embed(interaction):
    rows = get_actions()
    lines = []
    for key, min_level, dangerous, description, enabled in rows:
        status = "🟢" if enabled else "🔴"
        danger = "⚠️" if dangerous else ""
        lines.append(f"{status} `{key}` — {ACCESS_LABELS.get(min_level, min_level)} {danger}")
    text = "\n".join(lines) or "Реестр пуст."
    return E(interaction, "ACTION REGISTRY", f"Кто может создавать кнопки с каким действием.\n\n{text}")


class ActionRegistryView(PanelView):
    """Выбор конкретного action для правки — чтобы не городить 20 кнопок сразу."""

    def __init__(self, back_target):
        super().__init__(back_target=back_target)
        rows = get_actions()
        for key, min_level, dangerous, description, enabled in rows[:23]:
            button = discord.ui.Button(label=key, style=discord.ButtonStyle.secondary)

            async def callback(interaction, key=key):
                await interaction.response.edit_message(
                    embed=E(interaction, key, "Изменить минимальный уровень или включить/выключить действие."),
                    view=ActionEditView(key, back_target=(interaction.message.embeds[0], self))
                )

            button.callback = callback
            self.add_item(button)


class ActionEditView(PanelView):

    def __init__(self, action_key, back_target):
        super().__init__(back_target=back_target)
        self.action_key = action_key

    async def ensure_owner(self, interaction):
        if is_owner(interaction):
            return True
        await interaction.response.send_message("Настройки доступны только владельцу.", ephemeral=True)
        return False

    async def set_level(self, interaction, level):
        if not await self.ensure_owner(interaction):
            return
        set_action_min_level(self.action_key, level)
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
        if not await self.ensure_owner(interaction):
            return
        set_action_enabled(self.action_key, True)
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=self.back_target[1],
        )

    @discord.ui.button(label="Выключить", emoji="🔴", row=1, style=discord.ButtonStyle.danger)
    async def disable(self, interaction, button):
        if not await self.ensure_owner(interaction):
            return
        set_action_enabled(self.action_key, False)
        await interaction.response.edit_message(
            embed=action_registry_embed(interaction),
            view=self.back_target[1],
        )


# ============================================================
# КОМАНДА /access
# ============================================================

def register_access(bot):
    @bot.tree.command(name="design", description="Настройка оформления бота")
    async def design(interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("Настройки доступны только владельцу.", ephemeral=True)
            return
        await interaction.response.send_message(embed=design_embed(interaction), view=DesignView(), ephemeral=True)

    @bot.tree.command(name="access", description="Управление доступом к функциям бота")
    async def access(interaction):
        if not can_view_access(interaction):
            await interaction.response.send_message(get_setting(interaction.guild.id, "text_access_denied"), ephemeral=True)
            return

        access_level = get_user_level(interaction)
        embed = E(
            interaction, "ACCESS MANAGER",
            f"Ваш уровень: {ACCESS_LABELS.get(access_level)}\n\nУправление пользователями, уровнями и доступом к командам."
        )
        if bot.user:
            embed.set_thumbnail(url=bot.user.display_avatar.url)

        await interaction.response.send_message(embed=embed, view=AccessHomeView(bot), ephemeral=True)
