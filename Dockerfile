FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --upgrade pip && \
    pip install uv && \
    uv export --frozen --no-dev -o requirements.txt && \
    uv pip install --system -r requirements.txt && \
    rm requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app app
COPY --from=builder /usr/local /usr/local
COPY . .

RUN chown -R app:app /app
USER app

CMD ["python", "main.py"]
