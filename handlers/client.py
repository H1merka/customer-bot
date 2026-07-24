# handlers/client.py
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone, date
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config.settings import get_settings
from database.connection import AsyncSessionFactory
from database.models import (
    Booking,
    BookingStatus,
    StudioSetting,
    User,
    UserRole,
    DayOff,
    MediaTemplate,
)
from handlers.common import build_main_menu, register_user
from services.google_calendar import GoogleCalendarService
from sqlalchemy import select

from config.constants import (
    BASE_DIR,
    SERVICE_OPTIONS,
    SERVICES_WITH_ZONE,
    SERVICES_WITH_MEDICAL,
    SERVICES_GROUP_A,
    PIERCING_ZONES,
    DEFAULT_STAGE_TEXTS,
    MEDICAL_QUESTIONS,
    clear_booking_session,
    CUSTOM_TEXT_LABELS,
    DEFAULT_CUSTOM_TEXTS,
)

logger = logging.getLogger(__name__)


async def get_custom_text(key: str, default_value: str, **kwargs) -> str:
    """
    Получает динамический кастомный текст из базы данных (ключи вида custom_txt:...).
    Безопасно возвращает дефолтное значение при отсутствии записи или сбое форматирования.
    """
    async with AsyncSessionFactory() as session:
        template = await session.scalar(
            select(MediaTemplate).where(MediaTemplate.key == key)
        )
        raw_text = template.value if template and template.value else default_value

    try:
        return raw_text.format(**kwargs)
    except Exception as exc:
        logger.warning(
            "Formatting failed for custom text %s: %s. Falling back to default.",
            key,
            exc,
        )
        return raw_text


def get_allowed_booking_range() -> tuple[date, date]:
    local_tz = timezone(timedelta(hours=5))
    now_local = datetime.now(local_tz).date()
    
    start_date = now_local.replace(day=1)
    
    if now_local.month == 12:
        next_month = 1
        next_year = now_local.year + 1
    else:
        next_month = now_local.month + 1
        next_year = now_local.year
        
    end_date = date(next_year, next_month, 15)
    return start_date, end_date


async def get_zone_media_payload(
    zone_key: str, zone_info: dict
) -> tuple[bool, bool, Any]:
    async with AsyncSessionFactory() as session:
        db_media = await session.scalar(
            select(MediaTemplate).where(MediaTemplate.key == f"zone_img:{zone_key}")
        )

    if db_media is not None:
        if db_media.telegram_file_id:
            return True, True, db_media.telegram_file_id
        return False, False, None

    if zone_info.get("image"):
        return True, False, BASE_DIR / "images" / zone_info["image"]

    return False, False, None


async def get_stage_text(stage_key: str, **kwargs) -> str:
    default_text = DEFAULT_STAGE_TEXTS.get(stage_key, "")
    async with AsyncSessionFactory() as session:
        template = await session.scalar(
            select(MediaTemplate).where(MediaTemplate.key == f"stage_txt:{stage_key}")
        )
        raw_text = template.value if template and template.value else default_text

    try:
        return raw_text.format(**kwargs)
    except (KeyError, ValueError, IndexError) as exc:
        logger.warning(
            "Formatting failed for stage %s: %s. Falling back to default text.",
            stage_key,
            exc,
        )
        try:
            return default_text.format(**kwargs)
        except Exception:
            return default_text


def get_send_kwargs(
    update: Update, text: str, reply_markup: Any = None
) -> dict[str, Any]:
    send_kwargs: dict[str, Any] = {
        "text": text,
    }
    if reply_markup is not None:
        send_kwargs["reply_markup"] = reply_markup

    if not update.effective_chat:
        return send_kwargs

    thread_id = None
    if update.effective_message:
        if getattr(update.effective_message, "direct_messages_topic", None):
            thread_id = update.effective_message.direct_messages_topic.topic_id
        elif update.effective_message.message_thread_id:
            thread_id = update.effective_message.message_thread_id

    if getattr(update.effective_chat, "is_direct_messages", False):
        if thread_id:
            send_kwargs["direct_messages_topic_id"] = thread_id
    else:
        if thread_id:
            send_kwargs["message_thread_id"] = thread_id

    return send_kwargs


def get_photo_send_kwargs(
    update: Update, photo: Any, caption: str, reply_markup: Any = None
) -> dict[str, Any]:
    send_kwargs: dict[str, Any] = {
        "photo": photo,
        "caption": caption,
    }
    if reply_markup is not None:
        send_kwargs["reply_markup"] = reply_markup

    if not update.effective_chat:
        return send_kwargs

    thread_id = None
    if update.effective_message:
        if getattr(update.effective_message, "direct_messages_topic", None):
            thread_id = update.effective_message.direct_messages_topic.topic_id
        elif update.effective_message.message_thread_id:
            thread_id = update.effective_message.message_thread_id

    if getattr(update.effective_chat, "is_direct_messages", False):
        if thread_id:
            send_kwargs["direct_messages_topic_id"] = thread_id
    else:
        if thread_id:
            send_kwargs["message_thread_id"] = thread_id

    return send_kwargs


