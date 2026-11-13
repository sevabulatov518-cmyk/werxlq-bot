#!/usr/bin/env bash
# Копия заявок в backups/. Запуск по cron, например:
#   0 4 * * * /opt/werxlq-bot/deploy/backup.sh
set -euo pipefail

APP_DIR="/opt/werxlq-bot"
BACKUP_DIR="${APP_DIR}/backups"
KEEP_DAYS=14

mkdir -p "${BACKUP_DIR}"

if [[ -f "${APP_DIR}/data/state.json" ]]; then
    cp "${APP_DIR}/data/state.json" "${BACKUP_DIR}/state-$(date +%F_%H%M).json"
fi

# лог бота тоже полезен, но он ротируется сам — копируем необязательно
find "${BACKUP_DIR}" -type f -mtime +"${KEEP_DAYS}" -delete
