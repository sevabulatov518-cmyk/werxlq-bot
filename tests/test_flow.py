"""Офлайн-тесты: логика бота гоняется без сети и без токена.

Запуск:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import itertools
import json
import os
import shutil
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import texts  # noqa: E402
from api import TelegramAPI, TelegramError  # noqa: E402
from bot import build_config, load_env_file  # noqa: E402
from logic import BotLogic, Config, looks_like_card  # noqa: E402
from store import Store  # noqa: E402

SEND_METHODS = {
    "sendMessage",
    "sendPhoto",
    "sendDocument",
    "sendVideo",
    "forwardMessage",
    "copyMessage",
}

BUYER = 111
ADMIN = 999
PHOTO = [{"file_id": "small", "width": 90, "height": 160},
         {"file_id": "big", "width": 1280, "height": 2280}]
PRODUCT_PHOTO = [{"file_id": "prod-photo", "width": 1280, "height": 720}]

_TMP = itertools.count()


def make_temp_dir() -> Path:
    """Временный каталог внутри проекта.

    tempfile.mkdtemp создаёт каталог с правами 0o700 — в этом окружении такие
    каталоги становятся недоступны для записи, поэтому создаём обычным mkdir.
    """
    path = ROOT / f".tmp-check-{os.getpid()}-{next(_TMP)}"
    path.mkdir()
    return path


class FakeAPI:
    """Записывает вызовы и отдаёт правдоподобные ответы вместо Telegram."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._message_id = 0
        self.fail: dict[str, Exception] = {}

    def call(self, method: str, **params):
        record = {"method": method, "params": params, "result": None}
        self.calls.append(record)
        if method in self.fail:
            raise self.fail[method]
        if method in SEND_METHODS:
            self._message_id += 1
            record["result"] = {"message_id": self._message_id, "chat": {"id": params.get("chat_id")}}
            return record["result"]
        record["result"] = True
        return True

    def of(self, method: str) -> list[dict]:
        return [call for call in self.calls if call["method"] == method]

    def messages_to(self, chat_id: int) -> list[dict]:
        return [c["params"] for c in self.of("sendMessage") if c["params"].get("chat_id") == chat_id]

    def texts_to(self, chat_id: int) -> list[str]:
        return [params.get("text", "") for params in self.messages_to(chat_id)]

    def last_text_to(self, chat_id: int) -> str:
        texts = self.texts_to(chat_id)
        return texts[-1] if texts else ""

    def media_to(self, chat_id: int) -> list[dict]:
        return [
            c["params"]
            for c in self.calls
            if c["method"] in ("sendPhoto", "sendDocument", "sendVideo")
            and c["params"].get("chat_id") == chat_id
        ]


class BotTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = make_temp_dir()
        self.api = FakeAPI()
        self.store = Store(self.dir / "state.json")
        self.logic = BotLogic(self.api, self.store, Config(), strict=True)
        self._ids = itertools.count(1)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------------------------------------------------------- helpers
    @staticmethod
    def sender(user_id: int = BUYER, username: str = "ivan_petrov", first_name: str = "Иван") -> dict:
        return {"id": user_id, "is_bot": False, "first_name": first_name, "username": username}

    def send(self, text: str | None = None, user_id: int = BUYER, username: str = "ivan_petrov",
             first_name: str = "Иван", **extra) -> None:
        message = {
            "message_id": next(self._ids),
            "from": self.sender(user_id, username, first_name),
            "chat": {"id": user_id, "type": "private"},
        }
        if text is not None:
            message["text"] = text
        message.update(extra)
        self.logic.handle_update({"update_id": next(self._ids), "message": message})

    def press(self, data: str, user_id: int = BUYER, username: str = "ivan_petrov") -> None:
        update = {
            "update_id": next(self._ids),
            "callback_query": {
                "id": f"cb{next(self._ids)}",
                "from": self.sender(user_id, username),
                "data": data,
                "message": {"message_id": 1, "chat": {"id": user_id, "type": "private"}},
            },
        }
        self.logic.handle_update(update)

    def connect_admin(self) -> None:
        self.send("/start", user_id=ADMIN, username="werxlq", first_name="Арина")

    def make_order(self, nick: str = "werxlq_2026", proof_text: str | None = None) -> dict:
        self.connect_admin()
        self.press("pay")
        self.send(nick)
        if proof_text:
            self.send(proof_text)
        else:
            self.send(photo=PHOTO)
        self.press("confirm")
        return next(iter(self.store.orders.values()))

    def deliver_product(self, order: dict) -> None:
        self.press(f"acc:{order['id']}", user_id=ADMIN, username="werxlq")
        self.send(photo=PRODUCT_PHOTO, user_id=ADMIN, username="werxlq")


