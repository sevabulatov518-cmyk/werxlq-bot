#!/usr/bin/env python3
"""Точка входа Telegram-бота приёма заявок.

Запуск:
    python bot.py            # обычный режим (long polling)
    python bot.py --check    # проверить настройки и выйти
    python bot.py --demo     # показать сценарий без Telegram

Только стандартная библиотека Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from api import TelegramAPI, TelegramError  # noqa: E402
from logic import BotLogic, Config  # noqa: E402
from store import Store  # noqa: E402

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,}$")
ALLOWED_UPDATES = ["message", "callback_query"]
BOT_COMMANDS = [
    {"command": "start", "description": "Начать / показать реквизиты"},
    {"command": "pay", "description": "Я оплатил — отправить заявку"},
    {"command": "status", "description": "Статус последней заявки"},
    {"command": "cancel", "description": "Отменить текущую заявку"},
    {"command": "help", "description": "Как это работает"},
]

log = logging.getLogger("bot")


# ------------------------------------------------------------------ окружение
def load_env_file(path: Path) -> None:
    """Простейший .env: KEY=VALUE, без зависимостей. Уже заданные переменные не трогаем.

    Читает UTF-8 (в том числе с BOM), а если файл сохранён Блокнотом в cp1251 —
    прочитает и его. Это важно: иначе русские значения вроде «Арина Е.» ломали бы запуск.
    """
    if not path.exists():
        return
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(f"Не удалось прочитать {path}: {exc}", file=sys.stderr)
        return

    text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")

    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[7:].strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


def get_token() -> str:
    return _env("TELEGRAM_BOT_TOKEN") or _env("BOT_TOKEN")


def build_config() -> Config:
    admin_ids: list[int] = []
    for chunk in re.split(r"[,;\s]+", _env("ADMIN_IDS")):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            admin_ids.append(int(chunk))

    usernames = [
        name.lstrip("@")
        for name in re.split(r"[,;\s]+", _env("ADMIN_USERNAME", "werxlq") + "," + _env("ADMIN_USERNAMES"))
        if name.strip()
    ]
    if not usernames:
        usernames = ["werxlq"]
    primary, extra = usernames[0], usernames[1:]

    return Config(
        admin_username=primary,
        admin_usernames=extra,
        bot_username=_env("BOT_USERNAME").lstrip("@"),
        bot_created=_env("BOT_CREATED"),
        bot_developer=_env("BOT_DEVELOPER").lstrip("@"),
        bot_for=_env("BOT_FOR"),
        intro=_env("BOT_INTRO", "Это бот для сигн от Вельвет."),
        admin_ids=admin_ids,
        bank=_env("BANK", "Сбер"),
        card_number=_env("CARD_NUMBER", "2202 2084 5871 1745"),
        card_holder=_env("CARD_HOLDER", "Арина Е."),
        price=_env("PRICE"),
        product=_env("PRODUCT", "сигну"),
        support_contact=_env("SUPPORT_CONTACT"),
        rate_limit_count=_env_int("RATE_LIMIT_COUNT", 20),
        rate_limit_window=_env_int("RATE_LIMIT_WINDOW", 60),
    )


def get_proxy() -> str:
    """Прокси для api.telegram.org, если прямой доступ закрыт."""
    for name in ("TELEGRAM_PROXY", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = _env(name)
        if value:
            return value
    return ""


def setup_logging(verbose: bool, log_file: Path | None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            )
        except OSError as exc:
            print(f"Лог-файл недоступен: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%d.%m %H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("urllib").setLevel(logging.WARNING)


def mask_token(token: str) -> str:
    if not token:
        return "—"
    if len(token) <= 14:
        return token[:3] + "…"
    return f"{token[:10]}…{token[-4:]}"


def operator_list(cfg: Config) -> str:
    """Список операторов в том виде, как его вписали в .env."""
    return ", ".join(f"@{name}" for name in [cfg.admin_username, *cfg.admin_usernames])


# ------------------------------------------------------------------- проверка
def run_check(cfg: Config, token: str, data_dir: Path) -> int:
    problems = 0
    print("Проверка настроек\n" + "-" * 40)

    if not token:
        print("❌ TELEGRAM_BOT_TOKEN не задан — создай бота у @BotFather и впиши токен в .env")
        problems += 1
    elif not TOKEN_RE.match(token):
        print(f"❌ Токен не похож на токен Telegram: {mask_token(token)}")
        print("   Формат: 123456789:AAE...  (получи у @BotFather)")
        problems += 1
    else:
        print(f"✅ Токен: {mask_token(token)}")

    print(f"✅ Операторы: {operator_list(cfg)}")
    if cfg.bot_username:
        print(f"✅ Бот: @{cfg.bot_username} · https://t.me/{cfg.bot_username}")
    print(f"✅ Реквизиты: {cfg.card_number} · {cfg.bank} · {cfg.card_holder}")
    print(f"✅ Товар: «Здесь можно купить {cfg.product}»")
    print(
        "✅ О боте: создан "
        f"{cfg.bot_created or '—'}, разработчик @{cfg.bot_developer or '—'}, для {cfg.bot_for or '—'}"
    )
    print(f"{'✅' if cfg.price else 'ℹ️ '} Сумма к оплате: {cfg.price or 'не задана (строка скрыта)'}")
    proxy = get_proxy()
    print(f"{'✅' if proxy else 'ℹ️ '} Прокси: {proxy or 'не используется (прямое подключение)'}")

    state = Store(data_dir / "state.json")
    stats = state.stats()
    print(f"✅ База: {data_dir / 'state.json'}")
    print(f"   заявок: {stats['total']}, в ожидании: {stats['pending']}")
    print(f"   подключённых операторов: {len(state.data['admins'])}")

    if token and TOKEN_RE.match(token):
        try:
            me = TelegramAPI(token, timeout=10, max_retries=0, proxy=proxy).get_me()
            print(f"✅ Telegram отвечает: @{me.get('username')} (id {me.get('id')})")
        except TelegramError as exc:
            print(f"⚠️  Не удалось проверить токен онлайн: {exc.description}")
            print("   Если есть прокси — впиши его в .env: TELEGRAM_PROXY=http://127.0.0.1:8080")
            print("   Без прокси бот всё равно запустится и будет повторять попытки.")

    print("-" * 40)
    print("Готово к запуску." if problems == 0 else "Исправь ошибки выше.")
    return 0 if problems == 0 else 2


# ------------------------------------------------------------- сеть/диагностика
def run_net_check(token: str) -> int:
    """Пошагово проверяет DNS → TCP → HTTPS. Помогает отличить блокировку от кривого токена."""
    host = "api.telegram.org"
    print("Проверка сети до Telegram\n" + "-" * 40)

    print(f"1) DNS: ищу {host} ...")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        addresses = sorted({info[4][0] for info in infos})
        print("   ✅ DNS отвечает: " + ", ".join(addresses))
    except socket.gaierror as exc:
        print(f"   ❌ DNS не смог разрешить имя: {exc}")
        print("      Это ошибка getaddrinfo — та самая, что видна в логе бота.")
        print("      Причины: нет интернета, DNS-сервер недоступен или провайдер")
        print("      блокирует Telegram по DNS.")
        print("      Решения: DNS 1.1.1.1 / 8.8.8.8, VPN или TELEGRAM_PROXY в .env")
        print("-" * 40)
        return 2

    print("2) TCP: подключаюсь к порту 443 ...")
    reachable = False
    for address in addresses[:3]:
        started = time.time()
        try:
            with socket.create_connection((address, 443), timeout=6):
                elapsed = int((time.time() - started) * 1000)
                print(f"   ✅ {address}:443 доступен ({elapsed} мс)")
                reachable = True
                break
        except OSError as exc:
            print(f"   ⚠️  {address}:443 не отвечает: {exc}")

    if not reachable:
        print("   ❌ Ни один адрес не принял соединение: доступ к Telegram закрыт.")
        print("      Решения: VPN или прокси (TELEGRAM_PROXY=http://127.0.0.1:8080 в .env)")
        print("-" * 40)
        return 3

    if not token:
        print("3) HTTPS: токен не задан — проверка API пропущена.")
        print("-" * 40)
        print("DNS и TCP в порядке. Впиши TELEGRAM_BOT_TOKEN в .env.")
        return 2

    print("3) HTTPS: запрашиваю getMe ...")
    proxy = get_proxy()
    if proxy:
        print(f"   (через прокси {proxy})")
    try:
        me = TelegramAPI(token, timeout=15, max_retries=0, proxy=proxy).get_me()
        print(f"   ✅ Telegram ответил: @{me.get('username')} (id {me.get('id')})")
    except TelegramError as exc:
        print(f"   ❌ {exc.description}" + (f" (код {exc.code})" if exc.code else ""))
        print("      Сеть работает, но запрос не прошёл: чаще всего это неверный токен")
        print("      или прокси. Проверь TELEGRAM_BOT_TOKEN у @BotFather → /mybots.")
        print("-" * 40)
        return 4

    print("-" * 40)
    print("Сеть в порядке — бот готов работать.")
    return 0


# ------------------------------------------------------------------ long poll
def run_polling(api: TelegramAPI, logic: BotLogic, store: Store, cfg: Config, poll_timeout: int) -> int:
    offset = int(store.data.get("offset") or 0)
    log.info("Бот запущен. Операторы: %s", operator_list(cfg))
    log.info("Реквизиты: %s · %s · %s", cfg.card_number, cfg.bank, cfg.card_holder)
    if not store.data.get("admins") and not cfg.admin_ids:
        log.warning("Ни один оператор ещё не подключён: пусть %s откроет бота и отправит /start", operator_list(cfg))
    log.info("Остановка: Ctrl+C")

    try:
        api.set_my_commands(BOT_COMMANDS)
    except TelegramError as exc:
        log.warning("setMyCommands: %s (не критично)", exc)

    while True:
        try:
            updates = api.get_updates(offset=offset, timeout=poll_timeout, allowed_updates=ALLOWED_UPDATES)
        except TelegramError as exc:
            if exc.code in (401, 404):
                log.critical("Telegram не принял токен: %s", exc.description)
                log.critical("Проверь TELEGRAM_BOT_TOKEN в .env (и не отзывал ли токен @BotFather).")
                return 2
            if exc.code == 409:
                log.critical("Конфликт 409: этот бот уже запущен в другом месте. Останови второй экземпляр.")
                return 3
            log.warning("getUpdates: %s — пауза 5 c", exc)
            time.sleep(5)
            continue

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset, update_id + 1)
                store.data["offset"] = offset
            logic.handle_update(update)

        if updates:
            store.save()


# ----------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Telegram-бот: заявки на оплату уходят операторам (@werxlq, @Luotx)"
    )
    parser.add_argument("--env", default=str(BASE_DIR / ".env"), help="путь к файлу .env")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="каталог для state.json и логов (по умолчанию data/ рядом с ботом или $DATA_DIR)",
    )
    parser.add_argument("--check", action="store_true", help="проверить настройки и выйти")
    parser.add_argument("--net", action="store_true", help="проверить DNS, TCP и доступ к Telegram API")
    parser.add_argument("--demo", action="store_true", help="показать сценарий работы без Telegram")
    parser.add_argument("--poll-timeout", type=int, default=25, help="таймаут long polling, сек")
    parser.add_argument("--verbose", "-v", action="store_true", help="подробный лог")
    parser.add_argument("--reset-data", action="store_true", help="очистить заявки и счётчик и выйти")
    return parser.parse_args(argv)


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def reset_data_if_requested(data_dir: Path, force: bool = False) -> bool:
    """Очистка заявок и счётчика по запросу.

    Сохраняет список операторов и позицию в очереди Telegram, чтобы после сброса
    никому не пришлось заново нажимать /start и старые сообщения не переигрались.
    Срабатывает один раз: после сброса кладётся маркер .reset_done, поэтому
    включённая переменная RESET_DATA не стирает данные при каждом старте.
    """
    if not (force or _truthy(_env("RESET_DATA"))):
        return False

    marker = data_dir / ".reset_done"
    if marker.exists():
        return False

    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "state.json"

    admins: list = []
    offset = 0
    if state_path.exists():
        try:
            old = json.loads(state_path.read_text(encoding="utf-8-sig"))
            if isinstance(old, dict):
                admins = old.get("admins") or []
                offset = old.get("offset") or 0
        except (OSError, ValueError) as exc:
            print(f"Старое состояние не прочитано ({exc}), начинаю с чистого листа", file=sys.stderr)

    fresh = {
        "version": 1,
        "offset": offset,
        "admins": admins,
        "sessions": {},
        "orders": {},
        "msgmap": {},
        "counter": 1,
    }
    try:
        state_path.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
        marker.write_text("reset", encoding="utf-8")
    except OSError as exc:
        print(f"Не удалось сбросить данные: {exc}", file=sys.stderr)
        return False

    print("RESET_DATA: заявки очищены, нумерация начнётся с #1. Операторы сохранены.")
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file(Path(args.env))

    # Каталог данных: --data-dir, затем $DATA_DIR, затем ./data рядом с ботом.
    # Нужно для хостингов, где рабочая папка доступна только на чтение.
    data_dir = Path(args.data_dir or _env("DATA_DIR") or (BASE_DIR / "data"))

    if args.demo:
        import demo  # локальный модуль

        return demo.main()

    setup_logging(args.verbose, None if (args.check or args.net) else data_dir / "bot.log")

    cfg = build_config()
    token = get_token()

    reset_data_if_requested(data_dir, force=args.reset_data)
    if args.reset_data:
        return 0

    if args.net:
        return run_net_check(token)

    if args.check:
        return run_check(cfg, token, data_dir)

    if not token:
        log.critical("Не задан TELEGRAM_BOT_TOKEN. Скопируй .env.example в .env и впиши токен.")
        return 2
    if not TOKEN_RE.match(token):
        log.critical("Токен выглядит неверно: %s", mask_token(token))
        return 2

    api = TelegramAPI(token, proxy=get_proxy())
    store = Store(data_dir / "state.json")
    logic = BotLogic(api, store, cfg)
    if api.proxy:
        log.info("Использую прокси: %s", api.proxy)

    try:
        return run_polling(api, logic, store, cfg, max(1, args.poll_timeout))
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")
        store.save()
        return 0


if __name__ == "__main__":
    sys.exit(main())
