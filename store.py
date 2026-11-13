"""Хранилище состояния в JSON-файле (атомарная запись, без внешних БД)."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def _defaults() -> dict:
    return {
        "version": 1,
        "offset": 0,        # последний обработанный update_id + 1
        "admins": [],       # chat_id операторов
        "sessions": {},     # "chat_id" -> состояние диалога
        "orders": {},       # "id" -> заявка
        "msgmap": {},       # "chat_id:message_id" -> заявка/собеседник
        "counter": 1,       # следующий номер заявки
    }


class Store:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.data = _defaults()
        self.load()

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            log.error("Не удалось прочитать %s (%s), сохраняю копию в %s", self.path, exc, backup)
            try:
                shutil.copyfile(self.path, backup)
            except OSError:
                pass
            return

        if not isinstance(loaded, dict):
            log.error("Файл %s повреждён: ожидался объект", self.path)
            return

        for key, value in loaded.items():
            if key in self.data:
                self.data[key] = value

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("Не удалось сохранить состояние в %s: %s", self.path, exc)

    # ------------------------------------------------------------- helpers
    @property
    def orders(self) -> dict:
        return self.data.setdefault("orders", {})

    @property
    def sessions(self) -> dict:
        return self.data.setdefault("sessions", {})

    def next_order_id(self) -> str:
        number = int(self.data.get("counter") or 1000)
        self.data["counter"] = number + 1
        return str(number)

    def stats(self) -> dict:
        result = {"total": 0, "pending": 0, "awaiting_product": 0, "accepted": 0, "rejected": 0}
        for order in self.orders.values():
            if not isinstance(order, dict):
                continue
            result["total"] += 1
            status = order.get("status", "pending")
            if status in result:
                result[status] += 1
        return result