class BuyerFlowTests(BotTestCase):
    def test_start_shows_intro_without_payment_details(self) -> None:
        self.send("/start")

        text = self.api.last_text_to(BUYER)
        self.assertIn("Это бот для сигн от Вельвет.", text)
        self.assertIn("можно купить сигну", text)
        self.assertNotIn("0000 0000 0000 0000", text, "оплата не должна показываться сразу")

        keyboard = self.api.messages_to(BUYER)[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(keyboard[0][0]["callback_data"], "pay")
        self.assertEqual(keyboard[1][0]["callback_data"], "info")

    def test_info_shows_created_developer_and_for_whom(self) -> None:
        self.logic.cfg = Config(
            bot_username="Ve1vetz_bot",
            bot_created="21.10.2026",
            bot_developer="Luotx",
            bot_for="Ve1vetzy",
        )
        self.send("/start")
        self.press("info")

        text = self.api.last_text_to(BUYER)
        self.assertIn("О боте", text)
        self.assertIn("21.10.2026", text)
        self.assertIn("@Luotx", text)
        self.assertIn("Ve1vetzy", text)

    def test_payment_details_appear_only_after_nick(self) -> None:
        self.press("pay")
        self.assertIn("Шаг 1 из 3", self.api.last_text_to(BUYER))

        self.send("werxlq_2026")

        text = self.api.last_text_to(BUYER)
        self.assertIn("Шаг 2 из 3", text)
        self.assertIn("0000 0000 0000 0000", text)
        self.assertIn("Имя Фамилия", text)
        self.assertEqual(self.store.sessions[str(BUYER)]["step"], "proof")

    def test_full_order_and_delivery(self) -> None:
        order = self.make_order()
        order_id = order["id"]

        self.assertEqual(order["nick"], "werxlq_2026")
        self.assertEqual(order["status"], "pending")

        card = [t for t in self.api.texts_to(ADMIN) if "Заявка #" in t][-1]
        self.assertIn("werxlq_2026", card)
        self.assertIn("ivan_petrov", card)

        # подтверждение: бот просит фото с материалом
        self.press(f"acc:{order_id}", user_id=ADMIN, username="werxlq")
        self.assertEqual(order["status"], "awaiting_product")
        self.assertIn("Пришли фото с материалом", self.api.last_text_to(ADMIN))

        # оператор присылает фото — оно уходит покупателю
        self.send(photo=PRODUCT_PHOTO, user_id=ADMIN, username="werxlq")

        self.assertEqual(order["status"], "accepted")
        self.assertEqual(order["product"]["file_id"], "prod-photo")

        buyer_media = self.api.media_to(BUYER)
        self.assertEqual(buyer_media[-1]["photo"], "prod-photo")
        self.assertIn("Оплата подтверждена", buyer_media[-1]["caption"])
        self.assertIn("Фото отправлено покупателю", self.api.last_text_to(ADMIN))

        # карточка закрыта, остаётся кнопка переписки
        edits = self.api.of("editMessageText")
        buttons = edits[-1]["params"]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["callback_data"], f"chat:{order_id}")
        self.assertIn("Выполнено", edits[-1]["params"]["text"])

    def test_text_proof_is_accepted(self) -> None:
        order = self.make_order(proof_text="перевод 500р в 14:20, номер 123456")
        self.assertEqual(order["proof"]["kind"], "text")
        self.assertIn("перевод", order["proof"]["text"])
        self.assertEqual(order["status"], "pending")

        card = [t for t in self.api.texts_to(ADMIN) if "Заявка #" in t][-1]
        self.assertIn("перевод", card)

    def test_rejection_notifies_buyer(self) -> None:
        order = self.make_order()
        self.press(f"rej:{order['id']}", user_id=ADMIN, username="werxlq")

        self.assertEqual(order["status"], "rejected")
        self.assertIn("отклонена", self.api.last_text_to(BUYER))
        edits = self.api.of("editMessageText")
        buttons = edits[-1]["params"]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["callback_data"], f"chat:{order['id']}")

    def test_stranger_cannot_approve(self) -> None:
        order = self.make_order()
        self.press(f"acc:{order['id']}", user_id=222, username="hacker")

        self.assertEqual(order["status"], "pending")
        self.assertIn("только оператору", self.api.last_text_to(222))

    def test_card_number_is_rejected_in_nick(self) -> None:
        self.press("pay")
        self.send("4111 1111 1111 1111")

        self.assertIn("номер банковской карты", self.api.last_text_to(BUYER))
        self.assertEqual(self.store.sessions[str(BUYER)]["step"], "nick")
        self.assertEqual(self.store.orders, {})

    def test_empty_nick_is_rejected(self) -> None:
        self.press("pay")
        self.send("   ")
        self.assertIn("не может быть пустым", self.api.last_text_to(BUYER))
        self.assertEqual(self.store.sessions[str(BUYER)]["step"], "nick")

    def test_cancel_resets_flow(self) -> None:
        self.press("pay")
        self.press("cancel")
        self.assertNotIn(str(BUYER), self.store.sessions)
        self.assertIn("отменена", self.api.last_text_to(BUYER))

    def test_status_command(self) -> None:
        order = self.make_order()
        self.send("/status")
        text = self.api.last_text_to(BUYER)
        self.assertIn(f"#{order['id']}", text)
        self.assertIn("ожидает проверки", text)

    def test_status_after_delivery(self) -> None:
        order = self.make_order()
        self.deliver_product(order)
        self.send("/status")
        self.assertIn("выполнена", self.api.last_text_to(BUYER))

    def test_proof_required_for_sticker(self) -> None:
        self.press("pay")
        self.send("werxlq_2026")
        self.send(sticker={"file_id": "sticker"})
        self.assertIn("Пришли подтверждение оплаты", self.api.last_text_to(BUYER))
        self.assertEqual(self.store.sessions[str(BUYER)]["step"], "proof")


