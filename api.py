"""Минимальный клиент Telegram Bot API.

Только стандартная библиотека: ни requests, ни aiogram не нужны.
Умеет: long polling, повторы при 429/5xx/сетевых сбоях, разбор ошибок API.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    """Ошибка вызова метода Bot API."""

    def __init__(
        self,
        method: str,
        description: str,
        code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"{method}: {description} (code={code})")
        self.method = method
        self.description = description
        self.code = code
        self.retry_after = retry_after


def _decode(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _retry_after(payload: dict) -> int | None:
    params = payload.get("parameters")
    if isinstance(params, dict) and isinstance(params.get("retry_after"), int):
        return max(1, min(params["retry_after"], 60))
    return None


class TelegramAPI:
    """Тонкая обёртка над https://api.telegram.org/bot<token>/<method>."""

    RETRY_HTTP = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.telegram.org",
        timeout: float = 65.0,
        max_retries: int = 4,
        proxy: str | None = None,
    ) -> None:
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = (proxy or "").strip() or None
        self._opener = None
        if self.proxy:
            handlers = [urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})]
            self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------------ core
    def _open(self, request: urllib.request.Request, timeout: float):
        if self._opener is not None:
            return self._opener.open(request, timeout=timeout)
        return urllib.request.urlopen(request, timeout=timeout)

    def _request(self, method: str, params: dict, request_timeout: float):
        url = f"{self.base_url}/bot{self._token}/{method}"
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        delay = 1.0

        for attempt in range(1, self.max_retries + 2):
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            try:
                with self._open(request, request_timeout) as response:
                    raw = response.read()
            except urllib.error.HTTPError as exc:
                payload = _decode(exc.read() or b"")
                code = exc.code
                if code not in self.RETRY_HTTP or attempt > self.max_retries:
                    raise TelegramError(
                        method,
                        payload.get("description") or f"HTTP {code}",
                        code,
                        _retry_after(payload),
                    ) from None
                wait = _retry_after(payload) or delay
                log.warning("%s: %s — повтор через %.0f c", method, payload.get("description", code), wait)
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
                if attempt > self.max_retries:
                    reason = getattr(exc, "reason", exc)
                    if isinstance(reason, socket.gaierror):
                        raise TelegramError(
                            method,
                            "DNS не смог разрешить api.telegram.org — нет интернета, "
                            "DNS недоступен или провайдер блокирует Telegram. "
                            "Поможет VPN, прокси (TELEGRAM_PROXY) или DNS 1.1.1.1",
                        ) from None
                    raise TelegramError(method, f"сеть недоступна: {exc}") from None
                log.warning("%s: сеть (%s) — повтор через %.0f c", method, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            payload = _decode(raw)
            if not payload.get("ok", False):
                raise TelegramError(
                    method,
                    payload.get("description", "неизвестная ошибка"),
                    payload.get("error_code"),
                    _retry_after(payload),
                )
            return payload.get("result")

        raise TelegramError(method, "исчерпаны попытки повтора")

    def call(self, method: str, **params):
        """Вызов любого метода Bot API. Параметры уходят в JSON-теле."""
        return self._request(method, params, self.timeout)

    # ------------------------------------------------------------- helpers
    def get_me(self) -> dict:
        return self.call("getMe") or {}

    def get_updates(self, offset: int = 0, timeout: int = 25, allowed_updates=None):
        params = {"offset": offset, "timeout": timeout, "limit": 100}
        if allowed_updates:
            params["allowed_updates"] = list(allowed_updates)
        # HTTP-таймаут должен быть заметно больше long-poll таймаута.
        return self._request("getUpdates", params, request_timeout=timeout + 25) or []

    def set_my_commands(self, commands) -> bool:
        return bool(self.call("setMyCommands", commands=commands))