def build_service_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(SERVICE_OPTIONS["piercing"], callback_data="service:piercing")],
        [InlineKeyboardButton(SERVICE_OPTIONS["apsize"], callback_data="service:apsize")],
        [InlineKeyboardButton(SERVICE_OPTIONS["downsize"], callback_data="service:downsize")],
        [InlineKeyboardButton(SERVICE_OPTIONS["cleaning"], callback_data="service:cleaning")],
        [InlineKeyboardButton(SERVICE_OPTIONS["consultation"], callback_data="service:consultation")],
        [InlineKeyboardButton(SERVICE_OPTIONS["jewelry"], callback_data="service:jewelry")],
        [InlineKeyboardButton(SERVICE_OPTIONS["anodizing"], callback_data="service:anodizing")],
        [InlineKeyboardButton(SERVICE_OPTIONS["buy_certificate"], callback_data="service:buy_certificate")], # Новое
        [InlineKeyboardButton("Назад", callback_data="back:main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_cert_target_service_menu() -> InlineKeyboardMarkup:
    """Генерирует меню выбора целевой услуги для приобретаемого сертификата."""
    from config.constants import SERVICE_OPTIONS
    keyboard = []
    for key, val in SERVICE_OPTIONS.items():
        if key != "buy_certificate":
            # ИСПРАВЛЕНО: передаем латинский ключ key вместо длинного кириллического val
            keyboard.append([InlineKeyboardButton(val, callback_data=f"cert_target:{key}")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")])
    return InlineKeyboardMarkup(keyboard)


def build_piercing_zone_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Губы/рот", callback_data="p_zone:mouth")],
        [InlineKeyboardButton("Нос/лицо", callback_data="p_zone:face")],
        [InlineKeyboardButton("Тело", callback_data="p_zone:body")],
        [InlineKeyboardButton("Уши", callback_data="p_zone:ear")],
        [InlineKeyboardButton("Назад", callback_data="booking_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_piercing_types_menu(zone_key: str) -> InlineKeyboardMarkup:
    zone_info = PIERCING_ZONES.get(zone_key)
    if not zone_info:
        return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")]])

    keyboard = []
    for i, type_name in enumerate(zone_info["types"], 1):
        btn_text = type_name if zone_key == "body" else f"{i}. {type_name}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    btn_text, callback_data=f"p_type:{zone_key}:{type_name}"
                )
            ]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")])
    return InlineKeyboardMarkup(keyboard)


def build_back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")]]
    )


def build_medical_keyboard(question_id: int) -> InlineKeyboardMarkup:
    q_info = MEDICAL_QUESTIONS[question_id]
    keyboard = []
    row = []
    for label, cb_data in q_info["options"]:
        row.append(InlineKeyboardButton(label, callback_data=cb_data))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")])
    return InlineKeyboardMarkup(keyboard)


def build_client_healing_zones_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    for zone_key, zone_info in PIERCING_ZONES.items():
        keyboard.append(
            [
                InlineKeyboardButton(
                    zone_info["name"], callback_data=f"client_healing:select_zone:{zone_key}"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:main")])
    return InlineKeyboardMarkup(keyboard)


def build_client_healing_types_keyboard(zone_key: str) -> InlineKeyboardMarkup:
    zone_info = PIERCING_ZONES.get(zone_key)
    if not zone_info:
        return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="client_healing:zones")]])
    
    keyboard = []
    for idx, type_name in enumerate(zone_info["types"]):
        keyboard.append(
            [
                InlineKeyboardButton(
                    type_name, callback_data=f"client_healing:select_type:{zone_key}:{idx}"
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="client_healing:zones")])
    return InlineKeyboardMarkup(keyboard)


async def transition_to_state(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state_name: str,
    edit_message: bool = False,
) -> None:
    context.user_data["booking_state"] = state_name
    text = ""
    reply_markup = None

    if state_name == "service_selection":
        text = await get_stage_text("service_selection")
        reply_markup = build_service_menu()
    elif state_name == "cert_target_selection":
        text = "Выберите услугу, на которую вы приобретаете подарочный сертификат:"
        reply_markup = build_cert_target_service_menu()
    elif state_name == "await_cert_amount":
        text = "Пожалуйста, укажите желаемую номинальную стоимость (сумму) сертификата в рублях (введите целое число):"
        reply_markup = build_back_button()
    elif state_name == "await_cert_activation":
        text = "Пожалуйста, укажите 5-значный цифровой код вашего сертификата для его последующей активации:"
        reply_markup = build_back_button()
    elif state_name == "piercing_zone_selection":
        text = await get_stage_text("zone_selection")
        reply_markup = build_piercing_zone_menu()
    elif state_name == "await_tg_link":
        selected_service = context.user_data.get("selected_service")
        if selected_service == SERVICE_OPTIONS["buy_certificate"]:
            target_service = context.user_data.get("cert_target_service", "Не указано")
            amount = context.user_data.get("cert_amount", 0)
            text = (
                f"Вы выбрали приобретение сертификата на услугу: «{target_service}» номиналом {amount} руб.\n\n"
                f"Пожалуйста, отправьте ссылку на ваш Telegram-профиль (например, https://t.me/username или @username) "
                f"для оперативной связи:"
            )
        elif selected_service in SERVICES_WITH_ZONE:
            zone_name = context.user_data.get("temp_piercing_zone_name", "Не указано")
            type_name = context.user_data.get("temp_piercing_type", "Не указано")
            text = await get_stage_text(
                "await_tg_link_zone",
                service_name=selected_service,
                zone_name=zone_name,
                type_name=type_name,
            )
        else:
            text = await get_stage_text(
                "await_tg_link_simple", service_name=selected_service
            )
        reply_markup = build_back_button()
    elif state_name == "await_age":
        text = await get_stage_text("await_age")
        reply_markup = build_back_button()
    elif state_name == "await_parent_name":
        text = await get_stage_text("await_parent_name")
        reply_markup = build_back_button()
    elif state_name == "await_parent_phone":
        text = await get_stage_text("await_parent_phone")
        reply_markup = build_back_button()
    elif state_name.startswith("medical_question_"):
        q_num = int(state_name.split("_")[-1])
        text = await get_custom_text(f"custom_txt:medical_q{q_num}", DEFAULT_CUSTOM_TEXTS[f"medical_q{q_num}"])
        reply_markup = build_medical_keyboard(q_num)
    elif state_name.startswith("await_medical_text_"):
        q_num = int(state_name.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        text = q_info["detail_prompt"]
        reply_markup = build_back_button()
        
    elif state_name == "await_prepayment":
        user_id = update.effective_user.id
        
        # Если прохождение идет по активированному сертификату — бесшовно минуем шаг ручной предоплаты
        if "active_cert_code" in context.user_data:
            context.user_data["booking_state"] = "await_date"
            await transition_to_state(update, context, "await_date", edit_message=edit_message)
            return

        selected_service = context.user_data.get("selected_service", "Не указана")
        
        if selected_service == SERVICE_OPTIONS["buy_certificate"]:
            target_service = context.user_data.get("cert_target_service", "Не указана")
            cert_amount = context.user_data.get("cert_amount", 0)
            
            prepay_tpl = await get_custom_text("custom_txt:cert_prepayment_info", DEFAULT_CUSTOM_TEXTS["cert_prepayment_info"])
            text = prepay_tpl.format(target_service=target_service, amount=cert_amount)
            reply_markup = build_back_button()
        else:
            async with AsyncSessionFactory() as session:
                user = await session.scalar(select(User).where(User.telegram_id == user_id))
                is_prepaid = user.is_prepaid if user else False
                
            if is_prepaid:
                context.user_data["booking_state"] = "await_date"
                await transition_to_state(update, context, "await_date", edit_message=edit_message)
                return

            text = await get_custom_text("custom_txt:prepayment_info", DEFAULT_CUSTOM_TEXTS["prepayment_info"])
            reply_markup = build_back_button()
        
        client_name = html.escape(update.effective_user.full_name or "Unknown")
        client_username = f" (@{update.effective_user.username})" if update.effective_user.username else ""
        client_link = context.user_data.get("client_tg_link", "Не указана")
        
        if selected_service == SERVICE_OPTIONS["buy_certificate"]:
            target_service = context.user_data.get("cert_target_service", "Не указана")
            cert_amount = context.user_data.get("cert_amount", 0)
            notify_text = (
                f"💳 <b>Поступил запрос на подтверждение покупки сертификата!</b>\n\n"
                f"<b>Клиент:</b> {client_name}{client_username} (ID: {user_id})\n"
                f"<b>Ссылка на TG:</b> {html.escape(client_link)}\n"
                f"<b>Услуга для сертификата:</b> {html.escape(target_service)}\n"
                f"<b>Полная оплата:</b> {cert_amount} руб.\n\n"
                f"Ожидайте поступления средств на счет в размере {cert_amount} руб., после чего нажмите кнопку:"
            )
        else:
            notify_text = (
                f"💳 <b>Поступил запрос на подтверждение предоплаты!</b>\n\n"
                f"<b>Клиент:</b> {client_name}{client_username} (ID: {user_id})\n"
                f"<b>Ссылка на TG:</b> {html.escape(client_link)}\n"
                f"<b>Выбранная услуга:</b> {html.escape(selected_service)}\n\n"
                f"Ожидайте поступления средств на счет. После получения нажмите кнопку ниже:"
            )
        
        admin_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"approve_pay:{user_id}")]
        ])
        
        async with AsyncSessionFactory() as session:
            db_admins = await session.scalars(
                select(User.telegram_id).where(User.role == UserRole.ADMIN)
            )
            all_admins = set(get_settings().admin_telegram_ids) | set(db_admins)
            
        for admin_id in all_admins:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notify_text,
                    reply_markup=admin_keyboard,
                    parse_mode="HTML"
                )
            except Exception as exc:
                logger.warning("Failed to send prepayment notification to admin %s: %s", admin_id, exc)

    elif state_name == "await_date":
        start_date, end_date = get_allowed_booking_range()
        start_str = start_date.strftime("%d.%m.%Y")
        end_str = end_date.strftime("%d.%m.%Y")
        
        text = await get_stage_text("await_date", start_date=start_str, end_date=end_str)
        reply_markup = build_back_button()
    elif state_name == "select_slot":
        req_date = context.user_data.get("requested_date")
        if req_date:
            try:
                target_date = datetime.strptime(req_date, "%d.%m.%Y").replace(
                    tzinfo=timezone(timedelta(hours=5))
                )
                await process_date_availability(
                    update, context, target_date, edit_message=edit_message
                )
            except Exception:
                context.user_data["booking_state"] = "await_date"
                await transition_to_state(
                    update, context, "await_date", edit_message=edit_message
                )
        else:
            context.user_data["booking_state"] = "await_date"
            await transition_to_state(
                update, context, "await_date", edit_message=edit_message
            )
        return

    is_zone_flow_proceed = (
        state_name == "await_tg_link"
        and context.user_data.get("selected_service") in SERVICES_WITH_ZONE
    )

    sent_msg = None
    if edit_message and update.callback_query and not is_zone_flow_proceed:
        try:
            sent_msg = await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode="HTML"
            )
        except Exception:
            if update.effective_chat:
                kwargs = get_send_kwargs(update, text, reply_markup)
                kwargs["parse_mode"] = "HTML"
                sent_msg = await update.effective_chat.send_message(**kwargs)
            else:
                sent_msg = await update.effective_message.reply_text(
                    text, reply_markup=reply_markup, parse_mode="HTML"
                )
    else:
        if update.callback_query and not is_zone_flow_proceed:
            try:
                sent_msg = await update.callback_query.edit_message_text(
                    text, reply_markup=reply_markup, parse_mode="HTML"
                )
            except Exception:
                if update.effective_chat:
                    kwargs = get_send_kwargs(update, text, reply_markup)
                    kwargs["parse_mode"] = "HTML"
                    sent_msg = await update.effective_chat.send_message(**kwargs)
                else:
                    sent_msg = await update.effective_message.reply_text(
                        text, reply_markup=reply_markup, parse_mode="HTML"
                    )
        else:
            if update.effective_chat:
                kwargs = get_send_kwargs(update, text, reply_markup)
                kwargs["parse_mode"] = "HTML"
                sent_msg = await update.effective_chat.send_message(**kwargs)
            else:
                sent_msg = await update.effective_message.reply_text(
                    text, reply_markup=reply_markup, parse_mode="HTML"
                )

    if state_name == "await_prepayment" and sent_msg:
        context.user_data["prepayment_message_id"] = sent_msg.message_id
        context.user_data["prepayment_chat_id"] = sent_msg.chat_id


