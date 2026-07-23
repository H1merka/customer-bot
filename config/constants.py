# config/constants.py
from __future__ import annotations

from pathlib import Path

# Корневая папка проекта для поиска медиа-директорий
BASE_DIR = Path(__file__).resolve().parent.parent

SERVICE_OPTIONS = {
    "piercing": "Прокол",
    "apsize": "Апсайз(увеличение украшения)",
    "downsize": "Даунсайз(уменьшение украшения)",
    "cleaning": "Чистка украшения",
    "consultation": "Консультация",
    "jewelry": "Покупка украшения",
    "anodizing": "Анодирование титана украшения",
}

# Наборы услуг для определения логики флоу
SERVICES_WITH_ZONE = {
    SERVICE_OPTIONS["piercing"],
    SERVICE_OPTIONS["apsize"],
    SERVICE_OPTIONS["downsize"],
}

SERVICES_WITH_MEDICAL = {
    SERVICE_OPTIONS["piercing"],
    SERVICE_OPTIONS["consultation"],
}

# Группа А: услуги с 30-минутными слотами и соответствующей сеткой
SERVICES_GROUP_A = {
    SERVICE_OPTIONS["cleaning"],
    SERVICE_OPTIONS["jewelry"],
    SERVICE_OPTIONS["anodizing"],
}

PIERCING_ZONES = {
    "mouth": {
        "name": "Губы/рот",
        "image": "mouth.png",
        "types": [
            "Боковой лабрет",
            "Центральный лабрет",
            "Вертикальный лабрет",
            "Георгины",
            "Джеструм",
            "Язык",
        ],
    },
    "face": {
        "name": "Нос/лицо",
        "image": "face.jpg",
        "types": ["Микродермал", "Хай нострил", "Нострил", "Септум", "Бровь"],
    },
    "body": {"name": "Тело", "image": None, "types": ["Соски", "Пупок"]},
    "ear": {
        "name": "Уши",
        "image": "ear.jpg",
        "types": [
            "Рук",
            "Хеликс",
            "Форвард Хеликс",
            "Флэт",
            "Дэйс",
            "Конч",
            "Трагус",
            "Мочка",
            "Снаг",
            "Индастриал",
            "Лоу Хеликс",
        ],
    },
}

DEFAULT_STAGE_TEXTS = {
    "service_selection": "Выберите услугу:",
    "zone_selection": "Выберите зону пирсинга:",
    "await_tg_link_zone": "Вы выбрали: {service_name} ({zone_name} — {type_name}).\n\nПожалуйста, отправьте ссылку на ваш Telegram-профиль (например, https://t.me/username или @username):",
    "await_tg_link_simple": "Вы выбрали: {service_name}.\n\nПожалуйста, отправьте ссылку на ваш Telegram-профиль (например, https://t.me/username или @username):",
    "await_age": "Введите возраст целым числом.",
    "await_parent_name": "Вы не достигли совершеннолетия. Пожалуйста, введите ФИО вашего родителя или законного представителя.",
    "await_parent_phone": "Введите контактный телефон родителя или законного представителя.",
    "await_date": "Введите желаемую дату для записи в формате ДД.ММ.ГГГГ (например, 25.07.2026):",
    "select_slot": "Свободные слоты на {date_str}:",
    "no_slots": "К сожалению, на {date_str} свободных мест нет. Пожалуйста, выберите соседнюю дату или введите другую:",
}

STAGE_LABELS = {
    "service_selection": "Выбор услуги",
    "zone_selection": "Выбор зоны",
    "await_tg_link_zone": "Ввод TG (с зоной)",
    "await_tg_link_simple": "Ввод TG (без зоны)",
    "await_age": "Ввод возраста",
    "await_parent_name": "ФИО родителя",
    "await_parent_phone": "Телефон родителя",
    "await_date": "Ввод даты",
    "select_slot": "Выбор слота",
    "no_slots": "Нет слотов",
}

MEDICAL_QUESTIONS = {
    1: {
        "text": "Есть ли у тебя какие-нибудь заболевания крови?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "blood_disease",
        "state": "medical_question_1",
        "detail_state": "await_medical_text_1",
        "detail_prompt": "Пожалуйста, напишите подробнее о заболеваниях крови:",
    },
    2: {
        "text": "Свертываемость крови хорошая или плохая?",
        "options": [("Плохая", "medical:bad"), ("Хорошая", "medical:good")],
        "db_field": "blood_clotting",
        "state": "medical_question_2",
        "detail_state": "await_medical_text_2",
        "detail_prompt": "Пожалуйста, напишите подробнее о проблемах со свертываемостью крови:",
    },
    3: {
        "text": "Принимаешь ли ты в настоящее время какие-либо лекарства?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "current_medication",
        "state": "medical_question_3",
        "detail_state": "await_medical_text_3",
        "detail_prompt": "Пожалуйста, укажите принимаемые лекарства:",
    },
    4: {
        "text": "Есть ли хронические заболевания?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "chronic_disease",
        "state": "medical_question_4",
        "detail_state": "await_medical_text_4",
        "detail_prompt": "Пожалуйста, напишите подробнее о хронических заболеваниях:",
    },
    5: {
        "text": "Были ли в прошлом проблемы с заживлением пирсинга или ран?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "healing_issues",
        "state": "medical_question_5",
        "detail_state": "await_medical_text_5",
        "detail_prompt": "Пожалуйста, напишите подробнее о проблемах с заживлением:",
    },
    6: {
        "text": "Есть ли кожные заболевания?",
        "options": [("Да", "medical:yes"), ("Нет", "medical:no")],
        "db_field": "skin_disease",
        "state": "medical_question_6",
        "detail_state": "await_medical_text_6",
        "detail_prompt": "Пожалуйста, напишите подробнее о кожных заболеваниях:",
    },
}


def clear_booking_session(user_data: dict) -> None:
    """Безопасно очищает все временные данные бронирования клиента (DRY)."""
    booking_keys = [
        "booking_state",
        "client_name",
        "client_phone",
        "client_age",
        "parent_name",
        "parent_phone",
        "medical_answers",
        "selected_service",
        "requested_date",
        "last_checked_date",
        "history",
        "admin_state",
        "temp_latitude",
        "temp_longitude",
        "temp_piercing_zone_key",
        "temp_piercing_zone_name",
        "temp_piercing_type",
        "photo_message_id",
        "zone_menu_message_id",
        "client_tg_link",
    ]
    for key in booking_keys:
        user_data.pop(key, None)
