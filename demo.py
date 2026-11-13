"""Демонстрация полного сценария без Telegram.

Запуск:  python bot.py --demo

Внутри — тот же BotLogic, что и в бою, но вместо сети подставлен ConsoleAPI,
который просто печатает сообщения. Удобно показать заказчику, как всё выглядит.
"""

from __future__ import annotations

import html
import itertools
import re
import shutil
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from logic import BotLogic, Config  # noqa: E402
from store import Store  # noqa: E402

TAG_RE = re.compile(r"<[^>]+>")


class ConsoleAPI:
    """Печатает вызовы Bot API в консоль и возвращает правдоподобные ответы."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdout
        self.labels: dict[int, str] = {}
        self._message_id = 500

    # ------------------------------------------------------------- вывод
    def label(self, chat_id: int, name: str) -> None:
        self.labels[chat_id] = name

    def _who(self, chat_id) -> str:
        return self.labels.get(chat_id, f"id {chat_id}")

    def note(self, text: str) -> None:
        self.stream.write(f"\n▸ {text}\n")

    def _line(self, text: str = "") -> None:
        self.stream.write(text + "\n")

    @staticmethod
    def plain(text: str) -> str:
        return html.unescape(TAG_RE.sub("", text or ""))

    def _block(self, header: str, body: str, buttons=None) -> None:
        self._line(header)
        for line in (body or "").splitlines() or [""]:
            self._line(f"   {line}")
        if buttons:
            flat = "  ".join(f"[{button['text']}]" for row in buttons for button in row)
            self._line(f"   ↳ кнопки: {flat}")

    def _next_id(self) -> int:
        self._message_id += 1
        return self._message_id

    # -------------------------------------------------------------- API
    def call(self, method: str, **params):
        if method == "sendMessage":
            keyboard = (params.get("reply_markup") or {}).get("inline_keyboard")
            self._block(
                f"🤖 бот → {self._who(params.get('chat_id'))}",
                self.plain(params.get("text", "")),
                keyboard,
            )
            return {"message_id": self._next_id(), "chat": {"id": params.get("chat_id")}}

        if method in ("sendPhoto", "sendDocument", "sendVideo"):
            icon = {"sendPhoto": "📷 фото", "sendDocument": "📄 файл", "sendVideo": "🎬 видео"}[method]
            keyboard = (params.get("reply_markup") or {}).get("inline_keyboard")
            self._block(
                f"🤖 бот → {self._who(params.get('chat_id'))} · {icon}",
                self.plain(params.get("caption", "")),
                keyboard,
            )
            return {"message_id": self._next_id()}

        if method in ("forwardMessage", "copyMessage"):
            self._line(
                f"🤖 бот → {self._who(params.get('chat_id'))} · ↪️ переслано сообщение "
                f"(id {params.get('message_id')})"
            )
            return {"message_id": self._next_id()}

        if method == "editMessageText":
            tail = self.plain(params.get("text", "")).splitlines()[-1:]
            buttons = (params.get("reply_markup") or {}).get("inline_keyboard") or []
            hint = ""
            if buttons:
                hint = "  " + " ".join(f"[{b['text']}]" for row in buttons for b in row)
            self._line(
                f"🤖 бот → {self._who(params.get('chat_id'))} · ✏️ карточка обновлена: "
                f"{tail[0] if tail else ''}{hint}"
            )
            return True

        if method == "answerCallbackQuery":
            return True

        if method == "getMe":
            return {"id": 0, "username": "demo_bot", "first_name": "Demo"}

        if method == "setMyCommands":
            return True

        return True


class Actor:
    """Пользователь в демо: шлёт сообщения и «нажимает» кнопки."""

    _ids = itertools.count(1)

    def __init__(self, api: ConsoleAPI, logic: BotLogic, user_id: int, title: str,
                 username: str | None = None, first_name: str = "") -> None:
        self.api = api
        self.logic = logic
        self.user_id = user_id
        self.title = title
        self.username = username
        self.first_name = first_name or title
        api.label(user_id, title)

    def _from(self) -> dict:
        payload = {"id": self.user_id, "is_bot": False, "first_name": self.first_name}
        if self.username:
            payload["username"] = self.username
        return payload

    def says(self, text: str | None = None, narration: str | None = None, reply_to: int | None = None, **extra):
        message = {
            "message_id": next(self._ids),
            "from": self._from(),
            "chat": {"id": self.user_id, "type": "private"},
        }
        if text is not None:
            message["text"] = text
        message.update(extra)
        if reply_to:
            message["reply_to_message"] = {"message_id": reply_to, "from": {"id": 0}}
        self.api.note(f"{self.title}: {narration or text or '[вложение]'}")
        self.logic.handle_update({"update_id": next(self._ids), "message": message})

    def presses(self, data: str, narration: str):
        self.api.note(f"{self.title}: {narration}")
        update = {
            "update_id": next(self._ids),
            "callback_query": {
                "id": f"cb{next(self._ids)}",
                "from": self._from(),
                "data": data,
                "message": {
                    "message_id": next(self._ids),
                    "chat": {"id": self.user_id, "type": "private"},
                },
            },
        }
        self.logic.handle_update(update)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    print("=" * 72)
    print("ДЕМО: полный сценарий бота, без подключения к Telegram")
    print("=" * 72)
    print("Операторы — @werxlq и @Luotx. Покупатель: привет → ник → оплата → чек.")
    print("Оператор: подтверждение фото с подписью + переписка по заявке.")
    print("Все сообщения печатаются так, как их увидели бы люди в Telegram.\n")

    state_dir = Path(__file__).resolve().parent / ".demo"
    try:
        return _run(state_dir)
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def _run(state_dir: Path) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    api = ConsoleAPI()
    store = Store(state_dir / "state.json")
    logic = BotLogic(
        api,
        store,
        Config(
            admin_usernames=["Luotx"],
            bot_username="Ve1vetz_bot",
            bot_created="21.10.2026",
            bot_developer="Luotx",
            bot_for="Ve1vetzy",
        ),
        strict=True,
    )

    admin = Actor(api, logic, 999, "werxlq", username="werxlq", first_name="Арина")
    buyer = Actor(api, logic, 111, "покупатель", username="ivan_petrov", first_name="Иван")

    # 1. Оператор подключается к боту.
    admin.says("/start", narration="/start — подключается как оператор")

    # 2. Покупатель открывает бота: приветствие без реквизитов.
    buyer.says("/start", narration="/start — открыл бота")
    buyer.presses("info", "нажал «ℹ️ О боте»")

    # 3. Шаг 1 — ник.
    buyer.presses("pay", "нажал «🛒 Купить сигну»")
    buyer.says("werxlq_2026", narration="прислал ник: werxlq_2026")

    # 4. Шаг 2 — реквизиты и оплата, шаг 3 — подтверждение.
    buyer.says(
        photo=[
            {"file_id": "AgACAgIAAxkBAAIB", "width": 90, "height": 160},
            {"file_id": "AgACAgIAAxkBAAIC", "width": 1280, "height": 2280},
        ],
        narration="прислал фото чека",
    )
    buyer.presses("confirm", "нажал «✅ Отправить заявку»")

    order_id = next(iter(store.orders))

    # 5. Оператор подтверждает — бот просит фото с материалом.
    admin.presses(f"acc:{order_id}", "нажал «✅ Подтвердить»")

    # 6. Оператор присылает фото С ПОДПИСЬЮ — она уходит покупателю.
    admin.says(
        photo=[{"file_id": "AgACAgIAAxkBAAID", "width": 1280, "height": 720}],
        caption="Держи сигну, ник активирован. Если что — пиши сюда.",
        narration="прислал фото с сигной и подписью",
    )

    # 7. Оператор продолжает переписку по заявке.
    admin.presses(f"chat:{order_id}", "нажал «✍️ Написать по заявке»")
    admin.says("Если появятся вопросы — я на связи 👍")

    # 8. Покупатель отвечает реплаем на сообщение оператора — ответ доходит до админов.
    keys = [key for key in store.data["msgmap"] if key.startswith("111:")]
    last_to_buyer = max(int(key.split(":")[1]) for key in keys)
    buyer.says(
        "спасибо, всё работает!",
        narration="ответил реплаем на сообщение оператора",
        reply_to=last_to_buyer,
    )

    # 9. Постороннее сообщение оператору НЕ уходит.
    buyer.says("а есть ещё что-нибудь?")

    order = store.orders[order_id]
    print("\n" + "=" * 72)
    print("ИТОГ")
    print(f"  Заявка #{order_id}: ник {order['nick']}, статус {order['status']}")
    print(f"  Подпись к фото: {order.get('product_text') or '—'}")
    print("=" * 72)
    print("\nЗаявка, фото с подписью и переписка идут через бота;")
    print("посторонние сообщения покупателя оператору не отправляются.")
    print("\nДальше: впиши токен от @BotFather в .env и запусти  python bot.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