class OutsideFlowTests(BotTestCase):
    """Посторонние сообщения покупателей оператору НЕ пересылаются."""

    def test_plain_text_is_not_forwarded(self) -> None:
        self.connect_admin()

        self.send("привет, а сколько стоит?")

        self.assertEqual(self.api.of("forwardMessage"), [])
        self.assertEqual(self.api.of("copyMessage"), [])
        self.assertFalse([t for t in self.api.texts_to(ADMIN) if "Сообщение от" in t])
        self.assertIn("вне заявки оператору не уходят", self.api.last_text_to(BUYER))

    def test_plain_photo_is_not_forwarded(self) -> None:
        self.connect_admin()
        self.send(photo=PHOTO, caption="вот чек")

        self.assertEqual(self.api.of("forwardMessage"), [])
        self.assertEqual(self.api.media_to(ADMIN), [])
        self.assertEqual(self.store.orders, {})
        self.assertIn("вне заявки оператору не уходят", self.api.last_text_to(BUYER))

    def test_message_after_cancelled_order_is_not_forwarded(self) -> None:
        order = self.make_order()
        self.press("pay")
        self.press("cancel")
        self.send("а можно скидку?")

        self.assertEqual(self.api.of("forwardMessage"), [])
        self.assertEqual(order["status"], "pending")


