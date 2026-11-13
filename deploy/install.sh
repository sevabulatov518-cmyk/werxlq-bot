#!/usr/bin/env bash
# Установка бота на Ubuntu/Debian.
# Запуск:  sudo bash deploy/install.sh
set -euo pipefail

APP_DIR="/opt/werxlq-bot"
USER_NAME="werxlq"
SERVICE_NAME="werxlq-bot"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Нужны права root. Запусти: sudo bash deploy/install.sh"
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Исходники: ${SRC_DIR}"
echo "Каталог установки: ${APP_DIR}"

# --- 1. Python -------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "Ставлю Python..."
    apt-get update -y
    apt-get install -y python3
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "Python: ${PY_VERSION}"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "Нужен Python 3.9 или новее, найден ${PY_VERSION}."
    exit 1
fi

# --- 2. Пользователь -------------------------------------------------------
if ! id "${USER_NAME}" >/dev/null 2>&1; then
    echo "Создаю пользователя ${USER_NAME}..."
    useradd --system --shell /usr/sbin/nologin --home "${APP_DIR}" "${USER_NAME}"
fi

# --- 3. Копирование файлов -------------------------------------------------
mkdir -p "${APP_DIR}"
if [[ "${SRC_DIR}" != "${APP_DIR}" ]]; then
    echo "Копирую файлы в ${APP_DIR}..."
    cp -a "${SRC_DIR}/." "${APP_DIR}/"
fi

rm -rf "${APP_DIR}/__pycache__" "${APP_DIR}/tests/__pycache__" "${APP_DIR}/.demo"
mkdir -p "${APP_DIR}/data" "${APP_DIR}/deploy"

# --- 4. Настройки ----------------------------------------------------------
if [[ ! -f "${APP_DIR}/.env" ]]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    echo
    echo "============================================================"
    echo " Файл .env создан из шаблона — впиши в него токен бота:"
    echo "   nano ${APP_DIR}/.env"
    echo " Сохранить: Ctrl+O, Enter, Ctrl+X."
    echo " Потом запусти этот скрипт ещё раз:"
    echo "   sudo bash ${APP_DIR}/deploy/install.sh"
    echo "============================================================"
    exit 2
fi
chmod 600 "${APP_DIR}/.env"

if ! grep -q '^TELEGRAM_BOT_TOKEN=.\+' "${APP_DIR}/.env"; then
    echo "В ${APP_DIR}/.env не заполнен TELEGRAM_BOT_TOKEN."
    echo "Открой файл: nano ${APP_DIR}/.env"
    exit 2
fi

# --- 5. Права --------------------------------------------------------------
chown -R "${USER_NAME}:${USER_NAME}" "${APP_DIR}"
chmod 600 "${APP_DIR}/.env"

# --- 6. systemd ------------------------------------------------------------
echo "Ставлю systemd-сервис..."
install -m 644 "${APP_DIR}/deploy/werxlq-bot.service" "${SERVICE_PATH}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"

sleep 2
echo
systemctl --no-pager --full status "${SERVICE_NAME}" | head -n 12 || true
echo
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "Готово: сервис запущен и работает."
else
    echo "⚠ Сервис не запустился. Посмотри логи:"
    echo "   journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi
echo "  Логи:      journalctl -u ${SERVICE_NAME} -f"
echo "  Перезапуск: systemctl restart ${SERVICE_NAME}"
echo "  Проверка:  python3 ${APP_DIR}/bot.py --check"
