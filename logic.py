"""Вся логика бота: заявки, выдача материала, переписка с покупателем.

Класс BotLogic ничего не знает про сеть — он получает объект API с методом call(),
поэтому его можно гонять офлайн в тестах и в демо-режиме.

Сценарий покупателя:
    /start → «Это бот для сигн от Вельвет»
    «Купить сигну» → ник → реквизиты и оплата → подтверждение (фото/текст) → заявка

Оператор:
    карточка заявки → «Подтвердить» → фото с подписью уходит покупателю
    «✍️ Написать по заявке» → переписка; ответы покупателя реплаем возвращаются оператору

Посторонние сообщения покупателей оператору не пересылаются.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field

import texts as T
from api import TelegramAPI, TelegramError
from store import Store

log = logging.getLogger(__name__)

CMD_RE = re.compile(r"^/([A-Za-z0-9_]+)(?:@\w+)?(?:\s+(.*))?$", re.S)
CARD_RE = re.compile(r"\d(?:[ \-]?\d){12,18}")

BUYER_PROOF_KINDS = {"photo", "document", "video", "text"}
PRODUCT_KINDS = {"photo", "document"}


@dataclass
class Config:
    admin_username: str = "werxlq"
    admin_usernames: list[str] = field(default_factory=list)
    bot_username: str = ""
    bot_created: str = ""
    bot_developer: str = ""
    bot_for: str = ""
    intro: str = "Это бот для сигн от Вельвет."
    admin_ids: list[int] = field(default_factory=list)
    bank: str = "Сбер"
    card_number: str = "0000 0000 0000 0000"
    card_holder: str = "Имя Фамилия"
    price: str = ""
    product: str = "сигну"
    support_contact: str = ""
    rate_limit_count: int = 20
    rate_limit_window: int = 60

    @property
    def all_admin_usernames(self) -> list[str]:
        """Все операторы: основной + дополнительные (без @, в нижнем регистре)."""
        result: list[str] = []
        for name in [self.admin_username, *self.admin_usernames]:
            clean = (name or "").strip().lstrip("@").lower()
            if clean and clean not in result:
                result.append(clean)
        return result


def luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def looks_like_card(text: str, seller_digits: str = "") -> bool:
    """Ищем в тексте номер чужой банковской карты (по алгоритму Луна)."""
    for match in CARD_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", match.group(0))
        if not 13 <= len(digits) <= 19:
            continue
        if seller_digits and digits == seller_digits:
            continue
        if luhn(digits):
            return True
    return False


class BotLogic:
    def __init__(self, api, store: Store, cfg: Config, strict: bool = False) -> None:
        self.api = api
        self.store = store
        self.cfg = cfg
        self.strict = strict
        self._hits: dict[int, list[float]] = {}
        self._seller_digits = re.sub(r"\D", "", cfg.card_number or "")

    # ----------------------------------------------------------- утилиты
    @property
    def data(self) -> dict:
        return self.store.data

    def _now(self) -> float:
        return time.time()

    def _fmt_time(self, timestamp: float) -> str:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))

    def _save(self) -> None:
        self.store.save()

    def _send(self, chat_id: int, text: str, keyboard=None):
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        return self.api.call("sendMessage", **params)

    def _try_send(self, chat_id: int, text: str, keyboard=None):
        try:
            return self._send(chat_id, text, keyboard)
        except TelegramError as exc:
            if self.strict:
                raise
            log.warning("Не удалось отправить сообщение в %s: %s", chat_id, exc)
            return None

    def _session(self, chat_id: int) -> dict:
        return self.store.sessions.setdefault(str(chat_id), {})

    def _set_session(self, chat_id: int, **fields) -> dict:
        session = self.store.sessions.setdefault(str(chat_id), {})
        session.update(fields)
        return session

    def _clear_session(self, chat_id: int) -> None:
        self.store.sessions.pop(str(chat_id), None)

    def _remember_buyer(self, buyer_chat: int, result, order_id: str) -> None:
        """Запоминаем сообщение, отправленное покупателю, чтобы работал reply."""
        if isinstance(result, dict) and result.get("message_id"):
            self.data["msgmap"][f"{buyer_chat}:{result['message_id']}"] = {
                "order_id": order_id,
            }

    def _rate_limited(self, user_id: int) -> bool:
        limit, window = self.cfg.rate_limit_count, self.cfg.rate_limit_window
        if not limit or limit <= 0:
            return False
        now = self._now()
        hits = [stamp for stamp in self._hits.get(user_id, []) if now - stamp < window]
        hits.append(now)
        self._hits[user_id] = hits
        return len(hits) > limit

    # ------------------------------------------------------------- админы
    def _is_admin(self, user: dict) -> bool:
        user_id = user.get("id")
        if user_id in self.cfg.admin_ids:
            return True
        if user_id in self.data["admins"]:
            return True
        username = (user.get("username") or "").lower()
        return bool(username) and username in self.cfg.all_admin_usernames

    def _register_admin(self, user: dict) -> bool:
        user_id = user.get("id")
        if not user_id:
            return False
        if user_id not in self.data["admins"]:
            self.data["admins"].append(user_id)
            self._save()
            log.info("Оператор подключён: @%s (id %s)", user.get("username"), user_id)
            return True
        return False

    def _admin_chat_ids(self) -> list[int]:
        result: list[int] = []
        for chat_id in list(self.cfg.admin_ids) + list(self.data["admins"]):
            if isinstance(chat_id, int) and chat_id not in result:
                result.append(chat_id)
        return result

    # ------------------------------------------------------------- dispatch
    def handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                self._on_callback(update["callback_query"])
            elif "message" in update:
                self._on_message(update["message"])
        except TelegramError as exc:
            if self.strict:
                raise
            log.warning("Ошибка Telegram API при обработке обновления: %s", exc)
        except Exception:  # noqa: BLE001 — бот не должен падать от одного апдейта
            if self.strict:
                raise
            log.exception("Необработанная ошибка в обновлении %s", update.get("update_id"))

    def _on_message(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        if chat.get("type") != "private" or not user or user.get("is_bot"):
            return

        chat_id = chat.get("id")
        text = (msg.get("text") or msg.get("caption") or "").strip()

        if self._is_admin(user):
            self._register_admin(user)
            self._on_admin_message(msg, text)
            return

        command = CMD_RE.match(text)
        if command:
            self._on_buyer_command(chat_id, user, command.group(1).lower())
            return

        if self._rate_limited(user.get("id") or chat_id):
            self._send(chat_id, T.rate_limited())
            return

        self._on_buyer_message(msg, text)

    # ------------------------------------------------------------- покупатель
    def _on_buyer_command(self, chat_id: int, user: dict, command: str) -> None:
        if command in ("start", "help"):
            self._clear_session(chat_id)
            self._send(chat_id, T.welcome(self.cfg, user.get("first_name")), T.welcome_keyboard())
            return
        if command == "info":
            self._send(chat_id, T.info_text(self.cfg), T.back_keyboard())
            return
        if command == "pay":
            self._start_order(chat_id, user)
            return
        if command == "cancel":
            self._clear_session(chat_id)
            self._send(chat_id, T.cancelled())
            return
        if command == "status":
            self._send_status(chat_id, user)
            return
        self._send(chat_id, T.welcome(self.cfg, user.get("first_name")), T.welcome_keyboard())

    def _on_buyer_message(self, msg: dict, text: str) -> None:
        chat_id = msg["chat"]["id"]
        session = self._session(chat_id)
        step = session.get("step")

        if step == "nick":
            self._take_nick(chat_id, text)
            return
        if step == "proof":
            self._take_proof(chat_id, msg)
            return
        if step == "confirm":
            self._send(chat_id, T.confirm_reminder(), T.preview_keyboard())
            return

        # Ответ реплаем на сообщение оператора по заявке — передаём оператору.
        reply = msg.get("reply_to_message") or {}
        if reply.get("message_id"):
            record = self.data["msgmap"].get(f"{chat_id}:{reply['message_id']}")
            if isinstance(record, dict):
                self._relay_buyer_reply(msg, record.get("order_id", ""))
                return

        # Всё остальное оператору не уходит — только подсказка.
        self._send(chat_id, T.outside_flow_hint(self.cfg), T.back_keyboard())

    def _start_order(self, chat_id: int, user: dict) -> None:
        self._set_session(chat_id, step="nick", started=self._now(), user_id=user.get("id"))
        self._send(chat_id, T.ask_nick(self.cfg), [[T.btn("✖️ Отмена", "cancel")]])
        self._save()

    def _guard_card(self, chat_id: int, text: str) -> bool:
        if looks_like_card(text, self._seller_digits):
            self._send(chat_id, T.card_warning(self.cfg))
            return True
        return False

    def _take_nick(self, chat_id: int, text: str) -> None:
        nick = " ".join((text or "").split())
        if not nick:
            self._send(chat_id, T.nick_empty())
            return
        if len(nick) > 64:
            self._send(chat_id, T.nick_too_long())
            return
        if self._guard_card(chat_id, nick):
            return
        # Шаг 2: показываем реквизиты и ждём подтверждение оплаты.
        self._set_session(chat_id, step="proof", nick=nick)
        self._send(chat_id, T.payment_step(self.cfg), [[T.btn("✖️ Отмена", "cancel")]])
        self._save()

    def _extract_proof(self, msg: dict) -> dict | None:
        caption = (msg.get("caption") or "").strip()
        photos = msg.get("photo") or []
        if photos:
            return {"kind": "photo", "file_id": photos[-1].get("file_id"), "caption": caption}
        document = msg.get("document")
        if document:
            return {
                "kind": "document",
                "file_id": document.get("file_id"),
                "name": document.get("file_name"),
                "caption": caption,
            }
        video = msg.get("video")
        if video:
            return {"kind": "video", "file_id": video.get("file_id"), "caption": caption}
        text = (msg.get("text") or caption).strip()
        if text and not text.startswith("/"):
            return {"kind": "text", "text": text[:1000]}
        return None

    def _take_proof(self, chat_id: int, msg: dict) -> None:
        proof = self._extract_proof(msg)
        if not proof or proof.get("kind") not in BUYER_PROOF_KINDS:
            self._send(chat_id, T.proof_required())
            return
        if proof.get("kind") == "text" and self._guard_card(chat_id, proof.get("text", "")):
            return
        if proof.get("kind") in ("photo", "document", "video") and not proof.get("file_id"):
            self._send(chat_id, T.proof_required())
            return

        self._set_session(chat_id, step="confirm", proof=proof)
        session = self._session(chat_id)
        self._send(
            chat_id,
            T.preview(self.cfg, session.get("nick", ""), proof),
            T.preview_keyboard(),
        )
        self._save()

    def _send_status(self, chat_id: int, user: dict) -> None:
        orders = [
            order
            for order in self.store.orders.values()
            if isinstance(order, dict) and order.get("user_id") == user.get("id")
        ]
        if not orders:
            self._send(chat_id, T.no_orders_yet())
            return
        orders.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        latest = orders[0]
        self._send(chat_id, T.status_text(self.cfg, latest, self._fmt_time(latest.get("created_at", 0))))

    # --------------------------------------------------------------- заявка
    def _submit(self, chat_id: int, user: dict) -> None:
        session = self._session(chat_id)
        if session.get("step") != "confirm" or not session.get("proof"):
            self._send(chat_id, T.nothing_to_send())
            return

        order_id = self.store.next_order_id()
        order = {
            "id": order_id,
            "user_id": user.get("id"),
            "chat_id": chat_id,
            "username": user.get("username") or "",
            "name": user.get("first_name") or "",
            "nick": session.get("nick", ""),
            "proof": session["proof"],
            "status": "pending",
            "created_at": self._now(),
            "admin_msgs": [],
        }
        order["text"] = T.order_card(self.cfg, order, self._fmt_time(order["created_at"]))
        self.store.orders[order_id] = order

        self._clear_session(chat_id)
        self._set_session(chat_id, step="sent", order_id=order_id)

        delivered = self._notify_admins(order)
        self._send(chat_id, T.order_sent(self.cfg, order_id))
        if not delivered:
            self._send(chat_id, T.no_admins(self.cfg))
        self._save()

    def _deliver_proof(self, chat_id: int, proof: dict, caption: str):
        kind = proof.get("kind")
        try:
            if kind == "photo":
                return self.api.call(
                    "sendPhoto",
                    chat_id=chat_id,
                    photo=proof.get("file_id"),
                    caption=caption,
                    parse_mode="HTML",
                )
            if kind == "document":
                return self.api.call(
                    "sendDocument",
                    chat_id=chat_id,
                    document=proof.get("file_id"),
                    caption=caption,
                    parse_mode="HTML",
                )
            if kind == "video":
                return self.api.call(
                    "sendVideo",
                    chat_id=chat_id,
                    video=proof.get("file_id"),
                    caption=caption,
                    parse_mode="HTML",
                )
        except TelegramError as exc:
            log.warning("Вложение не доставлено (%s): %s", kind, exc)
            return self._send(chat_id, f"{caption}\n\n⚠️ Вложение не удалось переслать.")
        if kind == "text":
            return self._send(
                chat_id,
                f"{caption}\n\n<blockquote>{html.escape(proof.get('text', ''))}</blockquote>",
            )
        return None

    def _notify_admins(self, order: dict) -> bool:
        admins = self._admin_chat_ids()
        if not admins:
            log.error(
                "Нет подключённых операторов: заявка #%s сохранена, но не доставлена",
                order["id"],
            )
            return False

        keyboard = T.order_keyboard(order["id"])
        delivered = False
        for chat_id in admins:
            try:
                result = self._send(chat_id, order["text"], keyboard)
                if isinstance(result, dict) and result.get("message_id"):
                    order["admin_msgs"].append([chat_id, result["message_id"]])

                self._deliver_proof(
                    chat_id, order.get("proof") or {}, T.proof_caption(order["id"])
                )
                delivered = True
            except TelegramError as exc:
                log.warning("Оператор %s недоступен: %s", chat_id, exc)
        self._save()
        return delivered

    # ----------------------------------------------------------- действия
    def _on_callback(self, callback: dict) -> None:
        user = callback.get("from") or {}
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id") or user.get("id")

        try:
            self.api.call("answerCallbackQuery", callback_query_id=callback.get("id"))
        except TelegramError as exc:
            log.debug("answerCallbackQuery: %s", exc)

        if data in ("pay", "restart"):
            self._start_order(chat_id, user)
            return
        if data == "help":
            self._send(chat_id, T.help_text(self.cfg))
            return
        if data == "info":
            self._send(chat_id, T.info_text(self.cfg), T.back_keyboard())
            return
        if data == "cancel":
            if self._is_admin(user) and self._session(chat_id).get("step") in ("product", "chat"):
                self._cancel_admin_step(chat_id)
                return
            self._clear_session(chat_id)
            self._send(chat_id, T.cancelled())
            return
        if data == "confirm":
            self._submit(chat_id, user)
            return
        if data == "orders":
            if not self._is_admin(user):
                self._send(chat_id, T.admins_only())
                return
            self._send_pending(chat_id)
            return

        action, _, order_id = data.partition(":")
        if action in ("acc", "rej", "unacc", "chat") and order_id:
            self._admin_action(action, order_id, user, chat_id)
            return

        log.debug("Неизвестный callback: %s", data)

    def _admin_action(self, action: str, order_id: str, user: dict, chat_id: int) -> None:
        if not self._is_admin(user):
            self._send(chat_id, T.admins_only())
            return

        order = self.store.orders.get(order_id)
        if not isinstance(order, dict):
            self._send(chat_id, T.order_missing(order_id))
            return

        if action == "acc":
            if order.get("status") == "accepted":
                self._send(chat_id, T.already_done(order_id))
                return
            self._set_status(order, "awaiting_product")
            self._set_session(chat_id, step="product", order_id=order_id)
            self._send(
                chat_id,
                T.ask_product_photo(self.cfg, order_id),
                [[T.btn("✖️ Отмена", f"unacc:{order_id}")]],
            )
        elif action == "rej":
            if order.get("status") == "rejected":
                self._send(chat_id, T.already_rejected(order_id))
                return
            self._clear_session(chat_id)
            self._set_status(order, "rejected", final=True)
            self._try_send(order["chat_id"], T.buyer_rejected(self.cfg, order_id))
            self._send(chat_id, f"❌ Заявка #{order_id} отклонена. Покупатель уведомлён.")
        elif action == "unacc":
            if order.get("status") != "awaiting_product":
                self._send(chat_id, "Эту заявку уже нельзя вернуть в ожидание.")
                return
            self._clear_session(chat_id)
            self._set_status(order, "pending")
            self._send(chat_id, T.order_cancelled_accept(order_id))
        else:  # chat — переписка по заявке
            self._set_session(chat_id, step="chat", order_id=order_id)
            self._send(chat_id, T.ask_chat_message(order_id), [[T.btn("✖️ Закончить", "cancel")]])
        self._save()

    def _set_status(self, order: dict, status: str, final: bool = False) -> None:
        order["status"] = status
        order["updated_at"] = self._now()
        suffix = "\n\n" + T.status_line(status, self._fmt_time(order["updated_at"]))
        for entry in order.get("admin_msgs", []):
            try:
                chat_id, message_id = entry
            except (TypeError, ValueError):
                continue
            params = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": order.get("text", "") + suffix,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if final:
                params["reply_markup"] = {"inline_keyboard": T.done_keyboard(order["id"])}
            try:
                self.api.call("editMessageText", **params)
            except TelegramError as exc:
                log.debug("Не удалось обновить карточку заявки #%s: %s", order.get("id"), exc)

    # ------------------------------------------------------------- оператор
    def _on_admin_message(self, msg: dict, text: str) -> None:
        chat_id = msg["chat"]["id"]
        session = self._session(chat_id)

        command = CMD_RE.match(text)
        if command:
            self._on_admin_command(chat_id, msg, command.group(1).lower(), (command.group(2) or "").strip())
            return

        step = session.get("step")
        order_id = session.get("order_id")
        if step == "product" and order_id:
            self._take_product_photo(chat_id, msg, order_id)
            return
        if step == "chat" and order_id:
            self._send_chat_message(chat_id, order_id, msg)
            return

        self._send(chat_id, T.admin_hint())

    def _take_product_photo(self, chat_id: int, msg: dict, order_id: str) -> None:
        order = self.store.orders.get(order_id)
        if not isinstance(order, dict):
            self._clear_session(chat_id)
            self._send(chat_id, T.order_missing(order_id))
            return
        if order.get("status") == "accepted":
            self._clear_session(chat_id)
            self._send(chat_id, T.already_done(order_id))
            return

        proof = self._extract_proof(msg)
        if not proof or proof.get("kind") not in PRODUCT_KINDS or not proof.get("file_id"):
            self._send(chat_id, T.product_photo_required(self.cfg, order_id))
            return

        order["product"] = proof
        order["product_text"] = (proof.get("caption") or "").strip()
        order["delivered_at"] = self._now()
        delivered = self._send_product(order)
        self._set_status(order, "accepted", final=True)
        self._clear_session(chat_id)
        self._send(chat_id, T.product_sent(self.cfg, order_id, delivered))
        self._save()

    def _send_product(self, order: dict) -> bool:
        buyer = order.get("chat_id")
        product = order.get("product") or {}
        text = (order.get("product_text") or "").strip()
        is_document = product.get("kind") == "document"
        method = "sendDocument" if is_document else "sendPhoto"
        key = "document" if is_document else "photo"
        try:
            if text:
                # Подпись оператора уходит как есть, без HTML-разметки.
                result = self.api.call(
                    method,
                    chat_id=buyer,
                    **{key: product.get("file_id")},
                    caption=text[:1024],
                )
                self._remember_buyer(buyer, result, order["id"])
                system = self._send(buyer, T.buyer_delivered(self.cfg, order["id"]))
                self._remember_buyer(buyer, system, order["id"])
            else:
                result = self.api.call(
                    method,
                    chat_id=buyer,
                    **{key: product.get("file_id")},
                    caption=T.buyer_delivered(self.cfg, order["id"]),
                    parse_mode="HTML",
                )
                self._remember_buyer(buyer, result, order["id"])
            return True
        except TelegramError as exc:
            log.warning("Материал покупателю %s не доставлен: %s", buyer, exc)
            self._try_send(
                buyer,
                T.buyer_delivered(self.cfg, order["id"])
                + "\n\n⚠️ Фото не удалось передать — оператор пришлёт его вручную.",
            )
            return False

    def _send_chat_message(self, admin_chat: int, order_id: str, msg: dict) -> None:
        order = self.store.orders.get(order_id)
        if not isinstance(order, dict):
            self._clear_session(admin_chat)
            self._send(admin_chat, T.order_missing(order_id))
            return
        buyer = order.get("chat_id")
        try:
            result = self.api.call(
                "copyMessage",
                chat_id=buyer,
                from_chat_id=admin_chat,
                message_id=msg.get("message_id"),
            )
            self._remember_buyer(buyer, result, order_id)
            self._send(admin_chat, T.chat_sent())
        except TelegramError as exc:
            log.warning("Сообщение по заявке #%s не доставлено: %s", order_id, exc)
            self._send(admin_chat, T.chat_failed())

    def _relay_buyer_reply(self, msg: dict, order_id: str) -> None:
        """Ответ покупателя реплаем на сообщение оператора уходит операторам."""
        buyer_chat = msg["chat"]["id"]
        admins = self._admin_chat_ids()
        if not admins:
            self._send(buyer_chat, T.no_admins(self.cfg))
            return

        delivered = False
        for admin_chat in admins:
            try:
                self._send(admin_chat, T.buyer_reply_header(order_id), T.reply_button(order_id))
                self.api.call(
                    "copyMessage",
                    chat_id=admin_chat,
                    from_chat_id=buyer_chat,
                    message_id=msg.get("message_id"),
                )
                delivered = True
            except TelegramError as exc:
                log.warning("Ответ покупателя не доставлен оператору %s: %s", admin_chat, exc)

        self._send(buyer_chat, T.reply_forwarded() if delivered else T.no_admins(self.cfg))
        self._save()

    def _send_pending(self, chat_id: int) -> None:
        pending = [
            order
            for order in self.store.orders.values()
            if isinstance(order, dict) and order.get("status") in ("pending", "awaiting_product")
        ]
        pending.sort(key=lambda item: item.get("created_at", 0))
        self._send(chat_id, T.orders_list(self.cfg, pending))

    def _on_admin_command(self, chat_id: int, msg: dict, command: str, argument: str) -> None:
        if command == "start":
            self._clear_session(chat_id)
            self._register_admin(msg.get("from") or {})
            self._send(
                chat_id,
                T.admin_welcome(self.cfg, (msg.get("from") or {}).get("first_name")),
            )
            return
        if command == "id":
            self._send(
                chat_id,
                f"🆔 Твой chat_id: <code>{chat_id}</code>\n"
                f"Username: @{html.escape((msg.get('from') or {}).get('username') or '—')}",
            )
            return
        if command == "orders":
            self._send_pending(chat_id)
            return
        if command == "stats":
            self._send(chat_id, T.stats_text(self.cfg, self.store.stats(), self._admin_chat_ids()))
            return
        if command == "link":
            self._send(chat_id, T.link_text(self.cfg))
            return
        if command == "cancel":
            self._cancel_admin_step(chat_id)
            return
        self._send(chat_id, T.unknown_command(self.cfg))

    def _cancel_admin_step(self, chat_id: int) -> None:
        session = self._session(chat_id)
        order_id = session.get("order_id")
        step = session.get("step")
        self._clear_session(chat_id)

        if step == "product" and order_id:
            order = self.store.orders.get(order_id)
            if isinstance(order, dict) and order.get("status") == "awaiting_product":
                self._set_status(order, "pending")
                self._send(chat_id, T.order_cancelled_accept(order_id))
                self._save()
                return
        if step == "chat":
            self._send(chat_id, T.chat_closed())
            return
        self._send(chat_id, "Ок, действие отменено.")