class AdminTests(BotTestCase):
    def test_admin_registered_by_username_case_insensitive(self) -> None:
        self.send("/start", user_id=ADMIN, username="WerxLq")
        self.assertIn(ADMIN, self.store.data["admins"])
        self.assertIn("Панель оператора", self.api.last_text_to(ADMIN))

    def test_similar_username_is_not_admin(self) -> None:
        self.send("/start", user_id=222, username="werxlq_fake")
        self.assertNotIn(222, self.store.data["admins"])
        self.assertIn("Это бот для сигн", self.api.last_text_to(222))

    def test_admin_ids_from_env_work_without_start(self) -> None:
        logic = BotLogic(FakeAPI(), Store(self.dir / "b.json"), Config(admin_ids=[777]), strict=True)
        self.assertTrue(logic._is_admin({"id": 777, "username": None}))

    def test_admin_text_while_waiting_photo(self) -> None:
        order = self.make_order()
        self.press(f"acc:{order['id']}", user_id=ADMIN, username="werxlq")
        media_before = len(self.api.media_to(BUYER))

        self.send("сейчас отправлю", user_id=ADMIN, username="werxlq")

        self.assertEqual(order["status"], "awaiting_product")
        self.assertIn("Нужно именно фото с материалом", self.api.last_text_to(ADMIN))
        self.assertEqual(len(self.api.media_to(BUYER)), media_before)

    def test_unacc_returns_order_to_pending(self) -> None:
        order = self.make_order()
        self.press(f"acc:{order['id']}", user_id=ADMIN, username="werxlq")
        self.assertEqual(order["status"], "awaiting_product")

        self.press(f"unacc:{order['id']}", user_id=ADMIN, username="werxlq")

        self.assertEqual(order["status"], "pending")
        self.assertIn("снова в ожидании", self.api.last_text_to(ADMIN))
        self.assertNotEqual(self.store.sessions.get(str(ADMIN), {}).get("step"), "product")

    def test_slash_cancel_returns_order_to_pending(self) -> None:
        order = self.make_order()
        self.press(f"acc:{order['id']}", user_id=ADMIN, username="werxlq")
        self.send("/cancel", user_id=ADMIN, username="werxlq")

        self.assertEqual(order["status"], "pending")
        self.assertIn("снова в ожидании", self.api.last_text_to(ADMIN))

    def test_second_admin_can_finish_order(self) -> None:
        order = self.make_order()
        self.logic.cfg = Config(admin_username="werxlq", admin_usernames=["Luotx"])
        self.send("/start", user_id=777, username="luotx", first_name="Второй")

        self.press(f"acc:{order['id']}", user_id=777, username="luotx")
        self.send(photo=PRODUCT_PHOTO, user_id=777, username="luotx")

        self.assertEqual(order["status"], "accepted")
        self.assertEqual(self.api.media_to(BUYER)[-1]["photo"], "prod-photo")

    def test_orders_and_stats_commands(self) -> None:
        order = self.make_order()
        self.send("/orders", user_id=ADMIN, username="werxlq")
        self.assertIn(f"#{order['id']}", self.api.last_text_to(ADMIN))

        self.send("/stats", user_id=ADMIN, username="werxlq")
        self.assertIn("Всего заявок", self.api.last_text_to(ADMIN))

    def test_admin_hint_for_unrelated_message(self) -> None:
        self.connect_admin()
        self.send("просто болтаю", user_id=ADMIN, username="werxlq")
        self.assertIn("Написать по заявке", self.api.last_text_to(ADMIN))


class DeliveryFailureTests(BotTestCase):
    def test_buyer_blocked_bot(self) -> None:
        order = self.make_order()
        self.press(f"acc:{order['id']}", user_id=ADMIN, username="werxlq")
        self.api.fail["sendPhoto"] = TelegramError("sendPhoto", "Forbidden: bot was blocked")

        self.send(photo=PRODUCT_PHOTO, user_id=ADMIN, username="werxlq")

        self.assertEqual(order["status"], "accepted")
        self.assertIn("не удалось доставить фото", self.api.last_text_to(ADMIN))

    def test_order_saved_when_no_admins(self) -> None:
        self.press("pay")
        self.send("werxlq_2026")
        self.send(photo=PHOTO)
        self.press("confirm")

        self.assertEqual(len(self.store.orders), 1)
        buyer_texts = self.api.texts_to(BUYER)
        self.assertTrue(any("отправлена" in text for text in buyer_texts))
        self.assertIn("Оператор ещё не подключён", buyer_texts[-1])
        self.assertEqual(self.api.texts_to(ADMIN), [])

    def test_rate_limit(self) -> None:
        logic = BotLogic(self.api, Store(self.dir / "rl.json"),
                         Config(rate_limit_count=2, rate_limit_window=60), strict=True)
        self.logic = logic
        for _ in range(3):
            self.send("спам")
        self.assertIn("Слишком много сообщений", self.api.last_text_to(BUYER))


