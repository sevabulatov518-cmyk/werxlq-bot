"""Все тексты бота в одном месте — правь под себя, не трогая логику."""

from __future__ import annotations

import html
import os


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


APPLY_BUTTON = "🛒 Купить сигну"
INFO_BUTTON = "ℹ️ О боте"
HELP_BUTTON = "❓ Как это работает"
WRITE_BUTTON = "✍️ Написать по заявке"


# --------------------------------------------------------------- покупатель
def card_block(cfg) -> str:
    lines = [
        "💳 <b>Реквизиты для оплаты</b>",
        f"Банк: <b>{esc(cfg.bank)}</b>",
        f"Получатель: <b>{esc(cfg.card_holder)}</b>",
        f"Карта: <code>{esc(cfg.card_number)}</code>",
    ]
    if cfg.price:
        lines.append(f"Сумма: <b>{esc(cfg.price)}</b>")
    return "\n".join(lines)


def welcome(cfg, name: str) -> str:
    intro = esc(cfg.intro) or "Это бот для заявок."
    offer = f"Здесь можно купить {esc(cfg.product)}." if cfg.product else "Здесь можно сделать заказ."
    return (
        f"👋 Привет{f', {esc(name)}' if name else ''}!\n\n"
        f"{intro}\n\n"
        f"{offer}\n"
        f"Нажми «{APPLY_BUTTON}», пришли ник — бот покажет реквизиты и примет заявку."
    )


def welcome_keyboard() -> list:
    return [
        [btn(APPLY_BUTTON, "pay")],
        [btn(INFO_BUTTON, "info"), btn(HELP_BUTTON, "help")],
    ]


def back_keyboard() -> list:
    return [[btn(APPLY_BUTTON, "pay")], [btn(INFO_BUTTON, "info")]]


def info_text(cfg) -> str:
    lines = ["ℹ️ <b>О боте</b>", ""]
    if cfg.bot_username:
        lines.append(f"🤖 Бот: @{esc(cfg.bot_username)}")
    if cfg.bot_created:
        lines.append(f"📅 Создан: {esc(cfg.bot_created)}")
    if cfg.bot_developer:
        lines.append(f"👨‍💻 Разработчик: @{esc(cfg.bot_developer)}")
    if cfg.bot_for:
        lines.append(f"🎯 Для: {esc(cfg.bot_for)}")

    # Своя строка вместо «🛒 Купить: сигну» — задаётся переменной BOT_INFO.
    # Переводы строк пишутся как \n, потому что поле в панели однострочное.
    custom = (os.environ.get("BOT_INFO") or "").strip().replace("\\n", "\n")
    if custom:
        lines.append(custom)
    elif cfg.product:
        lines.append(f"🛒 Купить: {esc(cfg.product)}")
    return "\n".join(lines)


def help_text(cfg) -> str:
    return (
        "ℹ️ <b>Как это работает</b>\n\n"
        "1. Нажми «" + APPLY_BUTTON + "».\n"
        "2. Пришли ник, на который оформляешь заказ.\n"
        "3. Бот покажет реквизиты — переведи оплату.\n"
        "4. Пришли подтверждение: фото чека или текст с номером перевода.\n"
        "5. Оператор проверит оплату и пришлёт материал в этот чат.\n\n"
        "Команды:\n"
        "/cancel — отменить текущую заявку\n"
        "/status — статус последней заявки\n\n"
        "⚠️ Сообщения вне заявки оператору не отправляются. Ответить оператору "
        "можно реплаем на его сообщение."
    )


def outside_flow_hint(cfg) -> str:
    return (
        f"ℹ️ Чтобы оформить заявку, нажми «{APPLY_BUTTON}».\n\n"
        "Ответить оператору можно реплаем (свайп по его сообщению → «Ответить»). "
        "Остальные сообщения вне заявки оператору не уходят."
    )


def ask_nick(cfg) -> str:
    return (
        "✍️ <b>Шаг 1 из 3 — ник</b>\n\n"
        "Отправь ник, на который оформляешь заказ (одно сообщение, до 64 символов)."
    )


def nick_empty() -> str:
    return "Ник не может быть пустым. Напиши его текстом, например: <code>werxlq_2026</code>"


def nick_too_long() -> str:
    return "Слишком длинный ник (максимум 64 символа). Пришли покороче."