async def handle_booking_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    history = context.user_data.get("history", [])
    if not history:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "Главное меню", reply_markup=build_main_menu()
            )
        else:
            await update.effective_message.reply_text(
                "Главное меню", reply_markup=build_main_menu()
            )
        context.user_data["booking_state"] = None
        return

    current_state = context.user_data.get("booking_state")
    prev_state = history.pop()

    if current_state == "await_cert_activation":
        query = update.callback_query
        if query:
            await query.edit_message_text("Главное меню", reply_markup=build_main_menu())
        else:
            await update.effective_message.reply_text("Главное меню", reply_markup=build_main_menu())
        context.user_data["booking_state"] = None
        return

    elif current_state == "cert_target_selection":
        context.user_data.pop("selected_service", None)
        query = update.callback_query
        if query:
            await query.edit_message_text("Выберите услугу:", reply_markup=build_service_menu())
        else:
            await update.effective_message.reply_text("Выберите услугу:", reply_markup=build_service_menu())
        context.user_data["booking_state"] = "service_selection"
        return

    elif current_state == "await_cert_amount":
        context.user_data.pop("cert_target_service", None)
        query = update.callback_query
        if query:
            await query.edit_message_text(
                "Выберите услугу, на которую вы приобретаете подарочный сертификат:",
                reply_markup=build_cert_target_service_menu()
            )
        else:
            await update.effective_message.reply_text(
                "Выберите услугу, на которую вы приобретаете подарочный сертификат:",
                reply_markup=build_cert_target_service_menu()
            )
        context.user_data["booking_state"] = "cert_target_selection"
        return

    elif current_state == "piercing_type_selection":
        context.user_data.pop("temp_piercing_zone_key", None)
        context.user_data.pop("temp_piercing_zone_name", None)
        context.user_data.pop("temp_piercing_type", None)

        photo_msg_id = context.user_data.pop("photo_message_id", None)
        if photo_msg_id and update.effective_chat:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=photo_msg_id
                )
            except Exception as e:
                logger.warning(
                    "Не удалось удалить сообщение с фото при возврате назад: %s", e
                )

        query = update.callback_query
        if query and query.message and query.message.message_id == photo_msg_id:
            try:
                await query.message.delete()
            except Exception as e:
                logger.warning("Не удалось удалить callback-сообщение с фото: %s", e)

        zone_menu_msg_id = context.user_data.get("zone_menu_message_id")
        if zone_menu_msg_id and update.effective_chat:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=zone_menu_msg_id,
                    text="Выберите зону пирсинга:",
                    reply_markup=build_piercing_zone_menu(),
                )
            except Exception as e:
                logger.warning("Не удалось отредактировать меню зон: %s", e)
                kwargs = get_send_kwargs(
                    update, "Выберите зону пирсинга:", build_piercing_zone_menu()
                )
                await update.effective_chat.send_message(**kwargs)
        else:
            if query:
                try:
                    await query.edit_message_text(
                        "Выберите зону пирсинга:",
                        reply_markup=build_piercing_zone_menu(),
                    )
                except Exception:
                    if update.effective_chat:
                        kwargs = get_send_kwargs(
                            update,
                            "Выберите зону пирсинга:",
                            build_piercing_zone_menu(),
                        )
                        await update.effective_chat.send_message(**kwargs)
                    else:
                        await update.effective_message.reply_text(
                            "Выберите зону пирсинга:",
                            reply_markup=build_piercing_zone_menu(),
                        )
            else:
                if update.effective_chat:
                    kwargs = get_send_kwargs(
                        update, "Выберите зону пирсинга:", build_piercing_zone_menu()
                    )
                    await update.effective_chat.send_message(**kwargs)
                else:
                    await update.effective_message.reply_text(
                        "Выберите зону пирсинга:",
                        reply_markup=build_piercing_zone_menu(),
                    )

        context.user_data["booking_state"] = "piercing_zone_selection"
        return

    elif current_state == "piercing_zone_selection":
        context.user_data.pop("selected_service", None)
        query = update.callback_query
        if query:
            await query.edit_message_text(
                "Выберите услугу:", reply_markup=build_service_menu()
            )
        else:
            await update.effective_message.reply_text(
                "Выберите услугу:", reply_markup=build_service_menu()
            )
        context.user_data["booking_state"] = "service_selection"
        return

    elif current_state == "await_tg_link":
        context.user_data.pop("client_tg_link", None)
        if context.user_data.get("selected_service") in SERVICES_WITH_ZONE:
            query = update.callback_query
            if query:
                try:
                    await query.message.delete()
                except Exception:
                    pass

            zone_key = context.user_data.get("temp_piercing_zone_key")
            zone_info = PIERCING_ZONES.get(zone_key) if zone_key else None
            if zone_info:
                has_image, use_file_id, img_payload = await get_zone_media_payload(
                    zone_key, zone_info
                )

                if has_image:
                    kwargs = get_send_kwargs(
                        update,
                        f"Выбрана зона: {zone_info['name']}. Выберите тип прокола на картинке ниже.",
                    )
                    new_zone_msg = await update.effective_chat.send_message(**kwargs)
                    context.user_data["zone_menu_message_id"] = new_zone_msg.message_id

                    try:
                        if use_file_id:
                            photo_kwargs = get_photo_send_kwargs(
                                update,
                                photo=img_payload,
                                caption=f"Выберите тип прокола для зоны {zone_info['name']}:",
                                reply_markup=build_piercing_types_menu(zone_key),
                            )
                            photo_msg = await update.effective_chat.send_photo(
                                **photo_kwargs
                            )
                        else:
                            with open(img_payload, "rb") as f:
                                photo_kwargs = get_photo_send_kwargs(
                                    update,
                                    photo=f,
                                    caption=f"Выберите тип прокола для зоны {zone_info['name']}:",
                                    reply_markup=build_piercing_types_menu(zone_key),
                                )
                                photo_msg = await update.effective_chat.send_photo(
                                    **photo_kwargs
                                )
                        context.user_data["photo_message_id"] = photo_msg.message_id
                    except Exception as e:
                        logger.error("Ошибка отправки фото при возврате: %s", e)
                        fallback_kwargs = get_send_kwargs(
                            update,
                            text=f"Выберите тип прокола для зоны {zone_info['name']}:",
                            reply_markup=build_piercing_types_menu(zone_key),
                        )
                        fallback_msg = await update.effective_chat.send_message(
                            **fallback_kwargs
                        )
                        context.user_data["photo_message_id"] = fallback_msg.message_id
                else:
                    body_kwargs = get_send_kwargs(
                        update,
                        text=f"Выберите тип прокола для зоны {zone_info['name']}:",
                        reply_markup=build_piercing_types_menu(zone_key),
                    )
                    body_msg = await update.effective_chat.send_message(**body_kwargs)
                    context.user_data["zone_menu_message_id"] = body_msg.message_id

            context.user_data["booking_state"] = "piercing_type_selection"
            return
        else:
            context.user_data.pop("selected_service", None)
            query = update.callback_query
            if query:
                await query.edit_message_text(
                    "Выберите услугу:", reply_markup=build_service_menu()
                )
            else:
                await update.effective_message.reply_text(
                    "Выберите услугу:", reply_markup=build_service_menu()
                )
            context.user_data["booking_state"] = "service_selection"
            return

    elif current_state == "await_age":
        context.user_data.pop("client_age", None)
    elif current_state == "await_parent_name":
        context.user_data.pop("parent_name", None)
    elif current_state == "await_parent_phone":
        context.user_data.pop("parent_phone", None)
    elif current_state == "await_prepayment":
        pass
    elif current_state == "await_date":
        context.user_data.pop("requested_date", None)
    elif current_state and current_state.startswith("await_medical_text_"):
        q_num = int(current_state.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        if "medical_answers" in context.user_data:
            context.user_data["medical_answers"].pop(q_info["db_field"], None)
    elif current_state and current_state.startswith("medical_question_"):
        q_num = int(current_state.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]
        if "medical_answers" in context.user_data:
            context.user_data["medical_answers"].pop(q_info["db_field"], None)

    await transition_to_state(update, context, prev_state, edit_message=True)


async def initiate_medical_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["booking_state"] = "paused_for_medical_review"
    client_id = update.effective_user.id
    client_name = html.escape(update.effective_user.full_name or "Unknown")
    client_link = context.user_data.get("client_tg_link", "Не указана")
    client_age = context.user_data.get("client_age", "Не указан")
    medical_answers = context.user_data.get("medical_answers", {})

    from database.models import SupportTicket, SupportTicketStatus, User, UserRole
    from database.connection import AsyncSessionFactory
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        existing = await session.scalar(
            select(SupportTicket).where(SupportTicket.user_telegram_id == client_id)
        )
        if existing is None:
            session.add(
                SupportTicket(
                    user_telegram_id=client_id,
                    status=SupportTicketStatus.OPEN,
                )
            )
        else:
            existing.status = SupportTicketStatus.OPEN
            existing.assigned_admin_id = None

        db_admins = await session.scalars(
            select(User.telegram_id).where(User.role == UserRole.ADMIN)
        )
        all_admins = set(get_settings().admin_telegram_ids) | set(db_admins)

        await session.commit()

    report_lines = [
        "📋 <b>Медицинская анкета для ручного разбора!</b>\n",
        f"<b>Клиент:</b> {client_name} (ID: {client_id})",
        f"<b>Ссылка на TG:</b> {html.escape(client_link)}",
        f"<b>Возраст:</b> {client_age}\n",
        "<b>Ответы на медицинские вопросы:</b>",
    ]

    field_to_question = {
        "blood_disease": "1. Заболевания крови",
        "blood_clotting": "2. Свертываемость крови",
        "current_medication": "3. Принимаемые лекарства",
        "chronic_disease": "4. Хронические заболевания",
        "healing_issues": "5. Проблемы с заживлением",
        "skin_disease": "6. Кожные заболевания",
    }

    for field, q_text in field_to_question.items():
        ans = medical_answers.get(field, "Нет ответа")
        report_lines.append(f"• <b>{q_text}:</b> {html.escape(str(ans))}")

    report_text = "\n".join(report_lines)

    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=report_text, parse_mode="HTML"
            )
        except Exception as exc:
            logger.warning(
                "Не удалось отправить медицинский отчет админу %s: %s", admin_id, exc
            )

    review_text = await get_custom_text("custom_txt:specialist_review", DEFAULT_CUSTOM_TEXTS["specialist_review"])
    
    keyboard = [[InlineKeyboardButton("Закрыть диалог", callback_data="close_support")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    send_kwargs = get_send_kwargs(
        update, review_text, reply_markup=reply_markup
    )
    await update.effective_chat.send_message(**send_kwargs)


async def handle_resume_booking(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    context.user_data["booking_state"] = "await_prepayment"
    context.user_data.setdefault("history", []).append("await_tg_link")

    await transition_to_state(update, context, "await_prepayment", edit_message=True)


async def handle_resume_after_pay(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    
    context.user_data["booking_state"] = "await_date"
    context.user_data.setdefault("history", []).append("await_prepayment")
    
    await transition_to_state(update, context, "await_date", edit_message=True)


async def get_free_slots_for_date(
    calendar_service: GoogleCalendarService,
    target_date: datetime,
    selected_service: str | None,
) -> list[datetime]:
    local_tz = timezone(timedelta(hours=5))

    async with AsyncSessionFactory() as session:
        day_off = await session.scalar(
            select(DayOff).where(DayOff.date == target_date.date())
        )
        if day_off is not None:
            return []

    busy_intervals = await calendar_service.get_busy_intervals(target_date)

    if selected_service in SERVICES_GROUP_A:
        working_hours = ["15:30", "16:30", "17:30", "18:30", "19:30"]
        slot_duration = timedelta(minutes=30)
    else:
        working_hours = ["14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
        slot_duration = timedelta(hours=1)

    free_slots = []
    now = datetime.now(timezone.utc)

    for hw in working_hours:
        hour, minute = map(int, hw.split(":"))
        slot_start = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hour,
            minute,
            tzinfo=local_tz,
        )

        if slot_start < now:
            continue

        slot_end = slot_start + slot_duration
        overlaps = False
        for busy_start, busy_end in busy_intervals:
            if slot_start < busy_end and slot_end > busy_start:
                overlaps = True
                break

        if not overlaps:
            free_slots.append(slot_start)

    return free_slots


def build_slots_keyboard(date_str: str, slots: list[datetime]) -> InlineKeyboardMarkup:
    keyboard = []
    for slot in slots:
        btn_text = slot.strftime("%H:%M")
        callback_data = f"book_slot:{slot.isoformat()}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])

    keyboard.append(
        [InlineKeyboardButton("✍️ Ввести другую дату", callback_data="change_date")]
    )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")])
    return InlineKeyboardMarkup(keyboard)


def build_neighboring_dates_keyboard(current_date: datetime) -> InlineKeyboardMarkup:
    prev_date = current_date - timedelta(days=1)
    next_date = current_date + timedelta(days=1)

    prev_str = prev_date.strftime("%d.%m.%Y")
    next_str = next_date.strftime("%d.%m.%Y")

    keyboard = [
        [
            InlineKeyboardButton(
                f"⬅️ {prev_date.strftime('%d.%m')}",
                callback_data=f"check_date:{prev_str}",
            ),
            InlineKeyboardButton(
                f"➡️ {next_date.strftime('%d.%m')}",
                callback_data=f"check_date:{next_str}",
            ),
        ],
        [InlineKeyboardButton("✍️ Ввести другую дату", callback_data="change_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="booking_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def process_date_availability(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_date: datetime,
    edit_message: bool = False,
) -> None:
    settings = get_settings()
    calendar_service = GoogleCalendarService(settings)

    checking_msg = None
    if edit_message and update.callback_query:
        await update.callback_query.answer("Проверяем расписание...")
    else:
        checking_msg = await update.effective_message.reply_text(
            "Минутку, сверяемся с календарем студии..."
        )

    selected_service = context.user_data.get("selected_service")
    free_slots = await get_free_slots_for_date(
        calendar_service, target_date, selected_service
    )

    if checking_msg:
        try:
            await checking_msg.delete()
        except Exception:
            pass

    date_str = target_date.strftime("%d.%m.%Y")
    context.user_data["last_checked_date"] = date_str

    if free_slots:
        text = await get_stage_text("select_slot", date_str=date_str)
        reply_markup = build_slots_keyboard(date_str, free_slots)
    else:
        text = await get_stage_text("no_slots", date_str=date_str)
        reply_markup = build_neighboring_dates_keyboard(target_date)

    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def notify_admins_about_blood_disease(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    settings = get_settings()
    user = update.effective_user
    if not user:
        return

    user_id = user.id
    username_part = f", @{user.username}" if user.username else ""
    msg_text = (
        f"⚠️ Пользователь {user.full_name or 'Unknown'} (ID: {user_id}{username_part}) "
        f"попытался записаться на сеанс, но указал наличие заболевания крови. "
        f"Запись была автоматически отклонена."
    )

    from database.models import UserRole

    async with AsyncSessionFactory() as session:
        db_admins = await session.scalars(
            select(User.telegram_id).where(User.role == UserRole.ADMIN)
        )
        all_admins = set(settings.admin_telegram_ids) | set(db_admins)

    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=msg_text,
            )
        except Exception as exc:
            logger.warning(
                "Не удалось отправить оповещение о заблевании крови админу %s: %s",
                admin_id,
                exc,
            )


async def notify_admins_about_minor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client_age: int,
    parent_name: str,
    parent_phone: str,
) -> None:
    user = update.effective_user
    if not user:
        return

    client_name = html.escape(user.full_name or "Unknown")
    username_str = f" (@{user.username})" if user.username else ""

    msg_text = (
        f"⚠️ <b>Попытка записи несовершеннолетнего!</b>\n\n"
        f"Пользователь: {client_name}{username_str} (ID: {user.id})\n"
        f"Возраст: {client_age}\n"
        f"Родитель: {html.escape(parent_name)}\n"
        f"Телефон родителя: {html.escape(parent_phone)}"
    )

    from database.models import UserRole

    async with AsyncSessionFactory() as session:
        db_admins = await session.scalars(
            select(User.telegram_id).where(User.role == UserRole.ADMIN)
        )
        all_admins = set(get_settings().admin_telegram_ids) | set(db_admins)

    for admin_id in all_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=msg_text, parse_mode="HTML"
            )
        except Exception as exc:
            logger.warning(
                "Не удалось отправить оповещение о несовершеннолетнем админу %s: %s",
                admin_id,
                exc,
            )


async def handle_main_menu_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await register_user(update, context)
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if data == "book":
        await query.edit_message_text(
            await get_stage_text("service_selection"),
            reply_markup=build_service_menu(),
        )
    elif data == "activate_cert":
        # Переход к FSM состояния ввода кода активации
        context.user_data["booking_state"] = "await_cert_activation"
        context.user_data["history"] = []
        await query.edit_message_text(
            "Пожалуйста, введите 5-значный цифровой код вашего подарочного сертификата для его последующей активации:",
            reply_markup=build_back_button()
        )
    elif data == "support":
        from handlers.chat_bridge import create_support_ticket

        await query.answer()
        try:
            await query.delete_message()
        except Exception:
            pass
        await create_support_ticket(update, context)
    elif data == "healing":
        async with AsyncSessionFactory() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == query.from_user.id)
            )

        if user and user.has_healing_access:
            await query.edit_message_text(
                "✨ <b>Инструкции по заживлению</b> ✨\n\nВыберите интересующую вас зону прокола:",
                reply_markup=build_client_healing_zones_keyboard(),
                parse_mode="HTML"
            )
        else:
            healing_denied_text = await get_custom_text("custom_txt:healing_denied", DEFAULT_CUSTOM_TEXTS["healing_denied"])
            await query.edit_message_text(
                healing_denied_text,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Назад", callback_data="back:main")]]
                ),
            )
    elif data.startswith("back:"):
        await query.edit_message_text(
            "Главное меню",
            reply_markup=build_main_menu(),
        )


async def handle_service_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if not data.startswith("service:"):
        return

    service_key = data.split(":", 1)[1]
    service_name = SERVICE_OPTIONS.get(service_key, "Неизвестная услуга")
    context.user_data["selected_service"] = service_name
    context.user_data["history"] = ["service_selection"]

    if service_key == "buy_certificate":
        await transition_to_state(
            update, context, "cert_target_selection", edit_message=True
        )
    elif service_key in ["piercing", "apsize", "downsize"]:
        await transition_to_state(
            update, context, "piercing_zone_selection", edit_message=True
        )
    else:
        await transition_to_state(update, context, "await_tg_link", edit_message=True)


async def handle_cert_target_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик выбора целевой услуги для оформляемого подарочного сертификата."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    # Получаем переданный короткий ключ (например, "anodizing")
    target_key = query.data.split(":", 1)[1]
    
    # ИСПРАВЛЕНО: Преобразуем латинский ключ обратно в читаемое русское название из SERVICE_OPTIONS
    from config.constants import SERVICE_OPTIONS
    target_val = SERVICE_OPTIONS.get(target_key, target_key)
    
    context.user_data["cert_target_service"] = target_val
    context.user_data.setdefault("history", []).append("cert_target_selection")
    
    await transition_to_state(update, context, "await_cert_amount", edit_message=True)


async def handle_zone_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    zone_key = query.data.split(":", 1)[1]
    zone_info = PIERCING_ZONES.get(zone_key)
    if not zone_info:
        return

    context.user_data["temp_piercing_zone_key"] = zone_key
    context.user_data["temp_piercing_zone_name"] = zone_info["name"]
    context.user_data.setdefault("history", []).append("piercing_zone_selection")
    context.user_data["zone_menu_message_id"] = query.message.message_id

    has_image, use_file_id, img_payload = await get_zone_media_payload(
        zone_key, zone_info
    )

    if has_image:
        await query.edit_message_text(
            f"Выбрана зона: {zone_info['name']}. Выберите тип прокола на картинке ниже.",
            reply_markup=None,
        )

        photo_msg = None
        try:
            if use_file_id:
                photo_kwargs = get_photo_send_kwargs(
                    update,
                    photo=img_payload,
                    caption=f"Выберите тип прокола для зоны {zone_info['name']}:",
                    reply_markup=build_piercing_types_menu(zone_key),
                )
                photo_msg = await update.effective_chat.send_photo(**photo_kwargs)
            else:
                with open(img_payload, "rb") as f:
                    photo_kwargs = get_photo_send_kwargs(
                        update,
                        photo=f,
                        caption=f"Выберите тип прокола для зоны {zone_info['name']}:",
                        reply_markup=build_piercing_types_menu(zone_key),
                    )
                    photo_msg = await update.effective_chat.send_photo(**photo_kwargs)
        except Exception as e:
            logger.error("Ошибка при отправке изображения зоны %s: %s", zone_key, e)
            fallback_kwargs = get_send_kwargs(
                update,
                text=f"Выберите тип прокола для зоны {zone_info['name']}:",
                reply_markup=build_piercing_types_menu(zone_key),
            )
            photo_msg = await update.effective_chat.send_message(**fallback_kwargs)

        if photo_msg:
            context.user_data["photo_message_id"] = photo_msg.message_id
        context.user_data["booking_state"] = "piercing_type_selection"
    else:
        await query.edit_message_text(
            text=f"Выберите тип прокола для зоны {zone_info['name']}:",
            reply_markup=build_piercing_types_menu(zone_key),
        )
        context.user_data["booking_state"] = "piercing_type_selection"


async def handle_type_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    parts = query.data.split(":", 2)
    type_name = parts[2]

    context.user_data["temp_piercing_type"] = type_name
    context.user_data.setdefault("history", []).append("piercing_type_selection")

    photo_msg_id = context.user_data.pop("photo_message_id", None)
    zone_menu_msg_id = context.user_data.pop("zone_menu_message_id", None)

    if zone_menu_msg_id and (
        not query.message or zone_menu_msg_id != query.message.message_id
    ):
        if update.effective_chat:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=zone_menu_msg_id
                )
            except Exception as e:
                logger.warning("Не удалось удалить плейсхолдер меню на шаге ФИО: %s", e)

    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(
            "Не удалось удалить сообщение с фото через query.message.delete(): %s", e
        )
        if photo_msg_id and update.effective_chat:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=photo_msg_id
                )
            except Exception:
                pass

    await transition_to_state(update, context, "await_tg_link")


