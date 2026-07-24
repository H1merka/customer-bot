# handlers/admin.py
from __future__ import annotations

import html
import logging
from datetime import UTC, date, datetime, time, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Прямой импорт из constants
from config.constants import (
    CUSTOM_TEXT_LABELS,
    DEFAULT_CUSTOM_TEXTS,
    DEFAULT_STAGE_TEXTS,
    PIERCING_ZONES,
    STAGE_LABELS,
)
from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import (
    Booking,
    BookingStatus,
    Broadcast,
    DayOff,
    MediaTemplate,
    StudioSetting,
    User,
    UserRole,
)
from scheduler.jobs import run_broadcast_job
from services.google_calendar import GoogleCalendarService

logger = logging.getLogger(__name__)
settings = get_settings()


async def check_if_admin(
    user_id: int, context: ContextTypes.DEFAULT_TYPE | None = None
) -> bool:
    if user_id in settings.admin_telegram_ids:
        return True

    if context and context.user_data and context.user_data.get("is_admin") is True:
        return True

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == user_id))
        if user and user.role == UserRole.ADMIN:
            if context and context.user_data:
                context.user_data["is_admin"] = True
            return True
    return False


def build_admin_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Помощь", callback_data="admin_menu:help"),
            InlineKeyboardButton("Настройки", callback_data="admin_menu:settings"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_help_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Помощь", callback_data="admin_menu:help"),
            InlineKeyboardButton("Настройки", callback_data="admin_menu:settings"),
        ],
        [InlineKeyboardButton("Назад", callback_data="admin_menu:back_to_start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_settings_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "Выдать доступ к заживлению",
                callback_data="admin_setting:grant_healing",
            )
        ],
        [
            InlineKeyboardButton(
                "Обновить текст инструкции по заживлению",
                callback_data="admin_setting:update_healing",
            )
        ],
        [
            InlineKeyboardButton(
                "Добавить админа", callback_data="admin_setting:add_admin"
            )
        ],
        [
            InlineKeyboardButton(
                "Отозвать права админа", callback_data="admin_setting:revoke_admin"
            )
        ],
        [
            InlineKeyboardButton(
                "Изменить адрес и геолокацию",
                callback_data="admin_setting:set_location",
            )
        ],
        [
            InlineKeyboardButton(
                "Добавить выходные", callback_data="admin_setting:add_days_off"
            )
        ],
        [
            InlineKeyboardButton(
                "Настройка интерфейса пользователя",
                callback_data="admin_setting:ui_config",
            )
        ],
        [
            InlineKeyboardButton(
                "Добавить рассылку",
                callback_data="admin_setting:add_broadcast",
            )
        ],
        [InlineKeyboardButton("Назад", callback_data="admin_menu:back_to_start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Назад", callback_data="admin_menu:settings")]]
    )


