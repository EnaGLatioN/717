FROM python:3.12.2-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.1.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PATH="/root/.local/bin:$PATH"

# рабочий каталог — там, где manage.py
WORKDIR /application/main

# системные пакеты
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

# манифесты poetry лежат в корне контекста (717/)
COPY pyproject.toml poetry.lock* /application/

# зависимости
WORKDIR /application
RUN poetry install --no-root --no-ansi

# код Django-проекта
COPY main/ /application/main/

# entrypoint
COPY entrypoint.sh /application/entrypoint.sh
RUN chmod +x /application/entrypoint.sh

# возвращаемся туда, где manage.py
WORKDIR /application/main

EXPOSE 8000

ENTRYPOINT ["bash", "/application/entrypoint.sh"]