async def handle_booking_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_message is None or update.effective_message.text is None:
        return

    if context.user_data.get("admin_state"):
        return

    text = update.effective_message.text.strip()

    if text in ["Старт", "В начало"]:
        return

    state = context.user_data.get("booking_state")
    if not state:
        await register_user(update, context)

        client_reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Старт"), KeyboardButton("В начало")]], resize_keyboard=True, is_persistent=True
        )
        await update.effective_message.reply_text(
            "Добро пожаловать в студию пирсинга.\nВыберите действие ниже.",
            reply_markup=client_reply_markup,
        )
        await update.effective_message.reply_text(
            "Главное меню:", reply_markup=build_main_menu()
        )
        return

    if state == "await_cert_activation":
        if not (text.isdigit() and len(text) == 5):
            await update.effective_message.reply_text(
                "Некорректный формат. Пожалуйста, отправьте ровно 5 цифр кода вашего сертификата:",
                reply_markup=build_back_button()
            )
            return

        from database.models import Certificate
        async with AsyncSessionFactory() as session:
            cert = await session.get(Certificate, text)
            if not cert:
                await update.effective_message.reply_text(
                    "Сертификат с указанным кодом не найден. Проверьте правильность ввода кода:",
                    reply_markup=build_back_button()
                )
                return
            if cert.is_used:
                await update.effective_message.reply_text(
                    "Указанный подарочный сертификат уже был успешно использован ранее.",
                    reply_markup=build_back_button()
                )
                return

            target_service = cert.target_service

        # Сброс и сохранение сессии активации
        clear_booking_session(context.user_data)
        context.user_data["active_cert_code"] = text
        context.user_data["selected_service"] = target_service
        context.user_data["history"] = ["await_cert_activation"]

        await update.effective_message.reply_text(
            f"✅ <b>Сертификат успешно верифицирован!</b>\n\n"
            f"Услуга по сертификату: <b>{target_service}</b>\n\n"
            f"Переходим к заполнению ваших данных."
        )

        if target_service in SERVICES_WITH_ZONE:
            await transition_to_state(update, context, "piercing_zone_selection")
        else:
            await transition_to_state(update, context, "await_tg_link")
        return

    elif state == "await_cert_amount":
        try:
            amount = int(text)
            if amount <= 0:
                raise ValueError()
        except ValueError:
            await update.effective_message.reply_text(
                "Пожалуйста, укажите корректное целое число (стоимость сертификата в рублях):",
                reply_markup=build_back_button()
            )
            return
            
        context.user_data["cert_amount"] = amount
        context.user_data.setdefault("history", []).append("await_cert_amount")
        await transition_to_state(update, context, "await_tg_link")
        return

    elif state == "await_tg_link":
        if not ("t.me/" in text or text.startswith("@") or "telegram.me" in text):
            await update.effective_message.reply_text(
                "Пожалуйста, введите корректную ссылку на ваш Telegram-профиль (например, https://t.me/username или @username):",
                reply_markup=build_back_button(),
            )
            return

        context.user_data["client_tg_link"] = text
        context.user_data.setdefault("history", []).append("await_tg_link")
        
        # Если оформляется покупка подарочного сертификата — полностью пропускаем возрастные и медицинские вопросы!
        if context.user_data.get("selected_service") == SERVICE_OPTIONS["buy_certificate"]:
            await transition_to_state(update, context, "await_prepayment")
        else:
            await transition_to_state(update, context, "await_age")
        return

    elif state == "await_age":
        try:
            age = int(text)
        except ValueError:
            await update.effective_message.reply_text(
                "Пожалуйста, введите возраст цифрами.", reply_markup=build_back_button()
            )
            return

        context.user_data["client_age"] = age
        context.user_data.setdefault("history", []).append("await_age")

        if age < 18:
            next_state = "await_parent_name"
        else:
            if context.user_data.get("selected_service") in SERVICES_WITH_MEDICAL:
                next_state = "medical_question_1"
            else:
                next_state = "await_prepayment"

        await transition_to_state(update, context, next_state)
        return

    elif state == "await_parent_name":
        context.user_data["parent_name"] = text
        context.user_data.setdefault("history", []).append("await_parent_name")
        await transition_to_state(update, context, "await_parent_phone")
        return

    elif state == "await_parent_phone":
        context.user_data["parent_phone"] = text
        context.user_data.setdefault("history", []).append("await_parent_phone")

        client_age = context.user_data.get("client_age", 0)
        parent_name = context.user_data.get("parent_name", "Не указано")
        parent_phone = text

        await notify_admins_about_minor(
            update, context, client_age, parent_name, parent_phone
        )

        if context.user_data.get("selected_service") in SERVICES_WITH_MEDICAL:
            next_state = "medical_question_1"
        else:
            next_state = "await_prepayment"

        await transition_to_state(update, context, next_state)
        return

    elif state == "await_date":
        try:
            input_date = datetime.strptime(text, "%d.%m.%Y")
            today = datetime.now(timezone(timedelta(hours=5))).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            input_date_local = input_date.replace(tzinfo=timezone(timedelta(hours=5)))
            if input_date_local < today:
                await update.effective_message.reply_text(
                    "Дата не может быть в прошлом. Пожалуйста, введите корректную будущую дату в формате ДД.ММ.ГГГГ:",
                    reply_markup=build_back_button(),
                )
                return
            
            start_date, end_date = get_allowed_booking_range()
            if not (start_date <= input_date.date() <= end_date):
                start_str = start_date.strftime("%d.%m.%Y")
                end_str = end_date.strftime("%d.%m.%Y")
                await update.effective_message.reply_text(
                    f"Извините, сейчас запись доступна только на период с <b>{start_str}</b> по <b>{end_str}</b>.\n"
                    f"Пожалуйста, введите другую дату из этого диапазона (ДД.ММ.ГГГГ):",
                    reply_markup=build_back_button(),
                    parse_mode="HTML"
                )
                return
        except ValueError:
            await update.effective_message.reply_text(
                "Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ (например, 25.07.2026):",
                reply_markup=build_back_button(),
            )
            return

        context.user_data["requested_date"] = text
        context.user_data.setdefault("history", []).append("await_date")
        await transition_to_state(update, context, "select_slot")
        return

    elif state.startswith("await_medical_text_"):
        q_num = int(state.split("_")[-1])
        q_info = MEDICAL_QUESTIONS[q_num]

        context.user_data.setdefault("medical_answers", {})
        context.user_data["medical_answers"][q_info["db_field"]] = text
        context.user_data.setdefault("history", []).append(state)

        if q_num < 6:
            next_state = f"medical_question_{q_num + 1}"
            await transition_to_state(update, context, next_state)
        else:
            await initiate_medical_review(update, context)
        return