def build_zones_keyboard(prefix: str, back_callback: str) -> InlineKeyboardMarkup:
    keyboard = []
    for zone_key, zone_info in PIERCING_ZONES.items():
        keyboard.append(
            [
                InlineKeyboardButton(
                    zone_info["name"], callback_data=f"{prefix}:{zone_key}"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(keyboard)


def build_healing_zones_keyboard(
    prefix: str, back_callback: str
) -> InlineKeyboardMarkup:
    """Клавиатура выбора зон для управления инструкциями заживления."""
    keyboard = []
    for zone_key, zone_info in PIERCING_ZONES.items():
        keyboard.append(
            [
                InlineKeyboardButton(
                    zone_info["name"], callback_data=f"{prefix}:{zone_key}"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(keyboard)


def build_healing_types_keyboard(
    zone_key: str, prefix: str, back_callback: str
) -> InlineKeyboardMarkup:
    """Клавиатура выбора проколов выбранной зоны, передавая индекс во избежание Button_data_invalid."""
    zone_info = PIERCING_ZONES.get(zone_key)
    if not zone_info:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)]]
        )

    keyboard = []
    for idx, type_name in enumerate(zone_info["types"]):
        keyboard.append(
            [
                InlineKeyboardButton(
                    type_name, callback_data=f"{prefix}:{zone_key}:{idx}"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(keyboard)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await update.effective_message.reply_text(
            "У вас нет доступа к административным настройкам."
        )
        return

    await update.effective_message.reply_text(
        "Административные настройки студии:", reply_markup=build_admin_settings_menu()
    )


async def grant_healing_access(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_user is None or update.effective_message is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        return

    args = context.args
    if not args:
        await update.effective_message.reply_text(
            "Использование: /grant_access <telegram_id>"
        )
        return

    try:
        telegram_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Telegram ID должен быть числом.")
        return

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(
                telegram_id=telegram_id, username="unknown", full_name="Пользователь"
            )
            session.add(user)
            await session.flush()

        user.has_healing_access = True
        await session.commit()

    await update.effective_message.reply_text(
        "Доступ к инструкции по заживлению выдан."
    )


async def handle_admin_menu_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
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
        context.user_data.pop("admin_state", None)
        context.user_data.pop("temp_days_off_dates", None)
        await query.edit_message_text(
            "Административные настройки студии:",
            reply_markup=build_admin_settings_menu(),
        )
    elif action == "back_to_start":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("temp_days_off_dates", None)
        await query.edit_message_text(
            "Добро пожаловать в административный интерфейс студии пирсинга.\nВыберите действие ниже.",
            reply_markup=build_admin_main_menu(),
        )


async def handle_admin_setting_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    setting_action = data.split(":", 1)[1]

    if setting_action == "grant_healing":
        context.user_data["admin_state"] = "await_userid_grant_healing"
        await query.edit_message_text(
            "Введите Telegram ID пользователя, которому хотите выдать доступ к инструкции по заживлению:",
            reply_markup=build_admin_cancel_button(),
        )
    elif setting_action == "update_healing":
        await query.edit_message_text(
            "Управление инструкциями по заживлению.\n\nВыберите интересующую зону проколов:",
            reply_markup=build_healing_zones_keyboard(
                "admin_healing:select_zone", "admin_menu:settings"
            ),
        )
    elif setting_action == "add_admin":
        context.user_data["admin_state"] = "await_userid_add_admin"
        await query.edit_message_text(
            "Введите Telegram ID пользователя, которого хотите назначить администратором:",
            reply_markup=build_admin_cancel_button(),
        )
    elif setting_action == "revoke_admin":
        context.user_data["admin_state"] = "await_userid_revoke_admin"
        await query.edit_message_text(
            "Введите Telegram ID администратора, у которого хотите отозвать права:",
            reply_markup=build_admin_cancel_button(),
        )
    elif setting_action == "set_location":
        context.user_data["admin_state"] = "await_location"
        await query.edit_message_text(
            "Отправьте геолокацию студии через Telegram (прикрепите геопозицию).",
            reply_markup=build_admin_cancel_button(),
        )
    elif setting_action == "add_days_off":
        context.user_data["admin_state"] = "await_days_off"
        await query.edit_message_text(
            "Введите даты выходных в формате ДД.ММ.ГГГГ через пробел (например, 25.07.2026 26.07.2026):",
            reply_markup=build_admin_cancel_button(),
        )
    elif setting_action == "ui_config":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("edit_zone_key", None)
        context.user_data.pop("edit_text_key", None)

        keyboard = [
            [
                InlineKeyboardButton("Изображения", callback_data="admin_ui:images"),
                InlineKeyboardButton("Текст", callback_data="admin_ui:texts"),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_menu:settings")],
        ]
        await query.edit_message_text(
            "Настройка пользовательского интерфейса (UI):\n\nВыберите интересующий раздел:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif setting_action == "add_broadcast":
        context.user_data["admin_state"] = "await_broadcast_text"
        await query.edit_message_text(
            "Введите текст сообщения для рассылки всем пользователям (как обычный текст без форматирования):",
            reply_markup=build_admin_cancel_button(),
        )


async def handle_admin_ui_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    action = data.split(":", 1)[1]

    if action == "images":
        keyboard = [
            [
                InlineKeyboardButton(
                    "Изменить", callback_data="admin_ui_img:change_menu"
                ),
                InlineKeyboardButton(
                    "Удалить", callback_data="admin_ui_img:delete_menu"
                ),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_setting:ui_config")],
        ]
        await query.edit_message_text(
            "Управление изображениями зон проколов:\n\nВыберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif action == "texts":
        keyboard = [
            [
                InlineKeyboardButton(
                    "Этапы сценария записи", callback_data="admin_ui_txt_cat:stages"
                )
            ],
            [
                InlineKeyboardButton(
                    "Служебные сообщения бота", callback_data="admin_ui_txt_cat:service"
                )
            ],
            [
                InlineKeyboardButton(
                    "Медицинские вопросы", callback_data="admin_ui_txt_cat:medical"
                )
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_setting:ui_config")],
        ]
        await query.edit_message_text(
            "Управление текстами сообщений:\n\nВыберите необходимую категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif action == "back_to_ui":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("edit_zone_key", None)
        context.user_data.pop("edit_text_key", None)

        keyboard = [
            [
                InlineKeyboardButton("Изображения", callback_data="admin_ui:images"),
                InlineKeyboardButton("Текст", callback_data="admin_ui:texts"),
            ],
            [InlineKeyboardButton("Назад", callback_data="admin_menu:settings")],
        ]
        await query.edit_message_text(
            "Настройка пользовательского интерфейса (UI):",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_admin_ui_txt_cat_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    category = data.split(":", 1)[1]

    if category == "stages":
        keyboard = []
        for stage_key, label in STAGE_LABELS.items():
            keyboard.append(
                [
                    InlineKeyboardButton(
                        label, callback_data=f"admin_ui_txt:select:{stage_key}"
                    )
                ]
            )
        keyboard.append(
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_setting:ui_config")]
        )
        await query.edit_message_text(
            "Управление текстами этапов записи:\n\n"
            "Выберите этап для настройки его текста:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif category == "service":
        keyboard = [
            [
                InlineKeyboardButton(
                    "Приветственное сообщение",
                    callback_data="admin_ui_txt:select_custom:welcome",
                )
            ],
            [
                InlineKeyboardButton(
                    "Отказ в инструкции заживления",
                    callback_data="admin_ui_txt:select_custom:healing_denied",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вызов пирсера (из меню)",
                    callback_data="admin_ui_txt:select_custom:support_summon",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вызов специалиста (мед. анкета)",
                    callback_data="admin_ui_txt:select_custom:specialist_review",
                )
            ],
            [
                InlineKeyboardButton(
                    "Подтверждение записи",
                    callback_data="admin_ui_txt:select_custom:booking_confirmed",
                )
            ],
            [
                InlineKeyboardButton(
                    "Инструкция по предоплате",
                    callback_data="admin_ui_txt:select_custom:prepayment_info",
                )
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_setting:ui_config")],
        ]
        await query.edit_message_text(
            "Управление служебными сообщениями:\n\nВыберите сообщение для настройки:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif category == "medical":
        keyboard = [
            [
                InlineKeyboardButton(
                    "Вопрос 1: Заболевания крови",
                    callback_data="admin_ui_txt:select_custom:medical_q1",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вопрос 2: Свертываемость",
                    callback_data="admin_ui_txt:select_custom:medical_q2",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вопрос 3: Принимаемые лекарства",
                    callback_data="admin_ui_txt:select_custom:medical_q3",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вопрос 4: Хронические заболевания",
                    callback_data="admin_ui_txt:select_custom:medical_q4",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вопрос 5: Заживление ран",
                    callback_data="admin_ui_txt:select_custom:medical_q5",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вопрос 6: Кожные заболевания",
                    callback_data="admin_ui_txt:select_custom:medical_q6",
                )
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_setting:ui_config")],
        ]
        await query.edit_message_text(
            "Управление вопросами медицинской анкеты:\n\n"
            "Выберите вопрос для редактирования текста:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_admin_ui_img_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    sub_action = data.split(":", 1)[1]

    if sub_action == "change_menu":
        await query.edit_message_text(
            "Выберите зону, для которой хотите изменить или добавить изображение:",
            reply_markup=build_zones_keyboard(
                "admin_ui_img:change_zone", "admin_ui:images"
            ),
        )
    elif sub_action == "delete_menu":
        await query.edit_message_text(
            "Выберите зону, для которой хотите удалить изображение:",
            reply_markup=build_zones_keyboard(
                "admin_ui_img:delete_zone", "admin_ui:images"
            ),
        )
    elif sub_action.startswith("change_zone:"):
        zone_key = sub_action.split(":", 1)[1]
        zone_name = PIERCING_ZONES.get(zone_key, {}).get("name", zone_key)
        context.user_data["admin_state"] = "await_zone_image_upload"
        context.user_data["edit_zone_key"] = zone_key

        await query.edit_message_text(
            f"Вы выбрали изменение изображения для зоны: <b>{zone_name}</b>.\n\n"
            f"Пожалуйста, прикрепите и отправьте новое изображение для этой зоны в чат (как фото):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Отмена", callback_data="admin_ui:images")]]
            ),
            parse_mode="HTML",
        )
    elif sub_action.startswith("delete_zone:"):
        zone_key = sub_action.split(":", 1)[1]
        zone_name = PIERCING_ZONES.get(zone_key, {}).get("name", zone_key)

        async with AsyncSessionFactory() as session:
            db_media = await session.scalar(
                select(MediaTemplate).where(MediaTemplate.key == f"zone_img:{zone_key}")
            )
            if db_media:
                db_media.telegram_file_id = None
            else:
                session.add(
                    MediaTemplate(key=f"zone_img:{zone_key}", telegram_file_id=None)
                )
            await session.commit()

        await query.answer(f"Изображение для зоны {zone_name} удалено.")
        await query.edit_message_text(
            f"Изображение для зоны <b>{zone_name}</b> успешно удалено из базы данных. "
            f"Теперь для этой зоны бот будет использовать только текстовый режим отображения.\n\n"
            f"Возврат в меню управления медиафайлами.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Назад", callback_data="admin_ui:images")]]
            ),
            parse_mode="HTML",
        )


async def handle_admin_ui_txt_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    sub_action = data.split(":", 1)[1]

    if sub_action.startswith("select:"):
        stage_key = sub_action.split(":", 1)[1]
        stage_label = STAGE_LABELS.get(stage_key, stage_key)

        async with AsyncSessionFactory() as session:
            template = await session.scalar(
                select(MediaTemplate).where(
                    MediaTemplate.key == f"stage_txt:{stage_key}"
                )
            )
            current_text = (
                template.value
                if template and template.value
                else DEFAULT_STAGE_TEXTS.get(stage_key, "")
            )

        placeholder_info = ""
        if stage_key == "await_tg_link_zone":
            placeholder_info = (
                "⚠️ <b>Доступные динамические плейсхолдеры:</b>\n"
                "- <code>{{service_name}}</code> (название услуги)\n"
                "- <code>{{zone_name}}</code> (выбранная зона)\n"
                "- <code>{{type_name}}</code> (выбранный тип прокола)"
            )
        elif stage_key == "await_tg_link_simple":
            placeholder_info = (
                "⚠️ <b>Доступные динамические плейсхолдеры:</b>\n"
                "- <code>{{service_name}}</code> (название услуги)"
            )
        elif stage_key in ["select_slot", "no_slots"]:
            placeholder_info = (
                "⚠️ <b>Доступные динамические плейсхолдеры:</b>\n"
                "- <code>{{date_str}}</code> (дата бронирования)"
            )
        elif stage_key == "await_date":
            placeholder_info = (
                "⚠️ <b>Доступные динамические плейсхолдеры:</b>\n"
                "- <code>{{start_date}}</code> (дата начала записи)\n"
                "- <code>{{end_date}}</code> (дата окончания записи)"
            )
        else:
            placeholder_info = "Для этого этапа динамические плейсхолдеры отсутствуют."

        context.user_data["admin_state"] = "await_ui_text_update"
        context.user_data["edit_text_key"] = f"stage_txt:{stage_key}"

        await query.edit_message_text(
            f"Редактирование текста для этапа: <b>{stage_label}</b>\n\n"
            f"📝 <b>Текущий текст сообщения:</b>\n"
            f"<blockquote>{html.escape(current_text)}</blockquote>\n\n"
            f"{placeholder_info}\n\n"
            f"Пожалуйста, отправьте новый текст сообщения в ответ на этот запрос:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Отмена", callback_data="admin_setting:ui_config"
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )

    elif sub_action.startswith("select_custom:"):
        custom_key = sub_action.split(":", 1)[1]
        label = CUSTOM_TEXT_LABELS.get(custom_key, custom_key)
        default_text = DEFAULT_CUSTOM_TEXTS.get(custom_key, "")

        async with AsyncSessionFactory() as session:
            template = await session.scalar(
                select(MediaTemplate).where(
                    MediaTemplate.key == f"custom_txt:{custom_key}"
                )
            )
            current_text = (
                template.value if template and template.value else default_text
            )

        placeholder_info = ""
        if custom_key == "booking_confirmed":
            placeholder_info = (
                "⚠️ <b>Доступные динамические плейсхолдеры:</b>\n"
                "- <code>{{slot_display}}</code> (дата и время сеанса)\n"
                "- <code>{{address_text}}</code> (адрес студии)"
            )
        else:
            placeholder_info = (
                "Для этого сообщения динамические плейсхолдеры отсутствуют."
            )

        context.user_data["admin_state"] = "await_ui_text_update"
        context.user_data["edit_text_key"] = f"custom_txt:{custom_key}"

        await query.edit_message_text(
            f"Редактирование сообщения: <b>{label}</b>\n\n"
            f"📝 <b>Текущий текст сообщения:</b>\n"
            f"<blockquote>{html.escape(current_text)}</blockquote>\n\n"
            f"{placeholder_info}\n\n"
            f"Пожалуйста, отправьте новый текст сообщения в ответ на этот запрос:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Отмена", callback_data="admin_setting:ui_config"
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )


async def handle_admin_prepayment_approval(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Одобрение предоплаты администратором с авторедактированием клиентского интерфейса (Вариант А)."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    client_id = int(data.split(":", 1)[1])

    async with AsyncSessionFactory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == client_id))
        if user:
            user.is_prepaid = True
            await session.commit()
        else:
            await query.answer("Пользователь не найден в базе данных.", show_alert=True)
            return

    admin_username = (
        f"@{update.effective_user.username}"
        if update.effective_user.username
        else update.effective_user.full_name
    )
    await query.answer("Предоплата подтверждена!")

    original_text = query.message.text if query.message else "Запрос на предоплату"
    await query.edit_message_text(
        text=f"✅ {original_text}\n\n<b>[ОДОБРЕНО]</b> Администратор {admin_username} подтвердил получение предоплаты.",
        reply_markup=None,
        parse_mode="HTML",
    )

    # Динамический импорт во избежание круговых импортов на старте системы
    from handlers.client import (
        build_back_button,
        get_allowed_booking_range,
        get_stage_text,
    )

    start_date, end_date = get_allowed_booking_range()
    start_str = start_date.strftime("%d.%m.%Y")
    end_str = end_date.strftime("%d.%m.%Y")

    await_date_text = await get_stage_text(
        "await_date", start_date=start_str, end_date=end_str
    )
    client_text = f"✅ <b>Предоплата подтверждена!</b>\n\n{await_date_text}"
    client_keyboard = build_back_button()

    client_user_data = (
        context.application.user_data.get(client_id) if context.application else None
    )
    edited_successfully = False

    if client_user_data:
        prepay_msg_id = client_user_data.get("prepayment_message_id")
        prepay_chat_id = client_user_data.get("prepayment_chat_id")
        if prepay_msg_id and prepay_chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=prepay_chat_id,
                    message_id=prepay_msg_id,
                    text=client_text,
                    reply_markup=client_keyboard,
                    parse_mode="HTML",
                )
                edited_successfully = True

                client_user_data["booking_state"] = "await_date"
                client_user_data.setdefault("history", []).append("await_prepayment")
                logger.info(
                    "Successfully automatically edited client %s's screen to await_date",
                    client_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to edit prepayment message for user %s, falling back to new message: %s",
                    client_id,
                    exc,
                )

    if not edited_successfully:
        chat_id = client_id
        topic_id = None

        async with AsyncSessionFactory() as session:
            latest_booking = await session.scalar(
                select(Booking)
                .where(Booking.user_id == client_id)
                .order_by(Booking.id.desc())
                .limit(1)
            )
            if latest_booking:
                chat_id = latest_booking.chat_id or client_id
                topic_id = latest_booking.direct_messages_topic_id

        send_kwargs = {
            "chat_id": chat_id,
            "text": client_text,
            "reply_markup": client_keyboard,
            "parse_mode": "HTML",
        }
        if topic_id:
            send_kwargs["direct_messages_topic_id"] = topic_id

        try:
            await context.bot.send_message(**send_kwargs)
            if client_user_data:
                client_user_data["booking_state"] = "await_date"
                client_user_data.setdefault("history", []).append("await_prepayment")
            logger.info("Sent fallback prepayment message to client %s", client_id)
        except Exception as exc:
            logger.error(
                "Failed to notify user %s about prepayment approval: %s", client_id, exc
            )


async def handle_admin_healing_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Управление иерархическими инструкциями заживления с использованием безопасного разбора индексов."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    action = parts[1]

    if action == "zones":
        await query.edit_message_text(
            "Управление инструкциями по заживлению.\n\nВыберите интересующую зону проколов:",
            reply_markup=build_healing_zones_keyboard(
                "admin_healing:select_zone", "admin_menu:settings"
            ),
        )
    elif action == "select_zone":
        zone_key = parts[2]
        zone_name = PIERCING_ZONES.get(zone_key, {}).get("name", zone_key)
        await query.edit_message_text(
            f"Зона: <b>{zone_name}</b>\n\nВыберите тип прокола, для которого хотите настроить или изменить инструкцию:",
            reply_markup=build_healing_types_keyboard(
                zone_key, "admin_healing:select_type", "admin_healing:zones"
            ),
            parse_mode="HTML",
        )
    elif action == "select_type":
        zone_key = parts[2]
        type_idx_str = parts[3]

        zone_info = PIERCING_ZONES.get(zone_key)
        if not zone_info or not type_idx_str.isdigit():
            await query.answer("Произошла ошибка: неверные данные.", show_alert=True)
            return

        type_idx = int(type_idx_str)
        if type_idx < 0 or type_idx >= len(zone_info["types"]):
            await query.answer(
                "Произошла ошибка: неверный индекс типа.", show_alert=True
            )
            return

        type_name = zone_info["types"][type_idx]
        zone_name = zone_info.get("name", zone_key)

        async with AsyncSessionFactory() as session:
            template = await session.scalar(
                select(MediaTemplate).where(
                    MediaTemplate.key == f"healing_txt:{zone_key}:{type_name}"
                )
            )
            current_text = (
                template.value
                if template and template.value
                else "Текстовая инструкция еще не задана (пользователи будут видеть базовый дефолтный текст)."
            )

        context.user_data["admin_state"] = "await_healing_type_text"
        context.user_data["edit_healing_zone_key"] = zone_key
        context.user_data["edit_healing_type_name"] = type_name

        await query.edit_message_text(
            f"Редактирование инструкции для: <b>{type_name}</b> ({zone_name})\n\n"
            f"📝 <b>Текущий текст инструкции:</b>\n"
            f"<blockquote>{html.escape(current_text)}</blockquote>\n\n"
            f"Пожалуйста, введите и отправьте новый текст инструкции (обычный текст без форматирования):",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Назад",
                            callback_data=f"admin_healing:select_zone:{zone_key}",
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )


async def handle_admin_photo_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or not update.effective_message.photo
    ):
        return

    if not await check_if_admin(update.effective_user.id, context):
        return

    admin_state = context.user_data.get("admin_state")
    if admin_state != "await_zone_image_upload":
        return

    zone_key = context.user_data.pop("edit_zone_key", None)
    if not zone_key:
        await update.effective_message.reply_text(
            "Произошла ошибка: ключ зоны не найден. Начните редактирование заново.",
            reply_markup=build_admin_settings_menu(),
        )
        context.user_data.pop("admin_state", None)
        raise ApplicationHandlerStop()

    file_id = update.effective_message.photo[-1].file_id

    async with AsyncSessionFactory() as session:
        db_media = await session.scalar(
            select(MediaTemplate).where(MediaTemplate.key == f"zone_img:{zone_key}")
        )
        if db_media:
            db_media.telegram_file_id = file_id
        else:
            session.add(
                MediaTemplate(key=f"zone_img:{zone_key}", telegram_file_id=file_id)
            )
        await session.commit()

    context.user_data.pop("admin_state", None)
    zone_name = PIERCING_ZONES.get(zone_key, {}).get("name", zone_key)
    await update.effective_message.reply_text(
        f"Изображение для зоны <b>{zone_name}</b> успешно обновлено в базе данных.\n\n"
        f"Возврат в меню настроек.",
        reply_markup=build_admin_settings_menu(),
        parse_mode="HTML",
    )
    raise ApplicationHandlerStop()


async def handle_admin_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.text is None
    ):
        return

    if not await check_if_admin(update.effective_user.id, context):
        return

    admin_state = context.user_data.get("admin_state")
    if not admin_state:
        return

    text = update.effective_message.text.strip()

    if admin_state == "await_broadcast_text":
        context.user_data["temp_broadcast_text"] = text
        context.user_data["admin_state"] = "await_broadcast_time"
        await update.effective_message.reply_text(
            "Текст рассылки сохранен.\n\n"
            "Теперь укажите дату и время отправки в формате <b>ДД.ММ.ГГГГ ХХ:ХХ</b> (например, <code>25.07.2026 15:30</code>):",
            reply_markup=build_admin_cancel_button(),
            parse_mode="HTML",
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_broadcast_time":
        try:
            # Парсинг вводимых данных в часовом поясе Екатеринбурга (UTC+5)
            parsed_dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
            local_tz = timezone(timedelta(hours=5))
            aware_dt = parsed_dt.replace(tzinfo=local_tz)
            now_local = datetime.now(local_tz)

            if aware_dt <= now_local:
                await update.effective_message.reply_text(
                    "Вы указали время в прошлом. Пожалуйста, введите дату и время в будущем (ДД.ММ.ГГГГ ХХ:ХХ):",
                    reply_markup=build_admin_cancel_button(),
                )
                raise ApplicationHandlerStop()

        except ValueError:
            await update.effective_message.reply_text(
                "Некорректный формат даты или времени. Пожалуйста, введите по шаблону ДД.ММ.ГГГГ ХХ:ХХ (например, 25.07.2026 15:30):",
                reply_markup=build_admin_cancel_button(),
            )
            raise ApplicationHandlerStop()

        broadcast_text = context.user_data.pop("temp_broadcast_text", None)
        if not broadcast_text:
            await update.effective_message.reply_text(
                "Произошла непредвиденная ошибка: текст рассылки не найден. Пожалуйста, попробуйте создать рассылку заново.",
                reply_markup=build_admin_settings_menu(),
            )
            context.user_data.pop("admin_state", None)
            raise ApplicationHandlerStop()

        # Приводим к наивному UTC времени для хранения в БД
        utc_dt = aware_dt.astimezone(UTC).replace(tzinfo=None)

        async with AsyncSessionFactory() as session:
            broadcast = Broadcast(
                text=broadcast_text,
                scheduled_at=utc_dt,
                sent=False,
                admin_id=update.effective_user.id,
            )
            session.add(broadcast)
            await session.commit()
            await session.refresh(broadcast)

        # Планирование асинхронной задачи в Telegram JobQueue
        if context.job_queue:
            delay_seconds = (aware_dt - now_local).total_seconds()
            context.job_queue.run_once(
                run_broadcast_job,
                when=delay_seconds,
                data={"broadcast_id": broadcast.id},
                name=f"broadcast_job_{broadcast.id}",
            )
            await update.effective_message.reply_text(
                f"Рассылка успешно запланирована!\n\n"
                f"Время отправки: {text} (GMT+5)\n"
                f"Текст:\n{broadcast_text}",
                reply_markup=build_admin_settings_menu(),
            )
        else:
            await update.effective_message.reply_text(
                "Внимание: рассылка успешно сохранена в базу данных, но планировщик бота (JobQueue) временно недоступен. "
                "Рассылка будет автоматически отправлена при очередном перезапуске бота.",
                reply_markup=build_admin_settings_menu(),
            )

        context.user_data.pop("admin_state", None)
        raise ApplicationHandlerStop()

    if admin_state == "await_healing_type_text":
        zone_key = context.user_data.pop("edit_healing_zone_key", None)
        type_name = context.user_data.pop("edit_healing_type_name", None)
        if not zone_key or not type_name:
            await update.effective_message.reply_text(
                "Произошла ошибка: данные прокола не найдены. Начните редактирование заново.",
                reply_markup=build_admin_settings_menu(),
            )
            context.user_data.pop("admin_state", None)
            raise ApplicationHandlerStop()

        async with AsyncSessionFactory() as session:
            db_key = f"healing_txt:{zone_key}:{type_name}"
            template = await session.scalar(
                select(MediaTemplate).where(MediaTemplate.key == db_key)
            )
            if template:
                template.value = text
            else:
                session.add(MediaTemplate(key=db_key, value=text))
            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            f"Текстовая инструкция для прокола <b>{type_name}</b> успешно сохранена в базе данных.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu(),
            parse_mode="HTML",
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_ui_text_update":
        full_key = context.user_data.pop("edit_text_key", None)
        if not full_key:
            await update.effective_message.reply_text(
                "Произошла ошибка: ключ текста не найден. Начните редактирование заново.",
                reply_markup=build_admin_settings_menu(),
            )
            context.user_data.pop("admin_state", None)
            raise ApplicationHandlerStop()

        prefix, actual_key = full_key.split(":", 1)

        test_kwargs = {}
        if full_key == "stage_txt:await_tg_link_zone":
            test_kwargs = {
                "service_name": "Тест-услуга",
                "zone_name": "Тест-зона",
                "type_name": "Тест-тип",
            }
        elif full_key == "stage_txt:await_tg_link_simple":
            test_kwargs = {"service_name": "Тест-услуга"}
        elif full_key in ["stage_txt:select_slot", "stage_txt:no_slots"]:
            test_kwargs = {"date_str": "25.07.2026"}
        elif full_key == "stage_txt:await_date":
            test_kwargs = {"start_date": "01.07.2026", "end_date": "15.08.2026"}
        elif full_key == "custom_txt:booking_confirmed":
            test_kwargs = {
                "slot_display": "25.07.2026 в 15:30",
                "address_text": "Тест-адрес",
            }

        try:
            text.format(**test_kwargs)
        except (KeyError, ValueError, IndexError) as exc:
            context.user_data["edit_text_key"] = full_key
            await update.effective_message.reply_text(
                f"⚠️ <b>Ошибка разметки!</b>\n"
                f"Ваш текст содержит некорректно оформленные фигурные скобки или отсутствующие плейсхолдеры. "
                f"Ошибка: <code>{html.escape(str(exc))}</code>.\n\n"
                f"Пожалуйста, исправьте текст и пришлите его заново:",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Отмена", callback_data="admin_setting:ui_config"
                            )
                        ]
                    ]
                ),
                parse_mode="HTML",
            )
            raise ApplicationHandlerStop()

        async with AsyncSessionFactory() as session:
            template = await session.scalar(
                select(MediaTemplate).where(MediaTemplate.key == full_key)
            )
            if template:
                template.value = text
            else:
                session.add(MediaTemplate(key=full_key, value=text))
            await session.commit()

        context.user_data.pop("admin_state", None)

        if prefix == "stage_txt":
            label = STAGE_LABELS.get(actual_key, actual_key)
        else:
            label = CUSTOM_TEXT_LABELS.get(actual_key, actual_key)

        await update.effective_message.reply_text(
            f"Текст для <b>{label}</b> успешно сохранен в базе данных.\n\n"
            f"Возврат в меню настроек.",
            reply_markup=build_admin_settings_menu(),
            parse_mode="HTML",
        )
        raise ApplicationHandlerStop()

    if admin_state in [
        "await_userid_grant_healing",
        "await_userid_add_admin",
        "await_userid_revoke_admin",
    ]:
        try:
            target_id = int(text)
        except ValueError:
            await update.effective_message.reply_text(
                "Пожалуйста, введите корректный числовой Telegram ID пользователя.",
                reply_markup=build_admin_cancel_button(),
            )
            raise ApplicationHandlerStop()

        async with AsyncSessionFactory() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == target_id)
            )

            if user is None:
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
                success_text = (
                    f"Пользователь {target_id} успешно назначен администратором."
                )
            elif admin_state == "await_userid_revoke_admin":
                user.role = UserRole.USER
                success_text = (
                    f"Права администратора у пользователя {target_id} успешно отозваны."
                )

            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            success_text + "\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu(),
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_healing_instructions_text":
        async with AsyncSessionFactory() as session:
            setting = await session.get(StudioSetting, "healing_instructions")
            if setting:
                setting.value = text
            else:
                session.add(StudioSetting(key="healing_instructions", value=text))
            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Текст инструкции по заживлению успешно обновлен.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu(),
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_address":
        latitude = context.user_data.pop("temp_latitude", None)
        longitude = context.user_data.pop("temp_longitude", None)

        if latitude is None or longitude is None:
            await update.effective_message.reply_text(
                "Произошла ошибка: координаты не найдены. Попробуйте начать заново.",
                reply_markup=build_admin_settings_menu(),
            )
            context.user_data.pop("admin_state", None)
            raise ApplicationHandlerStop()

        async with AsyncSessionFactory() as session:
            for key, val in [
                ("latitude", str(latitude)),
                ("longitude", str(longitude)),
                ("address_text", text),
            ]:
                setting = await session.get(StudioSetting, key)
                if setting:
                    setting.value = val
                else:
                    session.add(StudioSetting(key=key, value=val))
            await session.commit()

        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Геолокация и текстовый адрес студии успешно обновлены.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu(),
        )
        raise ApplicationHandlerStop()

    if admin_state == "await_days_off":
        dates_str = text.split()
        valid_dates = []
        invalid_dates = []

        for d_str in dates_str:
            try:
                parsed_date = datetime.strptime(d_str, "%d.%m.%Y").date()
                valid_dates.append(parsed_date)
            except ValueError:
                invalid_dates.append(d_str)

        if invalid_dates:
            await update.effective_message.reply_text(
                f"Не удалось распознать следующие даты: {', '.join(invalid_dates)}.\n"
                "Пожалуйста, введите корректные даты в формате ДД.ММ.ГГГГ через пробел:",
                reply_markup=build_admin_cancel_button(),
            )
            raise ApplicationHandlerStop()

        if not valid_dates:
            await update.effective_message.reply_text(
                "Вы не ввели ни одной даты. Пожалуйста, попробуйте еще раз:",
                reply_markup=build_admin_cancel_button(),
            )
            raise ApplicationHandlerStop()

        clauses = []
        for d in valid_dates:
            start_dt = datetime.combine(d, time.min)
            end_dt = datetime.combine(d, time.max)
            clauses.append(
                (Booking.date_time >= start_dt) & (Booking.date_time <= end_dt)
            )

        async with AsyncSessionFactory() as session:
            conflicting_bookings = []
            if clauses:
                stmt = (
                    select(Booking)
                    .options(joinedload(Booking.user))
                    .where(Booking.status == BookingStatus.CONFIRMED)
                    .where(or_(*clauses))
                )
                res = await session.scalars(stmt)
                conflicting_bookings = list(res.unique().all())

        context.user_data["temp_days_off_dates"] = [d.isoformat() for d in valid_dates]
        context.user_data["admin_state"] = "await_days_off_confirm"

        if conflicting_bookings:
            by_date: dict[date, list[Booking]] = {}
            for b in conflicting_bookings:
                by_date.setdefault(b.date_time.date(), []).append(b)

            text_lines = ["⚠️ <b>Внимание! Обнаружены конфликтующие записи:</b>\n"]
            for d in sorted(by_date.keys()):
                text_lines.append(f"📅 <b>{d.strftime('%d.%m.%Y')}</b>:")
                for b in sorted(by_date[d], key=lambda x: x.date_time):
                    time_str = b.date_time.strftime("%H:%M")
                    username_str = (
                        f" (@{html.escape(b.user.username)})"
                        if b.user and b.user.username
                        else ""
                    )
                    text_lines.append(
                        f"  • {time_str} - {html.escape(b.client_name)}{username_str} "
                        f"(Тел: {html.escape(b.client_phone)}, Услуга: {html.escape(b.service_name)})"
                    )
                text_lines.append("")

            text_lines.append(
                "При установке выходных все эти записи будут <b>ОТМЕНЕНЫ</b>, "
                "а клиенты получат уведомление в свои личные чаты.\n\n"
                "Вы уверены, что хотите сделать эти дни выходными?"
            )
            confirm_text = "\n".join(text_lines)
        else:
            dates_list_str = ", ".join(
                d.strftime("%d.%m.%Y") for d in sorted(valid_dates)
            )
            confirm_text = (
                f"На выбранные даты (<b>{dates_list_str}</b>) нет активных записей.\n\n"
                "Вы уверены, что хотите установить эти выходные?"
            )

        keyboard = [
            [
                InlineKeyboardButton("Да", callback_data="admin_dayoff_confirm:yes"),
                InlineKeyboardButton("Нет", callback_data="admin_dayoff_confirm:no"),
            ]
        ]
        await update.effective_message.reply_text(
            confirm_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
        raise ApplicationHandlerStop()


async def handle_dayoff_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    if not await check_if_admin(update.effective_user.id, context):
        await query.answer("У вас нет прав администратора.", show_alert=True)
        return

    data = query.data or ""
    decision = data.split(":", 1)[1]

    temp_dates_str = context.user_data.pop("temp_days_off_dates", None)
    context.user_data.pop("admin_state", None)

    if decision == "no" or not temp_dates_str:
        await query.answer("Действие отменено.")
        await query.edit_message_text(
            "Установка выходных дней отменена.\n\nВозврат в меню настроек.",
            reply_markup=build_admin_settings_menu(),
        )
        return

    await query.answer("Изменения применяются...")
    dates = [date.fromisoformat(d) for d in temp_dates_str]

    clauses = []
    for d in dates:
        start_dt = datetime.combine(d, time.min)
        end_dt = datetime.combine(d, time.max)
        clauses.append((Booking.date_time >= start_dt) & (Booking.date_time <= end_dt))

    async with AsyncSessionFactory() as session:
        conflicting_bookings = []
        if clauses:
            stmt = (
                select(Booking)
                .options(joinedload(Booking.user))
                .where(Booking.status == BookingStatus.CONFIRMED)
                .where(or_(*clauses))
            )
            res = await session.scalars(stmt)
            conflicting_bookings = list(res.unique().all())

        calendar_service = GoogleCalendarService(get_settings())

        for booking in conflicting_bookings:
            booking.status = BookingStatus.CANCELLED
            if booking.google_event_id:
                await calendar_service.delete_booking_event(booking.google_event_id)
                booking.google_event_id = None

            notify_text = (
                f"⚠️ Здравствуйте, <b>{html.escape(booking.client_name)}</b>!\n\n"
                f"К сожалению, ваша запись на <b>{booking.date_time.strftime('%d.%m.%Y в %H:%M')}</b> "
                f"была отменена, так как этот день объявлен выходным в студии пирсинга.\n\n"
                f"Приносим извинения за неудобства! Вы можете выбрать любое другое свободное время для записи через меню бота."
            )
            try:
                if booking.chat_id and booking.direct_messages_topic_id:
                    await context.bot.send_message(
                        chat_id=booking.chat_id,
                        direct_messages_topic_id=booking.direct_messages_topic_id,
                        text=notify_text,
                        parse_mode="HTML",
                    )
                    logger.info(
                        "Уведомление об отмене отправлено в топик для бронирования %s",
                        booking.id,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=booking.user_id, text=notify_text, parse_mode="HTML"
                    )
                    logger.info(
                        "Уведомление об отмене отправлено в ЛС для бронирования %s",
                        booking.id,
                    )
            except Exception as exc:
                logger.error(
                    "Не удалось отправить уведомление пользователю %s об отмене бронирования %s: %s",
                    booking.user_id,
                    booking.id,
                    exc,
                )

        added_dates_count = 0
        for d in dates:
            exists = await session.scalar(select(DayOff).where(DayOff.date == d))
            if exists:
                continue

            local_tz = timezone(timedelta(hours=5))
            start_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=local_tz)
            end_dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=local_tz)

            g_event_id = await calendar_service.create_booking_event(
                booking_summary="ВЫХОДНОЙ СТУДИИ",
                description="Этот день был отмечен как выходной администратором в настройках.",
                start_dt=start_dt,
                end_dt=end_dt,
            )

            day_off = DayOff(date=d, google_event_id=g_event_id)
            session.add(day_off)
            added_dates_count += 1

        await session.commit()

    dates_str_list = ", ".join(d.strftime("%d.%m.%Y") for d in dates)
    await query.edit_message_text(
        f"Выходные успешно установлены на даты: <b>{dates_str_list}</b>.\n"
        f"Отменено конфликтующих записей: <b>{len(conflicting_bookings)}</b>.\n\n"
        f"Возврат в меню настроек.",
        reply_markup=build_admin_settings_menu(),
        parse_mode="HTML",
    )