def payment_step(cfg) -> str:
    return (
        "💳 <b>Шаг 2 из 3 — оплата</b>\n\n"
        f"{card_block(cfg)}\n\n"
        "Переведи оплату по реквизитам и пришли подтверждение:\n"
        "фото чека или текст с номером перевода."
    )


def proof_required() -> str:
    return "Пришли подтверждение оплаты: фото чека или текст с номером перевода."


def card_warning(cfg) -> str:
    return (
        "⚠️ Похоже, ты прислал номер банковской карты.\n"
        "Для заявки это не нужно и небезопасно — пришли фото чека, "
        "номер перевода или последние 4 цифры."
    )


def proof_desc(proof: dict) -> str:
    kind = (proof or {}).get("kind")
    if kind == "photo":
        return "фото чека"
    if kind == "document":
        name = (proof or {}).get("name")
        return f"файл ({esc(name)})" if name else "файл с чеком"
    if kind == "video":
        return "видео-подтверждение"
    text = ((proof or {}).get("text") or "").strip()
    if len(text) > 80:
        text = text[:77] + "…"
    return f"«{esc(text)}»" if text else "не указано"


def preview(cfg, nick: str, proof: dict) -> str:
    return (
        "🔍 <b>Шаг 3 из 3 — проверь заявку</b>\n\n"
        f"🎮 Ник: <b>{esc(nick)}</b>\n"
        f"💳 Оплата: {proof_desc(proof)}\n\n"
        "Если всё верно — жми «Отправить заявку»."
    )


def preview_keyboard() -> list:
    return [[btn("✅ Отправить заявку", "confirm")], [btn("✖️ Отмена", "cancel")]]


def order_sent(cfg, order_id: str) -> str:
    return (
        f"✅ Заявка <b>#{esc(order_id)}</b> отправлена.\n\n"
        f"Ожидай: @{esc(cfg.admin_username)} проверит оплату и пришлёт "
        f"{esc(cfg.product) or 'заказ'} в этот чат.\n"
        "Проверить статус: /status"
    )


def nothing_to_send() -> str:
    return f"Сейчас нечего отправлять. Нажми «{APPLY_BUTTON}», чтобы начать заново."


def confirm_reminder() -> str:
    return "Нажми «✅ Отправить заявку» или «✖️ Отмена» под карточкой выше."


def cancelled() -> str:
    return f"✖️ Заявка отменена. Когда будешь готов — нажми «{APPLY_BUTTON}»."


def status_text(cfg, order: dict, when: str) -> str:
    label = {
        "pending": "🕓 ожидает проверки оплаты",
        "awaiting_product": "⏳ оплата подтверждена, оператор готовит материал",
        "accepted": "✅ выполнена, материал отправлен",
        "rejected": "❌ отклонена",
        "cancelled": "🚫 отменена",
    }.get(order.get("status", "pending"), order.get("status", ""))
    return (
        f"📄 <b>Заявка #{esc(order.get('id'))}</b>\n"
        f"🎮 Ник: {esc(order.get('nick'))}\n"
        f"Статус: {label}\n"
        f"Создана: {esc(when)}"
    )


def no_orders_yet() -> str:
    return f"У тебя пока нет заявок. Нажми «{APPLY_BUTTON}», чтобы создать первую."


def rate_limited() -> str:
    return "⏳ Слишком много сообщений подряд. Подожди минуту и напиши снова."


def no_admins(cfg) -> str:
    return (
        "⚠️ Оператор ещё не подключён к боту.\n"
        f"Попроси @{esc(cfg.admin_username)} открыть бота и нажать /start — "
        "после этого заявки начнут доходить."
    )


def buyer_delivered(cfg, order_id: str) -> str:
    return (
        f"✅ <b>Оплата подтверждена</b>\n\n"
        f"Заявка #{esc(order_id)} выполнена. Материал — выше.\n"
        "Спасибо за покупку!"
    )


def buyer_rejected(cfg, order_id: str) -> str:
    return (
        f"❌ <b>Заявка #{esc(order_id)} отклонена</b>\n\n"
        "Оплату не удалось подтвердить. Если считаешь, что это ошибка — "
        "оформи заявку заново с чеком, оператор проверит ещё раз."
    )


def buyer_cancelled(cfg, order_id: str) -> str:
    return (
        f"❌ <b>Заявка #{esc(order_id)} отменена</b>\n\n"
        "Если передумаешь — оформи новую, реквизиты те же."
    )