async def handle_booking_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    if data == "booking_back":
        await handle_booking_back(update, context)
        return

    if data == "back:service":
        await query.edit_message_text("Главное меню", reply_markup=build_main_menu())
        return

    if data == "change_date":
        context.user_data["booking_state"] = "await_date"
        await query.edit_message_text(
            await get_stage_text("await_date"), reply_markup=build_back_button()
        )
        return

    if data.startswith("check_date:"):
        date_str = data.split(":", 1)[1]
        try:
            target_date = datetime.strptime(date_str, "%d.%m.%Y").replace(
                tzinfo=timezone(timedelta(hours=5))
            )
        except ValueError:
            await query.answer("Неверный формат даты в системе.", show_alert=True)
            return

        start_date, end_date = get_allowed_booking_range()
        if not (start_date <= target_date.date() <= end_date):
            start_str = start_date.strftime("%d.%m.%Y")
            end_str = end_date.strftime("%d.%m.%Y")
            await query.answer(
                f"Запись на эту дату недоступна. Допустимый диапазон: с {start_str} по {end_str}.",
                show_alert=True
            )
            return

        await process_date_availability(update, context, target_date, edit_message=True)
        return

    if data.startswith("medical:"):
        q_state = context.user_data.get("booking_state", "")
        if q_state.startswith("medical_question_"):
            q_num = int(q_state.split("_")[-1])
            q_info = MEDICAL_QUESTIONS[q_num]
            answer = data.split(":", 1)[1]

            context.user_data.setdefault("medical_answers", {})

            if q_num == 1 and answer == "yes":
                await notify_admins_about_blood_disease(update, context)
                clear_booking_session(context.user_data)

                text = (
                    "К сожалению, мы не сможем записать вас на прием, "
                    "так как мастер не работает с клиентами, имеющими заболевания крови.\n"
                    "Приносим извинения за неудобства."
                )
                await query.edit_message_text(text, reply_markup=build_main_menu())
                return

            if answer in ["yes", "bad"]:
                context.user_data.setdefault("history", []).append(q_state)
                next_state = q_info["detail_state"]
                await transition_to_state(
                    update, context, next_state, edit_message=True
                )
            else:
                ans_text = "Нет" if answer == "no" else "Хорошая"
                context.user_data["medical_answers"][q_info["db_field"]] = ans_text
                context.user_data.setdefault("history", []).append(q_state)

                if q_num < 6:
                    next_state = f"medical_question_{q_num + 1}"
                    await transition_to_state(
                        update, context, next_state, edit_message=True
                    )
                else:
                    await query.answer()
                    await query.edit_message_text("Ответы на анкету приняты.")
                    await initiate_medical_review(update, context)
        return

    if data.startswith("book_slot:"):
        slot_value = data.split(":", 1)[1]
        try:
            slot_dt = datetime.fromisoformat(slot_value)
        except ValueError:
            await query.edit_message_text(
                "Не удалось распознать выбранный слот. Попробуйте ещё раз."
            )
            return

        slot_display = slot_dt.strftime("%d.%m.%Y в %H:%M")

        settings = get_settings()
        calendar_service = GoogleCalendarService(settings)

        if update.effective_user is None:
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        direct_messages_topic_id = None
        if update.effective_message:
            if getattr(update.effective_message, "direct_messages_topic", None):
                direct_messages_topic_id = (
                    update.effective_message.direct_messages_topic.topic_id
                )
            elif update.effective_message.message_thread_id:
                direct_messages_topic_id = update.effective_message.message_thread_id

        async with AsyncSessionFactory() as session:
            user = await session.scalar(
                select(User).where(User.telegram_id == update.effective_user.id)
            )
            if user is None:
                await query.edit_message_text(
                    "Сначала требуется регистрация. Попробуйте /start."
                )
                return

            selected_service = context.user_data.get("selected_service", "Unknown")

            booking = Booking(
                user_id=user.telegram_id,
                client_name=update.effective_user.full_name or "Unknown",
                client_phone=context.user_data.get("client_tg_link", ""),
                client_age=context.user_data.get("client_age", 0),
                service_name=selected_service,
                piercing_zone=context.user_data.get("temp_piercing_zone_name"),
                piercing_type=context.user_data.get("temp_piercing_type"),
                parent_name=context.user_data.get("parent_name"),
                parent_phone=context.user_data.get("parent_phone"),
                has_parent_consent=True
                if context.user_data.get("client_age", 0) < 18
                else None,
                blood_disease=context.user_data.get("medical_answers", {}).get(
                    "blood_disease"
                ),
                blood_clotting=context.user_data.get("medical_answers", {}).get(
                    "blood_clotting"
                ),
                current_medication=context.user_data.get("medical_answers", {}).get(
                    "current_medication"
                ),
                chronic_disease=context.user_data.get("medical_answers", {}).get(
                    "chronic_disease"
                ),
                healing_issues=context.user_data.get("medical_answers", {}).get(
                    "healing_issues"
                ),
                skin_disease=context.user_data.get("medical_answers", {}).get(
                    "skin_disease"
                ),
                date_time=slot_dt.replace(tzinfo=None),
                status=BookingStatus.CONFIRMED,
                chat_id=chat_id,
                direct_messages_topic_id=direct_messages_topic_id,
            )
            session.add(booking)
            
            user.is_prepaid = False
            
            # Логика списания сертификата по Варианту А
            active_cert_code = context.user_data.get("active_cert_code")
            cert_info_for_desc = ""
            if active_cert_code:
                from database.models import Certificate
                cert = await session.get(Certificate, active_cert_code)
                if cert:
                    cert.is_used = True
                    cert.used_by_user_id = user.telegram_id
                    cert.used_at = datetime.utcnow()
                    cert_info_for_desc = f"\nАктивировано по сертификату: {active_cert_code} (Номинал: {cert.amount} руб.)"
            
            await session.commit()
            await session.refresh(booking)

            if selected_service in SERVICES_GROUP_A:
                end_dt = slot_dt + timedelta(minutes=30)
            else:
                end_dt = slot_dt + timedelta(hours=1)

            desc_lines = [
                f"Клиент: {booking.client_name}",
                f"Контакт (Telegram): {booking.client_phone}",
                f"Возраст: {booking.client_age}",
                f"Услуга: {booking.service_name}",
            ]
            if booking.piercing_zone:
                desc_lines.append(f"Зона пирсинга: {booking.piercing_zone}")
            if booking.piercing_type:
                desc_lines.append(f"Тип пирсинга: {booking.piercing_type}")

            if booking.client_age < 18:
                desc_lines.append(
                    f"Родитель: {booking.parent_name} ({booking.parent_phone})"
                )
                desc_lines.append("Согласие родителя: Да (автоматически)")

            if booking.blood_disease:
                desc_lines.append(f"Заболевания крови: {booking.blood_disease}")
            if booking.blood_clotting:
                desc_lines.append(f"Свертываемость: {booking.blood_clotting}")
            if booking.current_medication:
                desc_lines.append(
                    f"Принимаемые лекарства: {booking.current_medication}"
                )
            if booking.chronic_disease:
                desc_lines.append(f"Хронические заболевания: {booking.chronic_disease}")
            if booking.healing_issues:
                desc_lines.append(f"Проблемы с заживлением: {booking.healing_issues}")
            if booking.skin_disease:
                desc_lines.append(f"Кожные заболевания: {booking.skin_disease}")

            # Интеграция информации об активации сертификата в Google Календарь
            if cert_info_for_desc:
                desc_lines.append(cert_info_for_desc)

            calendar_event_id = await calendar_service.create_booking_event(
                booking_summary=f"Запись: {booking.client_name} ({booking.service_name})",
                description="\n".join(desc_lines),
                start_dt=slot_dt,
                end_dt=end_dt,
            )

            if calendar_event_id:
                booking.google_event_id = calendar_event_id
                await session.commit()

        async with AsyncSessionFactory() as session:
            location_settings = await session.scalars(select(StudioSetting))
            settings_map = {item.key: item.value for item in location_settings}

        address_text = settings_map.get("address_text", "Адрес студии будет уточнен")
        latitude = settings_map.get("latitude")
        longitude = settings_map.get("longitude")

        booking_confirmed_tpl = await get_custom_text(
            "custom_txt:booking_confirmed",
            DEFAULT_CUSTOM_TEXTS["booking_confirmed"]
        )
        booking_message = booking_confirmed_tpl.format(
            slot_display=slot_display,
            address_text=address_text
        )
        
        if latitude and longitude:
            booking_message += f"\nКоординаты: {latitude}, {longitude}"

        if calendar_event_id:
            await query.edit_message_text(
                booking_message + "\nСобытие добавлено в Google Calendar.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Главное меню", callback_data="back:main")]]
                ),
            )
        else:
            await query.edit_message_text(
                booking_message
                + "\nGoogle Calendar временно недоступен, но запись уже зарегистрирована.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Главное меню", callback_data="back:main")]]
                ),
            )

        if update.effective_chat is not None:
            booking_kwargs = get_send_kwargs(update, booking_message)
            await update.effective_chat.send_message(**booking_kwargs)

        clear_booking_session(context.user_data)
        return


