"""Phase 10 slice 10D: Telegram adapter, credentials and linking. The token must never leak."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx2
import pytest

from aaos.notifications.adapter import DeliveryError
from aaos.notifications.telegram import (
    TelegramCredentials,
    TelegramLinker,
    TelegramNotifier,
    TelegramUnavailable,
    load_credentials,
    save_credentials,
)

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
CHAT = 987654321


def client(handler) -> httpx2.Client:  # type: ignore[no-untyped-def]
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def ok(result: object) -> httpx2.Response:
    return httpx2.Response(200, json={"ok": True, "result": result})


def notifier(handler) -> TelegramNotifier:  # type: ignore[no-untyped-def]
    return TelegramNotifier(TelegramCredentials(TOKEN, CHAT), client=client(handler))


def test_a_message_is_sent_as_escaped_html_to_the_linked_chat_only() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return ok({"message_id": 1})

    result = notifier(handler).send("<b>AAOS</b> hi")

    body = json.loads(seen[0].content)
    assert result.sent
    assert seen[0].url.path.endswith("/sendMessage")
    assert body["chat_id"] == CHAT
    assert body["parse_mode"] == "HTML"
    assert body["link_preview_options"] == {"is_disabled": True}


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (
            401,
            {"ok": False, "error_code": 401, "description": "Unauthorized"},
            DeliveryError.TOKEN_INVALID,
        ),
        (
            403,
            {"ok": False, "error_code": 403, "description": "bot was blocked"},
            DeliveryError.CHAT_FORBIDDEN,
        ),
        (
            400,
            {"ok": False, "error_code": 400, "description": "can't parse entities"},
            DeliveryError.MESSAGE_INVALID,
        ),
        (500, {"ok": False}, DeliveryError.PROVIDER_ERROR),
    ],
)
def test_provider_errors_map_to_closed_codes(status, payload, expected) -> None:  # type: ignore[no-untyped-def]
    result = notifier(lambda _r: httpx2.Response(status, json=payload)).send("x")

    assert (result.sent, result.error) == (False, expected)


def test_rate_limits_carry_retry_after() -> None:
    result = notifier(
        lambda _r: httpx2.Response(
            429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 17}}
        )
    ).send("x")

    assert (result.error, result.retry_after_seconds) == (DeliveryError.RATE_LIMITED, 17)


def test_timeouts_and_network_errors_are_closed_codes() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("timed out", request=request)

    def network(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TransportError("connection reset")

    assert notifier(timeout).send("x").error is DeliveryError.TIMEOUT
    assert notifier(network).send("x").error is DeliveryError.NETWORK


def test_the_token_never_reaches_logs_results_or_reprs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def network(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TransportError(f"failed for {request.url}")

    sent = notifier(lambda _r: ok({"message_id": 1})).send("x")
    failed = notifier(network).send("x")
    credentials = TelegramCredentials(TOKEN, CHAT)

    everything = " ".join(
        [caplog.text, repr(sent), repr(failed), repr(credentials), str(credentials)]
    )
    assert TOKEN not in everything
    assert TOKEN.split(":")[1] not in everything


def test_credentials_live_outside_the_repository() -> None:
    import tempfile

    # The system temp folder: pytest's --basetemp may sit inside the repository,
    # which the guard rightly refuses.
    path = Path(tempfile.mkdtemp()) / "credentials" / "telegram.json"

    save_credentials(path, TelegramCredentials(TOKEN, CHAT))

    assert load_credentials(path) == TelegramCredentials(TOKEN, CHAT)
    repository_path = Path(__file__).resolve().parents[1] / "telegram.json"
    with pytest.raises(TelegramUnavailable):
        save_credentials(repository_path, TelegramCredentials(TOKEN, CHAT))


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"bot_token": "bad", "chat_id": CHAT}),
        json.dumps({"bot_token": TOKEN}),
    ],
)
def test_bad_credential_files_are_unavailable_without_details(tmp_path: Path, content: str) -> None:
    path = tmp_path / "telegram.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(TelegramUnavailable) as error:
        load_credentials(path)
    assert TOKEN not in str(error.value)


def update(
    update_id: int, chat_id: int, text: str, chat_type: str = "private"
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"text": text, "chat": {"id": chat_id, "type": chat_type}},
    }


def linker(updates: list[dict[str, object]]) -> TelegramLinker:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/getMe"):
            return ok({"username": "aaos_test_bot"})
        offset = int(json.loads(request.content or b"{}").get("offset", 0))
        return ok([item for item in updates if int(item["update_id"]) >= offset])  # type: ignore[call-overload]

    return TelegramLinker(TOKEN, client=client(handler))


def test_linking_takes_the_one_private_start_sent_after_the_prompt() -> None:
    link = linker([update(5, 111, "/start"), update(9, CHAT, "/start")])

    assert link.bot_username() == "aaos_test_bot"
    assert link.chat_after(after_update_id=5) == CHAT


@pytest.mark.parametrize(
    "updates",
    [
        [],
        [update(9, CHAT, "hello")],
        [update(9, CHAT, "/start", chat_type="group")],
        [update(9, CHAT, "/start"), update(10, 222, "/start")],
    ],
)
def test_linking_refuses_anything_but_exactly_one_private_start(updates) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TelegramUnavailable):
        linker(updates).chat_after(after_update_id=5)
