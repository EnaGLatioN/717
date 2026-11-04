#!/bin/bash
set -e

echo "== Переменные окружения =="
env | grep -E '^[A-Z0-9_]+' || echo "Нет переменных."

ENV_IP="${ENV_IP:-0.0.0.0}"
ENV_PORT="${ENV_PORT:-8000}"
echo "Используемый IP:   $ENV_IP"
echo "Используемый порт: $ENV_PORT"

# на всякий случай: если запустили не из /application/main
cd /application/main

echo "== Применяем миграции =="
python manage.py migrate --noinput

declare -a PIDS

terminate() {
    echo "== Остановка контейнера =="
    for pid in "${PIDS[@]}"; do
        echo "Завершаем PID=$pid..."
        kill -TERM "$pid" 2>/dev/null || true
    done
    wait
    echo "Все процессы завершены."
    exit 0
}
trap terminate SIGTERM SIGINT

# веб-сервер Django
python manage.py runserver "$ENV_IP:$ENV_PORT" &
PIDS+=($!)

# telegram-бот
python manage.py bot &
PIDS+=($!)

# ждём — контейнер живёт, пока живы процессы
wait