def cancel_too_late(cfg, order_id: str) -> str:
    return (
        f"⏳ Заявку #{esc(order_id)} уже подтвердил оператор — отменить её нельзя.\n\n"
        "Он готовит материал. Если что-то не так — ответь реплаем на его сообщение."
    )


def already_closed(cfg, order_id: str) -> str:
    return f"Заявка #{esc(order_id)} уже закрыта, отменять нечего."


def admin_buyer_cancelled(order_id: str) -> str:
    return (
        f"🚫 <b>Покупатель отменил заявку #{esc(order_id)}</b>\n\n"
        "Обрабатывать её больше не нужно — карточка закрыта."
    )


def reply_forwarded() -> str:
    return "📨 Передано оператору."


def buyer_got_reply() -> str:
    return "💬 Оператор ответил на твоё сообщение."


# ------------------------------------------------------------------ оператор
def order_card(cfg, order: dict, when: str) -> str:
    username = order.get("username")
    who = f"@{esc(username)}" if username else f"id {order.get('user_id')}"
    return (
        f"🧾 <b>Заявка #{esc(order.get('id'))}</b>\n"
        f"🎮 Ник: <b>{esc(order.get('nick'))}</b>\n"
        f"👤 От: {esc(order.get('name'))} ({who})\n"
        f"💳 Оплата: {proof_desc(order.get('proof') or {})}\n"
        f"🕐 {esc(when)}"
    )


def order_keyboard(order_id: str) -> list:
    return [
        [btn("✅ Подтвердить", f"acc:{order_id}"), btn("❌ Отклонить", f"rej:{order_id}")],
        [btn(WRITE_BUTTON, f"chat:{order_id}")],
    ]


def done_keyboard(order_id: str) -> list:
    return [[btn(WRITE_BUTTON, f"chat:{order_id}")]]


def proof_caption(order_id: str) -> str:
    return f"Подтверждение оплаты к заявке #{esc(order_id)}"


def status_line(status: str, when: str) -> str:
    return {
        "accepted": f"✅ Выполнено: {esc(when)}",
        "rejected": f"❌ Отклонено: {esc(when)}",
        "cancelled": f"🚫 Отменено покупателем: {esc(when)}",
        "awaiting_product": f"⏳ Ожидает фото с материалом: {esc(when)}",
        "pending": f"🕓 Ожидает проверки: {esc(when)}",
    }.get(status, status)


def admin_welcome(cfg, name: str) -> str:
    link = ""
    if cfg.bot_username:
        link = f"\n🔗 Ссылка для покупателей: https://t.me/{esc(cfg.bot_username)}\n"
    return (
        f"🛠 <b>Панель оператора</b>\n\n"
        f"Привет, {esc(name) or 'оператор'}! Сюда приходят заявки.\n"
        f"{link}\n"
        "• Заявка приходит карточкой: ник и подтверждение оплаты.\n"
        "• «✅ Подтвердить» → пришли фото с материалом; подпись к фото "
        "тоже уйдёт покупателю.\n"
        "• «✍️ Написать по заявке» — переписка с покупателем по этой заявке.\n"
        "• «❌ Отклонить» — покупатель получит отказ.\n\n"
        "Посторонние сообщения покупателей сюда НЕ пересылаются — только заявки "
        "и их ответы на твои сообщения.\n\n"
        "Команды:\n"
        "/id — твой chat_id\n"
        "/orders — заявки в ожидании\n"
        "/stats — статистика\n"
        "/link — ссылка на бота для покупателей\n"
        "/cancel — закончить переписку / отменить действие"
    )


def admin_hint() -> str:
    return (
        "Чтобы ответить покупателю, нажми «✍️ Написать по заявке» в карточке заявки.\n"
        "Команды: /start"
    )


def admins_only() -> str:
    return "Эта кнопка доступна только оператору."


def order_missing(order_id: str) -> str:
    return f"Заявка #{esc(order_id)} не найдена."


def ask_product_photo(cfg, order_id: str) -> str:
    return (
        f"📷 <b>Заявка #{esc(order_id)}</b>\n\n"
        "Пришли фото с материалом. Подпись к фото тоже уйдёт покупателю — "
        "можешь написать в ней пару слов или условия.\n\n"
        "Отмена — кнопка ниже."
    )