async def handle_admin_location_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if (
        update.effective_user is None
        or update.effective_message is None
        or update.effective_message.location is None
    ):
        return

    if not await check_if_admin(update.effective_user.id, context):
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
        reply_markup=build_admin_cancel_button(),
    )
    raise ApplicationHandlerStop()


admin_handlers = [
    CallbackQueryHandler(handle_admin_menu_callback, pattern=r"^admin_menu:"),
    CallbackQueryHandler(handle_admin_setting_callback, pattern=r"^admin_setting:"),
    CallbackQueryHandler(
        handle_dayoff_confirm_callback, pattern=r"^admin_dayoff_confirm:"
    ),
    CallbackQueryHandler(handle_admin_ui_callback, pattern=r"^admin_ui:"),
    CallbackQueryHandler(
        handle_admin_ui_txt_cat_callback, pattern=r"^admin_ui_txt_cat:"
    ),
    CallbackQueryHandler(handle_admin_prepayment_approval, pattern=r"^approve_pay:"),
    CallbackQueryHandler(handle_admin_healing_callback, pattern=r"^admin_healing:"),
    CallbackQueryHandler(handle_admin_ui_img_callback, pattern=r"^admin_ui_img:"),
    CallbackQueryHandler(handle_admin_ui_txt_callback, pattern=r"^admin_ui_txt:"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input),
    MessageHandler(filters.LOCATION, handle_admin_location_input),
    MessageHandler(filters.PHOTO, handle_admin_photo_input),
]