class ConversationTests(BotTestCase):
    """Фото с подписью и переписка по заявке."""

    def test_product_caption_goes_to_buyer(self) -> None:
        order = self.make_order()
        self.press(f"acc:{order['id']}", user_id=ADMIN, username="werxlq")
        self.send(
            photo=PRODUCT_PHOTO,
            caption="Держи сигну, ник активирован",
            user_id=ADMIN,
            username="werxlq",
        )

        media = self.api.media_to(BUYER)[-1]
        self.assertEqual(media["photo"], "prod-photo")
        self.assertEqual(media["caption"], "Держи сигну, ник активирован")
        self.assertEqual(order["product_text"], "Держи сигну, ник активирован")
        self.assertIn("Оплата подтверждена", self.api.last_text_to(BUYER))
        self.assertEqual(order["status"], "accepted")

    def test_admin_chat_message_reaches_buyer(self) -> None:
        order = self.make_order()
        self.press(f"chat:{order['id']}", user_id=ADMIN, username="werxlq")
        self.assertIn("Переписка по заявке", self.api.last_text_to(ADMIN))

        self.send("Если что, я на связи", user_id=ADMIN, username="werxlq")

        copies = [c for c in self.api.of("copyMessage") if c["params"]["chat_id"] == BUYER]
        self.assertEqual(copies[-1]["params"]["from_chat_id"], ADMIN)
        self.assertIn("Отправлено покупателю", self.api.last_text_to(ADMIN))

    def test_admin_can_send_several_messages_in_a_row(self) -> None:
        order = self.make_order()
        self.press(f"chat:{order['id']}", user_id=ADMIN, username="werxlq")

        self.send("первое сообщение", user_id=ADMIN, username="werxlq")
        self.send("второе сообщение", user_id=ADMIN, username="werxlq")
        self.send(photo=PRODUCT_PHOTO, user_id=ADMIN, username="werxlq")

        copies = [c for c in self.api.of("copyMessage") if c["params"]["chat_id"] == BUYER]
        self.assertEqual(len(copies), 3)

    def test_buyer_reply_reaches_admins(self) -> None:
        order = self.make_order()
        self.press(f"chat:{order['id']}", user_id=ADMIN, username="werxlq")
        self.send("я на связи", user_id=ADMIN, username="werxlq")

        buyer_keys = [
            int(key.split(":")[1]) for key in self.store.data["msgmap"] if key.startswith(f"{BUYER}:")
        ]
        self.assertTrue(buyer_keys, "сообщение покупателю должно попасть в msgmap")

        self.send(
            "спасибо, всё работает",
            reply_to_message={"message_id": max(buyer_keys), "from": {"id": 0}},
        )

        header = [text for text in self.api.texts_to(ADMIN) if "Ответ покупателя" in text]
        self.assertTrue(header)
        self.assertIn(f"#{order['id']}", header[-1])
        self.assertIn("Передано оператору", self.api.last_text_to(BUYER))

    def test_chat_closed_with_cancel(self) -> None:
        order = self.make_order()
        self.press(f"chat:{order['id']}", user_id=ADMIN, username="werxlq")
        self.send("/cancel", user_id=ADMIN, username="werxlq")
        self.assertIn("Переписка завершена", self.api.last_text_to(ADMIN))
        self.assertNotEqual(self.store.sessions.get(str(ADMIN), {}).get("step"), "chat")

    def test_finish_button_closes_chat_not_order(self) -> None:
        order = self.make_order()
        self.press(f"chat:{order['id']}", user_id=ADMIN, username="werxlq")

        self.press("cancel", user_id=ADMIN, username="werxlq")

        self.assertIn("Переписка завершена", self.api.last_text_to(ADMIN))
        self.assertNotEqual(self.store.sessions.get(str(ADMIN), {}).get("step"), "chat")
        self.assertEqual(order["status"], "pending")


