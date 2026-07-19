FROM python:3.13-slim-bookworm

# Настройка переменных окружения для Python и pip
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Глобальная активация виртуального окружения (путь к venv ставится на первое место)
    PATH="/app/venv/bin:$PATH"

# Создаем системного пользователя и группу, а также подготавливаем директорию с нужными правами
RUN groupadd --system app && useradd --system --gid app --create-home app \
    && mkdir /app && chown app:app /app

WORKDIR /app

# Переключаемся на пользователя app до начала установки зависимостей.
# Все последующие файлы и папки (включая venv) будут сразу создаваться с правами пользователя app.
USER app

# Шаг 1: Копируем только файл зависимостей
COPY --chown=app:app requirements.txt ./

# Шаг 2: Создаем виртуальное окружение и устанавливаем зависимости.
# Поскольку папка /app/venv/bin уже находится в PATH, pip автоматически установит всё внутрь venv.
RUN python -m venv venv \
    && pip install --upgrade pip \
    && pip install -r requirements.txt

# Шаг 3: Копируем остальной исходный код приложения с сохранением прав
COPY --chown=app:app . .

# При запуске контейнер автоматически использует правильный Python из venv
CMD ["python", "main.py"]
