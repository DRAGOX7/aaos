"""Send-only Telegram adapter (Phase 10 spec §6).

The bot token sits in the request URL, so this module never logs URLs,
silences HTTP-client request logging, catches every transport error and
returns only closed error codes. Credentials live in a JSON file outside
the repository; any problem reading them is a bare TelegramUnavailable.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

from aaos.notifications.adapter import DeliveryError, DeliveryResult

API = "https://api.telegram.org"
TIMEOUT_SECONDS = 10.0
_TOKEN = re.compile(r"\d{5,12}:[A-Za-z0-9_-]{30,64}")
_MAX_CREDENTIAL_BYTES = 4096

# HTTP client libraries log each request URL at INFO; for Telegram that URL
# contains the token, so their loggers stay above INFO.
for _name in ("httpx", "httpx2", "httpcore", "httpcore2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class TelegramUnavailable(RuntimeError):
    """Telegram is not configured or not reachable; deliberately without details."""

    def __init__(self) -> None:
        super().__init__("Telegram unavailable")


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    bot_token: str = field(repr=False)
    chat_id: int

    def __str__(self) -> str:
        return f"TelegramCredentials(chat_id={self.chat_id})"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _checked_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_absolute() or resolved.is_relative_to(_repository_root()):
        raise TelegramUnavailable()
    return resolved


def load_credentials(path: Path) -> TelegramCredentials:
    try:
        resolved = _checked_path(path)
        if not resolved.is_file() or resolved.stat().st_size > _MAX_CREDENTIAL_BYTES:
            raise ValueError
        data = json.loads(resolved.read_text(encoding="utf-8"))
        token, chat_id = data["bot_token"], data["chat_id"]
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError
        if isinstance(chat_id, bool) or not isinstance(chat_id, int):
            raise ValueError
        return TelegramCredentials(token, chat_id)
    except Exception:
        raise TelegramUnavailable() from None


def save_credentials(path: Path, credentials: TelegramCredentials) -> None:
    try:
        resolved = _checked_path(path)
        if not _TOKEN.fullmatch(credentials.bot_token):
            raise ValueError
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps({"bot_token": credentials.bot_token, "chat_id": credentials.chat_id}),
            encoding="utf-8",
        )
    except Exception:
        raise TelegramUnavailable() from None


def is_valid_token(token: str) -> bool:
    return _TOKEN.fullmatch(token) is not None


class _Api:
    def __init__(self, token: str, client: httpx2.Client | None) -> None:
        self._token = token
        self._client = client or httpx2.Client(timeout=TIMEOUT_SECONDS)

    def call(
        self, method: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        """POST one Bot API method; raises only TimeoutError or ConnectionError, without detail."""
        try:
            response = self._client.post(f"{API}/bot{self._token}/{method}", json=payload or {})
        except httpx2.TimeoutException:
            raise TimeoutError() from None
        except Exception:
            raise ConnectionError() from None
        try:
            body = response.json()
        except Exception:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}


class TelegramNotifier:
    """Sends one HTML message to the single linked chat."""

    def __init__(
        self, credentials: TelegramCredentials, *, client: httpx2.Client | None = None
    ) -> None:
        self._chat_id = credentials.chat_id
        self._api = _Api(credentials.bot_token, client)

    def send(self, html_message: str) -> DeliveryResult:
        payload = {
            "chat_id": self._chat_id,
            "text": html_message,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        try:
            status, body = self._api.call("sendMessage", payload)
        except TimeoutError:
            return DeliveryResult(sent=False, error=DeliveryError.TIMEOUT)
        except ConnectionError:
            return DeliveryResult(sent=False, error=DeliveryError.NETWORK)
        if status == 200 and body.get("ok") is True:
            return DeliveryResult(sent=True)
        if status == 429:
            retry = (
                body.get("parameters", {}).get("retry_after")
                if isinstance(body.get("parameters"), dict)
                else None
            )
            return DeliveryResult(
                sent=False,
                error=DeliveryError.RATE_LIMITED,
                retry_after_seconds=retry if isinstance(retry, int) and retry > 0 else None,
            )
        error = {
            401: DeliveryError.TOKEN_INVALID,
            404: DeliveryError.TOKEN_INVALID,
            403: DeliveryError.CHAT_FORBIDDEN,
            400: DeliveryError.MESSAGE_INVALID,
        }.get(status, DeliveryError.PROVIDER_ERROR)
        return DeliveryResult(sent=False, error=error)


class TelegramLinker:
    """One-time linking: identify the bot and the owner's private /start chat."""

    def __init__(self, token: str, *, client: httpx2.Client | None = None) -> None:
        if not is_valid_token(token):
            raise TelegramUnavailable()
        self._api = _Api(token, client)

    def _result(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            status, body = self._api.call(method, payload)
        except (TimeoutError, ConnectionError):
            raise TelegramUnavailable() from None
        if status != 200 or body.get("ok") is not True:
            raise TelegramUnavailable()
        return body.get("result")

    def bot_username(self) -> str:
        result = self._result("getMe")
        username = result.get("username") if isinstance(result, dict) else None
        if not isinstance(username, str):
            raise TelegramUnavailable()
        return username

    def latest_update_id(self) -> int:
        updates = self._result("getUpdates", {"timeout": 0})
        ids = [
            item["update_id"] for item in updates or [] if isinstance(item.get("update_id"), int)
        ]
        return max(ids, default=0)

    def chat_after(self, *, after_update_id: int) -> int:
        """The one private chat that sent /start after the prompt; anything else is refused."""
        updates = self._result("getUpdates", {"offset": after_update_id + 1, "timeout": 0})
        chats: set[int] = set()
        for item in updates or []:
            message = item.get("message") if isinstance(item, dict) else None
            if not isinstance(message, dict) or item.get("update_id", 0) <= after_update_id:
                continue
            chat = message.get("chat") or {}
            if (
                isinstance(message.get("text"), str)
                and message["text"].strip().split("@")[0] == "/start"
                and chat.get("type") == "private"
                and isinstance(chat.get("id"), int)
            ):
                chats.add(chat["id"])
        if len(chats) != 1:
            raise TelegramUnavailable()
        return chats.pop()