class MultiAdminTests(BotTestCase):
    def test_second_operator_username_has_admin_rights(self) -> None:
        logic = BotLogic(
            FakeAPI(),
            Store(self.dir / "multi.json"),
            Config(admin_username="werxlq", admin_usernames=["Luotx"]),
            strict=True,
        )
        self.assertTrue(logic._is_admin({"id": 1, "username": "LuotX"}))
        self.assertTrue(logic._is_admin({"id": 2, "username": "werxlq"}))
        self.assertFalse(logic._is_admin({"id": 3, "username": "random_user"}))

    def test_second_operator_receives_orders(self) -> None:
        self.logic.cfg = Config(admin_username="werxlq", admin_usernames=["Luotx"])
        self.send("/start", user_id=777, username="luotx", first_name="Оператор")
        self.assertIn(777, self.store.data["admins"])

        self.press("pay")
        self.send("werxlq_2026")
        self.send(photo=PHOTO)
        self.press("confirm")

        self.assertTrue(any("Заявка #" in text for text in self.api.texts_to(777)))

    def test_build_config_parses_operator_list(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"ADMIN_USERNAME": "werxlq, @Luotx ;Luotxx", "BOT_USERNAME": "@Ve1vetz_bot"},
            clear=False,
        ):
            cfg = build_config()

        self.assertEqual(cfg.admin_username, "werxlq")
        self.assertEqual(cfg.admin_usernames, ["Luotx", "Luotxx"])
        self.assertEqual(cfg.bot_username, "Ve1vetz_bot")
        self.assertEqual(cfg.all_admin_usernames, ["werxlq", "luotx", "luotxx"])

    def test_admin_panel_shows_bot_link(self) -> None:
        text = texts.admin_welcome(Config(bot_username="Ve1vetz_bot"), "Аня")
        self.assertIn("t.me/Ve1vetz_bot", text)


class ProxyTests(unittest.TestCase):
    def test_no_proxy_by_default(self) -> None:
        api = TelegramAPI("123456:AAEtest")
        self.assertIsNone(api._opener)
        self.assertIsNone(api.proxy)

    def test_proxy_builds_opener(self) -> None:
        api = TelegramAPI("123456:AAEtest", proxy="http://127.0.0.1:8080")
        self.assertIsNotNone(api._opener)
        self.assertEqual(api.proxy, "http://127.0.0.1:8080")


class StoreTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        directory = make_temp_dir()
        try:
            path = directory / "state.json"
            store = Store(path)
            store.data["admins"].append(5)
            first = store.next_order_id()
            store.orders[first] = {"id": first, "status": "awaiting_product"}
            store.save()

            reloaded = Store(path)
            self.assertEqual(reloaded.data["admins"], [5])
            self.assertIn(first, reloaded.orders)
            self.assertEqual(int(reloaded.data["counter"]), int(first) + 1)
            self.assertEqual(reloaded.stats()["awaiting_product"], 1)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_corrupt_file_is_tolerated(self) -> None:
        directory = make_temp_dir()
        try:
            path = directory / "state.json"
            path.write_text("{не json", encoding="utf-8")
            store = Store(path)
            self.assertEqual(store.data["admins"], [])
            self.assertTrue(path.with_suffix(".json.corrupt").exists())
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class EnvTests(unittest.TestCase):
    def test_env_file_parsing(self) -> None:
        directory = make_temp_dir()
        try:
            env_path = directory / ".env"
            env_path.write_text(
                "# комментарий\n"
                "WERXLQ_TEST_TOKEN=123456:AAEtest\n"
                "export WERXLQ_TEST_EXPORTED = \"значение в кавычках\"\n"
                "мусор без равно\n",
                encoding="utf-8",
            )
            os.environ.pop("WERXLQ_TEST_TOKEN", None)
            os.environ.pop("WERXLQ_TEST_EXPORTED", None)
            load_env_file(env_path)
            self.assertEqual(os.environ.get("WERXLQ_TEST_TOKEN"), "123456:AAEtest")
            self.assertEqual(os.environ.get("WERXLQ_TEST_EXPORTED"), "значение в кавычках")
            os.environ.pop("WERXLQ_TEST_TOKEN", None)
            os.environ.pop("WERXLQ_TEST_EXPORTED", None)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_env_file_saved_in_cp1251(self) -> None:
        """Блокнот на Windows может сохранить .env в cp1251 — значения всё равно должны читаться."""
        directory = make_temp_dir()
        try:
            env_path = directory / ".env"
            env_path.write_bytes(
                "WERXLQ_TEST_CP=123456:AAEtest\r\nWERXLQ_TEST_HOLDER=Имя Фамилия\r\n".encode("cp1251")
            )
            for key in ("WERXLQ_TEST_CP", "WERXLQ_TEST_HOLDER"):
                os.environ.pop(key, None)
            load_env_file(env_path)
            self.assertEqual(os.environ.get("WERXLQ_TEST_CP"), "123456:AAEtest")
            self.assertEqual(os.environ.get("WERXLQ_TEST_HOLDER"), "Имя Фамилия")
        finally:
            for key in ("WERXLQ_TEST_CP", "WERXLQ_TEST_HOLDER"):
                os.environ.pop(key, None)
            shutil.rmtree(directory, ignore_errors=True)

    def test_env_file_with_bom(self) -> None:
        directory = make_temp_dir()
        try:
            env_path = directory / ".env"
            env_path.write_bytes(b"\xef\xbb\xbfWERXLQ_TEST_BOM=ok\r\n")
            os.environ.pop("WERXLQ_TEST_BOM", None)
            load_env_file(env_path)
            self.assertEqual(os.environ.get("WERXLQ_TEST_BOM"), "ok")
        finally:
            os.environ.pop("WERXLQ_TEST_BOM", None)
            shutil.rmtree(directory, ignore_errors=True)


class CardDetectionTests(unittest.TestCase):
    def test_detects_luhn_valid_card(self) -> None:
        self.assertTrue(looks_like_card("4111 1111 1111 1111"))
        self.assertTrue(looks_like_card("оплатил с 4111-1111-1111-1111"))

    def test_ignores_seller_card(self) -> None:
        self.assertFalse(looks_like_card("0000 0000 0000 0000", "2202208458711745"))

    def test_ignores_plain_text(self) -> None:
        self.assertFalse(looks_like_card("werxlq_2026"))
        self.assertFalse(looks_like_card("чек 12345"))


class ApiResilienceTests(unittest.TestCase):
    def test_retries_after_429(self) -> None:
        api = TelegramAPI("123456789:AAEtestTOKENtestTOKENtestTOKEN", max_retries=2)
        error = urllib.error.HTTPError(
            url="https://api.telegram.org",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false,"error_code":429,"description":"Too Many Requests",'
                          b'"parameters":{"retry_after":1}}'),
        )
        response = mock.MagicMock()
        response.read.return_value = json.dumps({"ok": True, "result": {"message_id": 7}}).encode()
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", side_effect=[error, response]), \
                mock.patch("api.time.sleep") as sleeper:
            result = api.call("sendMessage", chat_id=1, text="hi")

        self.assertEqual(result, {"message_id": 7})
        sleeper.assert_called_once()

    def test_401_raises_without_retry(self) -> None:
        api = TelegramAPI("123456789:AAEtestTOKENtestTOKENtestTOKEN", max_retries=3)
        error = urllib.error.HTTPError(
            url="https://api.telegram.org",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false,"error_code":401,"description":"Unauthorized"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error) as opener, \
                mock.patch("api.time.sleep"):
            with self.assertRaises(TelegramError) as ctx:
                api.call("getMe")

        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(opener.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