def product_photo_required(cfg, order_id: str) -> str:
    return (
        f"Нужно именно фото с материалом (или файл-картинка) для заявки #{esc(order_id)}.\n"
        "Подпись к фото тоже дойдёт покупателю. Отправь фото, либо нажми «✖️ Отмена»."
    )


def product_sent(cfg, order_id: str, delivered: bool) -> str:
    if delivered:
        return (
            f"✅ Фото отправлено покупателю. Заявка #{esc(order_id)} закрыта.\n"
            "Хочешь что-то добавить — жми «✍️ Написать по заявке»."
        )
    return (
        f"⚠️ Покупателю не удалось доставить фото по заявке #{esc(order_id)} "
        "(возможно, он заблокировал бота). Отправь материал вручную."
    )


def ask_chat_message(order_id: str) -> str:
    return (
        f"✍️ <b>Переписка по заявке #{esc(order_id)}</b>\n\n"
        "Напиши сообщение покупателю: текст, фото с подписью или файл — "
        "всё уйдёт ему в чат.\n"
        "Можно отправлять сколько угодно сообщений подряд.\n"
        "Закончить: /cancel"
    )


def chat_sent() -> str:
    return "✅ Отправлено покупателю."


def chat_closed() -> str:
    return "Переписка завершена. Карточка заявки — в чате выше."


def chat_failed() -> str:
    return "⚠️ Не удалось отправить: покупатель мог заблокировать бота."


def buyer_reply_header(order_id: str) -> str:
    return (
        f"💬 <b>Ответ покупателя</b> по заявке #{esc(order_id)}\n\n"
        "Нажми «✍️ Ответить», чтобы продолжить переписку."
    )


def reply_button(order_id: str) -> list:
    return [[btn("✍️ Ответить", f"chat:{order_id}")]]


def order_cancelled_accept(order_id: str) -> str:
    return f"↩️ Подтверждение снято: заявка #{esc(order_id)} снова в ожидании."


def already_done(order_id: str) -> str:
    return f"Заявка #{esc(order_id)} уже выполнена."


def already_rejected(order_id: str) -> str:
    return f"Заявка #{esc(order_id)} уже отклонена."


def orders_list(cfg, orders: list) -> str:
    if not orders:
        return "📭 Заявок в ожидании нет."
    lines = [f"🕓 <b>В работе: {len(orders)}</b>", ""]
    for order in orders[:30]:
        username = order.get("username")
        who = f"@{esc(username)}" if username else f"id {order.get('user_id')}"
        state = "ждёт фото" if order.get("status") == "awaiting_product" else "ждёт проверки"
        lines.append(f"#{esc(order.get('id'))} · <b>{esc(order.get('nick'))}</b> · {who} · {state}")
    lines.append("")
    lines.append("Подтверждай и отклоняй кнопками ниже:")
    return "\n".join(lines)


def pending_keyboard(orders: list) -> list:
    """Кнопки под списком заявок: по паре на каждую заявку."""
    rows = []
    for order in orders[:20]:
        order_id = order.get("id", "")
        if not order_id:
            continue
        rows.append([
            btn(f"✅ #{order_id}", f"acc:{order_id}"),
            btn(f"❌ #{order_id}", f"rej:{order_id}"),
            btn("✍️", f"chat:{order_id}"),
        ])
    return rows


def admin_welcome_keyboard() -> list:
    return [[btn("📋 Заявки в ожидании", "orders")]]


def stats_text(cfg, stats: dict, admins: list) -> str:
    return (
        "📊 <b>Статистика</b>\n\n"
        f"Всего заявок: <b>{stats['total']}</b>\n"
        f"🕓 В ожидании: {stats['pending']}\n"
        f"⏳ Ждут фото: {stats.get('awaiting_product', 0)}\n"
        f"✅ Выполнено: {stats['accepted']}\n"
        f"❌ Отклонено: {stats['rejected']}\n"
        f"🚫 Отменено покупателем: {stats.get('cancelled', 0)}\n\n"
        f"Операторы: {len(admins)}"
    )


def link_text(cfg) -> str:
    if cfg.bot_username:
        return (
            "🔗 <b>Ссылка для покупателей</b>\n\n"
            f"https://t.me/{esc(cfg.bot_username)}\n\n"
            "Отправь её покупателю — он откроет бота и оформит заявку."
        )
    return "BOT_USERNAME не задан в .env — ссылку показать не могу."


def unknown_command(cfg) -> str:
    return "Не знаю такую команду. Список команд: /start"
