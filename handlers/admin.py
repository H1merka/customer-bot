# handlers/admin.py
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import StudioSetting, User, UserRole
from sqlalchemy import select

settings = get_settings()


async def check_if_admin(user_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором.
    Сначала сверяется со статическим списком в настройках .env, 
    затем делает запрос в базу данных.
    """
    if user_id in settings.admin_telegram_ids:
        return True
    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == user_id))
        if user and user.role == UserRole.ADMIN:
            return True
    return False


def build_admin_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Помощь", callback_data="admin_menu:help"),
            InlineKeyboardButton("Настройки", callback_data="admin_menu:settings")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_help_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Помощь", callback_data="admin_menu:help"),
            InlineKeyboardButton("Настройки", callback_data="admin_menu:settings")
        ],
        [InlineKeyboardButton("Назад", callback_data="admin_menu:back_to_start")]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_settings_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Выдать доступ к заживлению", callback_data="admin_setting:grant_healing")],
        [InlineKeyboardButton("Добавить админа", callback_data="admin_setting:add_admin")],
        [InlineKeyboardButton("Отозвать права админа", callback_data="admin_setting:revoke_admin")],
        [InlineKeyboardButton("Изменить адрес и геолокацию", callback_data="admin_setting:set_location")],
        [InlineKeyboardButton("Назад", callback_data="admin_menu:back_to_start")]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="admin_menu:settings")]])


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await update.effective_message.reply_text("У вас нет доступа к административным настройкам.")
        return

    await update.effective_message.reply_text(
        "Административные настройки студии:",
        reply_markup=build_admin_settings_menu()
    )


async def grant_healing_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy-команда быстрого доступа для обратной совместимости."""
    if update.effective_user is None or update.effective_message is None:
        return

    if not await check_if_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.effective_message.reply_text("Использование: /grant_access <telegram_id>")
        return

    try:
        telegram_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Telegram ID должен быть числом.")
        return

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id, username="unknown", full_name="Пользователь")
            session.add(user)
            await session.flush()

        user.has_healing_access = True
        await session.commit()

    await update.effective_message.reply_text("Доступ к инструкции по заживлению выдан.")


async def handle_admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    action = data.split(":", 1)[1]

    if action == "help":
        help_text = (
            "Административный интерфейс.\n\n"
            "Доступные разделы:\n"
            "• Настройки — управление доступом к инструкции по заживлению, добавление/удаление администраторов, изменение адреса и геолокации студии.\n"
            "• Помощь — описание доступного функционала."
        )
        await query.edit_message_text(help_text, reply_markup=build_admin_help_menu())
    elif action == "settings":
        await query.edit_message_text("Административные настройки студии:", reply_markup=build_admin_settings_menu())
    elif action == "back_to_start":
        context.user_data.pop("admin_state", None)
        await query.edit_message_text(
            "Добро пожаловать в административный интерфейс студии пирсинга.\nВыберите действие ниже.",
            reply_markup=build_admin_main_menu()
        )


async def handle_admin_setting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    setting_action = data.split(":", 1)[1]

    if setting_action == "grant_healing":
        context.user_data["admin_state"] = "await_userid_grant_healing"
        await query.edit_message_text(
            "Введите Telegram ID пользователя, которому хотите выдать доступ к инструкции по заживлению:",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "add_admin":
        context.user_data["admin_state"] = "await_userid_add_admin"
        await query.edit_message_text(
            "Введите Telegram ID пользователя, которого хотите назначить администратором:",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "revoke_admin":
        context.user_data["admin_state"] = "await_userid_revoke_admin"
        await query.edit_message_text(
            "Введите Telegram ID администратора, у которого хотите отозвать права:",
            reply_markup=build_admin_cancel_button()
        )
    elif setting_action == "set_location":
        context.user_data["admin_state"] = "await_location"
        await query.edit_message_text(
            "Отправьте геолокацию студии через Telegram (прикрепите геопозицию).",
            reply_markup=build_admin_cancel_button()
        )


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None or update.effective_message.text is None:
        return

    if not await check_if_admin(update.effective_user.id):
        return

    admin_state = context.user_data.get("admin_state")
    if not admin_state:
        return

    text = update.effective_message.text.strip()

    if admin_state in ["await_userid_grant_healing", "await_userid_add_admin", "await_userid_revoke_admin"]:
        try:
            target_id = int(text)
        except ValueError:
            await update.effective_message.reply_text(
                "Пожалуйста, введите корректный числовой Telegram ID пользователя.",
                reply_markup=build_admin_cancel_button()
            )
            return

        async with AsyncSessionFactory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == target_id))
            
            if user is None:
                # Если пользователя нет в БД, создаем каркас для возможности выдачи прав
                user = User(
                    telegram_id=target_id,
                    username="unknown",
                    full_name="Пользователь",
                )
                session.add(user)
                await session.flush()

            if admin_state == "await_userid_grant_healing":
                user.has_healing_access = True
                success_text = f"Доступ к инструкции по заживлению успешно выдан пользователю {target_id}."
            elif admin_state == "await_userid_add_admin":
                user.role = UserRole.ADMIN
                success_text = f"Пользователь {target_id} успешно назначен администратором."
            elif admin_state == "await_userid_revoke_admin":
                user.role = UserRole.USER
                success_text = f"Права администратора у пользователя {target_id} успешно отозваны."

            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            success_text + "\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu()
        )
        return

    if admin_state == "await_address":
        latitude = context.user_data.pop("temp_latitude", None)
        longitude = context.user_data.pop("temp_longitude", None)

        if latitude is None or longitude is None:
            await update.effective_message.reply_text(
                "Произошла ошибка: координаты не найдены. Попробуйте начать заново.",
                reply_markup=build_admin_settings_menu()
            )
            context.user_data.pop("admin_state", None)
            return

        async with AsyncSessionFactory() as session:
            for key, val in [("latitude", str(latitude)), ("longitude", str(longitude)), ("address_text", text)]:
                setting = await session.get(StudioSetting, key)
                if setting:
                    setting.value = val
                else:
                    session.add(StudioSetting(key=key, value=val))
            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Геолокация и текстовый адрес студии успешно обновлены.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu()
        )
        return


async def handle_admin_location_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None or update.effective_message.location is None:
        return

    if not await check_if_admin(update.effective_user.id):
        return

    admin_state = context.user_data.get("admin_state")
    if admin_state != "await_location":
        return

    loc = update.effective_message.location
    context.user_data["temp_latitude"] = loc.latitude
    context.user_data["temp_longitude"] = loc.longitude
    context.user_data["admin_state"] = "await_address"

    await update.effective_message.reply_text(
        "Координаты зафиксированы. Теперь введите текстовый адрес студии:",
        reply_markup=build_admin_cancel_button()
    )


admin_handlers = [
    CallbackQueryHandler(handle_admin_menu_callback, pattern=r"^admin_menu:"),
    CallbackQueryHandler(handle_admin_setting_callback, pattern=r"^admin_setting:"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input),
    MessageHandler(filters.LOCATION, handle_admin_location_input),
]