async def handle_client_healing_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    async with AsyncSessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == query.from_user.id)
        )

    if not user or not user.has_healing_access:
        healing_denied_text = await get_custom_text(
            "custom_txt:healing_denied", DEFAULT_CUSTOM_TEXTS["healing_denied"]
        )
        await query.answer("Доступ ограничен.", show_alert=True)
        await query.edit_message_text(
            healing_denied_text,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Назад", callback_data="back:main")]]
            ),
        )
        return

    data = query.data or ""
    parts = data.split(":")
    action = parts[1]

    if action == "zones":
        await query.edit_message_text(
            "✨ <b>Инструкции по заживлению</b> ✨\n\nВыберите интересующую вас зону прокола:",
            reply_markup=build_client_healing_zones_keyboard(),
            parse_mode="HTML"
        )
    elif action == "select_zone":
        zone_key = parts[2]
        zone_name = PIERCING_ZONES.get(zone_key, {}).get("name", zone_key)
        await query.edit_message_text(
            f"Проколы в зоне <b>{zone_name}</b>:\n\nВыберите прокол для получения детальной инструкции:",
            reply_markup=build_client_healing_types_keyboard(zone_key),
            parse_mode="HTML"
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
            await query.answer("Произошла ошибка: неверный тип прокола.", show_alert=True)
            return

        type_name = zone_info["types"][type_idx]
        
        async with AsyncSessionFactory() as session:
            template = await session.scalar(
                select(MediaTemplate).where(MediaTemplate.key == f"healing_txt:{zone_key}:{type_name}")
            )
            
            if template and template.value:
                healing_text = template.value
            else:
                setting = await session.get(StudioSetting, "healing_instructions")
                if setting and setting.value:
                    healing_text = setting.value
                else:
                    healing_text = (
                        "Инструкция по заживлению пирсинга:\n\n"
                        "1. Не трогайте прокол руками.\n"
                        "2. Обрабатывайте физраствором 2-3 раза в день.\n"
                        "3. Избегайте саун, бассейнов и открытых водоемов первые 2-4 недели.\n"
                        "4. Не проворачивайте украшение."
                    )

        await query.edit_message_text(
            text=healing_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data=f"client_healing:select_zone:{zone_key}")]
            ]),
            parse_mode=None
        )


client_handlers = [
    CallbackQueryHandler(
        handle_main_menu_callback, pattern=r"^(book|support|healing|activate_cert|back:main)$"
    ),
    CallbackQueryHandler(handle_service_selection, pattern=r"^service:"),
    CallbackQueryHandler(handle_cert_target_selection, pattern=r"^cert_target:"), # Новое
    CallbackQueryHandler(handle_zone_selection_callback, pattern=r"^p_zone:"),
    CallbackQueryHandler(handle_type_selection_callback, pattern=r"^p_type:"),
    CallbackQueryHandler(handle_resume_booking, pattern=r"^resume_booking$"),
    CallbackQueryHandler(handle_resume_after_pay, pattern=r"^resume_after_pay$"),
    CallbackQueryHandler(handle_client_healing_callback, pattern=r"^client_healing:"),
    CallbackQueryHandler(
        handle_booking_callback,
        pattern=r"^(back:service|slot:|medical:|change_date|check_date:|book_slot:|booking_back)",
    ),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_booking_input),
]
