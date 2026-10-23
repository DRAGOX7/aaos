"""Phase 11A: the ChatGPT-plan settings and the owner-only credential store."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from aaos.config import AppSettings, settings_from_environment
from aaos.inference.chatgpt_plan_credentials import (
    CHATGPT_PLAN_CREDENTIAL_PATH_ENVIRONMENT_KEY,
    PLAN_USAGE_SCOPE,
    ChatGPTCredentialStore,
    ChatGPTPlanUnavailable,
    RefreshRejected,
    TokenSet,
    credential_path_from_environment,
    is_owner_only,
)
from aaos.inference.factory import active_model_identifier
from aaos.inference.gateway import InferenceConnectionError

ACCESS = "access-token-SECRET-0123456789"
REFRESH = "refresh-token-SECRET-9876543210"
NEW_ACCESS = "access-token-NEW-SECRET-abcdef"
NEW_REFRESH = "refresh-token-NEW-SECRET-fedcba"
SCOPES = ("email", "offline_access", "openid", "profile", "resource.invoke", PLAN_USAGE_SCOPE)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _tokens(
    access: str = ACCESS,
    refresh: str = REFRESH,
    expires_at: float = 1_000_000.0 + 3600,
    scopes: tuple[str, ...] = SCOPES,
) -> TokenSet:
    return TokenSet(
        access_token=access, refresh_token=refresh, expires_at=expires_at, scopes=scopes
    )


class Refresher:
    def __init__(self, result: TokenSet | BaseException | None = None, delay: float = 0.0) -> None:
        self.result = result or _tokens(NEW_ACCESS, NEW_REFRESH, expires_at=1_000_000.0 + 7200)
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, client_id: str, refresh_token: str) -> TokenSet:
        self.calls.append((client_id, refresh_token))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _store(
    tmp_path: Path, refresher: Refresher | None = None, clock: Clock | None = None
) -> ChatGPTCredentialStore:
    return ChatGPTCredentialStore(
        tmp_path / "credentials" / "chatgpt-plan.json",
        refresher=refresher or Refresher(),
        clock=clock or Clock(),
        lock_poll_seconds=0.01,
    )


def _signed_in(tmp_path: Path, **kwargs: object) -> ChatGPTCredentialStore:
    store = _store(tmp_path, **kwargs)  # type: ignore[arg-type]
    store.save_sign_in(client_id="oaiapp_test", account_subject="user-sub-1", tokens=_tokens())
    return store


# --- settings -------------------------------------------------------------------------------


def test_the_chatgpt_plan_provider_needs_a_model_and_names_it_as_the_answering_model() -> None:
    settings = settings_from_environment(
        {"AAOS_MODEL_PROVIDER": "chatgpt_plan", "AAOS_CHATGPT_PLAN_MODEL": " gpt-test "}
    )
    assert settings.model_provider == "chatgpt_plan"
    assert settings.chatgpt_plan_model == "gpt-test"
    assert active_model_identifier(settings) == "gpt-test"
    with pytest.raises(ValueError, match="AAOS_CHATGPT_PLAN_MODEL"):
        settings_from_environment({"AAOS_MODEL_PROVIDER": "chatgpt_plan"})


def test_a_chatgpt_plan_model_without_that_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="chatgpt_plan_model"):
        AppSettings(chatgpt_plan_model="gpt-test")
    with pytest.raises(ValueError, match="chatgpt_plan_model"):
        AppSettings(model_provider="gemini", gemini_model="g", chatgpt_plan_model="gpt-test")


def test_the_local_model_stays_the_default() -> None:
    settings = settings_from_environment({})
    assert settings.model_provider == "lm_studio"
    assert settings.chatgpt_plan_model is None


# --- where the file lives --------------------------------------------------------------------


def test_the_default_credential_path_is_outside_the_repository(tmp_path: Path) -> None:
    path = credential_path_from_environment({"LOCALAPPDATA": str(tmp_path)})
    assert path == tmp_path / "AAOS" / "credentials" / "chatgpt-plan.json"
    override = tmp_path / "elsewhere" / "plan.json"
    assert (
        credential_path_from_environment(
            {
                "LOCALAPPDATA": str(tmp_path),
                CHATGPT_PLAN_CREDENTIAL_PATH_ENVIRONMENT_KEY: str(override),
            }
        )
        == override
    )


@pytest.mark.parametrize(
    "path",
    [
        REPOSITORY_ROOT / "data" / "credentials" / "chatgpt-plan.json",
        Path("relative") / "chatgpt-plan.json",
    ],
)
def test_a_path_inside_the_repository_or_relative_is_refused(path: Path) -> None:
    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        ChatGPTCredentialStore(path, refresher=Refresher())
    assert caught.value.category == "NOT_CONFIGURED"


# --- states without a network call ----------------------------------------------------------


def test_with_no_file_the_store_is_not_configured(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.state() == ("NOT_CONFIGURED", None)
    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        asyncio.run(store.access_token())
    assert caught.value.category == "NOT_CONFIGURED"


def test_a_saved_sign_in_is_owner_only_and_gives_its_token(tmp_path: Path) -> None:
    store = _signed_in(tmp_path)
    assert is_owner_only(store.path)
    if sys.platform != "win32":
        assert store.path.stat().st_mode & 0o077 == 0
    state, expires_at = store.state()
    assert state == "SIGNED_IN"
    assert expires_at == 1_000_000.0 + 3600
    assert asyncio.run(store.access_token()) == ACCESS


def test_the_host_id_is_made_once_and_kept(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.host_id()
    assert first.startswith("urn:uuid:")
    assert store.host_id() == first
    assert _store(tmp_path).host_id() == first
    store.save_sign_in(client_id="oaiapp_test", account_subject="sub", tokens=_tokens())
    assert store.host_id() == first
    assert store.client_id() == "oaiapp_test"


def test_a_file_others_can_read_is_refused(tmp_path: Path) -> None:
    store = _signed_in(tmp_path)
    content = store.path.read_bytes()
    store.path.unlink()
    store.path.write_bytes(content)  # recreated with the folder's ordinary permissions
    if sys.platform != "win32":
        store.path.chmod(0o644)
    assert not is_owner_only(store.path)
    assert store.state() == ("SIGN_IN_REQUIRED", None)
    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        asyncio.run(store.access_token())
    assert caught.value.category == "SIGN_IN_REQUIRED"


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"[]", b'{"version": 99}', b"x" * 70000],
    ids=["not-json", "a-list", "unknown-version", "oversized"],
)
def test_an_unreadable_or_oversized_file_needs_a_new_sign_in(
    tmp_path: Path, content: bytes
) -> None:
    store = _signed_in(tmp_path)
    original = store.path.read_bytes()
    store._write_atomic(content)  # an owner-only file whose content is wrong
    assert store.state()[0] == "SIGN_IN_REQUIRED"
    assert original != store.path.read_bytes()


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _signed_in(tmp_path)
    before = store.path.read_bytes()

    def broken_replace(source: object, destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(ChatGPTPlanUnavailable):
        store.save_sign_in(client_id="oaiapp_other", account_subject="x", tokens=_tokens("other"))
    monkeypatch.undo()
    assert store.path.read_bytes() == before
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]


# --- refresh --------------------------------------------------------------------------------


def test_an_expiring_token_is_refreshed_and_the_new_refresh_token_is_kept(tmp_path: Path) -> None:
    clock = Clock()
    refresher = Refresher()
    store = _signed_in(tmp_path, refresher=refresher, clock=clock)
    clock.now += 3600 - 60  # inside the two-minute margin
    assert asyncio.run(store.access_token()) == NEW_ACCESS
    assert refresher.calls == [("oaiapp_test", REFRESH)]
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["tokens"]["refresh_token"] == NEW_REFRESH
    assert is_owner_only(store.path)
    # The rotated token is the one used next time.
    clock.now += 7200
    refresher.result = _tokens("third-access", "third-refresh", expires_at=clock.now + 3600)
    assert asyncio.run(store.access_token()) == "third-access"
    assert refresher.calls[-1] == ("oaiapp_test", NEW_REFRESH)


def test_two_requests_at_once_refresh_only_once(tmp_path: Path) -> None:
    clock = Clock()
    refresher = Refresher(delay=0.05)
    store = _signed_in(tmp_path, refresher=refresher, clock=clock)
    clock.now += 3590

    async def both() -> list[str]:
        return list(await asyncio.gather(store.access_token(), store.access_token()))

    assert asyncio.run(both()) == [NEW_ACCESS, NEW_ACCESS]
    assert len(refresher.calls) == 1


def test_a_refresh_by_another_process_is_reread_not_repeated(tmp_path: Path) -> None:
    clock = Clock()
    refresher = Refresher()
    store = _signed_in(tmp_path, refresher=refresher, clock=clock)
    clock.now += 3590
    # What the other process will have written: built elsewhere, then copied in.
    elsewhere = _store(tmp_path / "elsewhere", clock=clock)
    elsewhere.save_sign_in(
        client_id="oaiapp_test",
        account_subject="user-sub-1",
        tokens=_tokens("from-other", "rotated-by-other", expires_at=clock.now + 3600),
    )
    refreshed_elsewhere = elsewhere.path.read_bytes()
    lock = store.path.with_name(store.path.name + ".lock")
    lock.write_bytes(b"held-by-another-process")

    async def scenario() -> str:
        async def other_process_finishes() -> None:
            await asyncio.sleep(0.05)
            store._write_atomic(refreshed_elsewhere)
            lock.unlink()

        waiter = asyncio.create_task(other_process_finishes())
        token = await store.access_token()
        await waiter
        return token

    assert asyncio.run(scenario()) == "from-other"
    assert refresher.calls == []


def test_a_stale_lock_is_broken(tmp_path: Path) -> None:
    clock = Clock()
    refresher = Refresher()
    store = _signed_in(tmp_path, refresher=refresher, clock=clock)
    clock.now += 3590
    lock = store.path.with_name(store.path.name + ".lock")
    lock.write_bytes(b"left-by-a-crash")
    old = time.time() - 120
    os.utime(lock, (old, old))
    assert asyncio.run(store.access_token()) == NEW_ACCESS
    assert not lock.exists()


def test_a_rejected_refresh_needs_a_new_sign_in_and_is_not_retried(tmp_path: Path) -> None:
    clock = Clock()
    refresher = Refresher(RefreshRejected("invalid_grant"))
    store = _signed_in(tmp_path, refresher=refresher, clock=clock)
    clock.now += 3590
    for _ in range(2):
        with pytest.raises(ChatGPTPlanUnavailable) as caught:
            asyncio.run(store.access_token())
        assert caught.value.category == "SIGN_IN_REQUIRED"
    assert len(refresher.calls) == 1
    assert store.state()[0] == "SIGN_IN_REQUIRED"
    # The tokens are kept, unchanged, for diagnosis.
    assert json.loads(store.path.read_text(encoding="utf-8"))["tokens"]["refresh_token"] == REFRESH


def test_a_network_failure_during_refresh_keeps_the_sign_in(tmp_path: Path) -> None:
    clock = Clock()
    refresher = Refresher(InferenceConnectionError("offline"))
    store = _signed_in(tmp_path, refresher=refresher, clock=clock)
    clock.now += 3590
    with pytest.raises(InferenceConnectionError):
        asyncio.run(store.access_token())
    assert store.state()[0] == "SIGNED_IN"
    refresher.result = _tokens(NEW_ACCESS, NEW_REFRESH, expires_at=clock.now + 3600)
    assert asyncio.run(store.access_token()) == NEW_ACCESS


def test_a_refresh_that_drops_plan_usage_is_reported(tmp_path: Path) -> None:
    clock = Clock()
    reduced = _tokens(NEW_ACCESS, NEW_REFRESH, expires_at=1_000_000.0 + 7200, scopes=("openid",))
    store = _signed_in(tmp_path, refresher=Refresher(reduced), clock=clock)
    clock.now += 3590
    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        asyncio.run(store.access_token())
    assert caught.value.category == "PLAN_USAGE_NOT_GRANTED"
    assert store.state()[0] == "PLAN_USAGE_NOT_GRANTED"


def test_a_forced_refresh_replaces_a_token_the_server_refused(tmp_path: Path) -> None:
    refresher = Refresher()
    store = _signed_in(tmp_path, refresher=refresher)
    assert asyncio.run(store.force_refresh(rejected_access_token=ACCESS)) == NEW_ACCESS
    # A second caller that saw the same rejected token reuses the new one.
    assert asyncio.run(store.force_refresh(rejected_access_token=ACCESS)) == NEW_ACCESS
    assert len(refresher.calls) == 1


# --- sign-out and secrecy -------------------------------------------------------------------


def test_sign_out_removes_the_tokens_and_keeps_the_host_and_client(tmp_path: Path) -> None:
    store = _signed_in(tmp_path)
    host = store.host_id()
    previous = store.sign_out()
    assert previous is not None and previous.refresh_token == REFRESH
    assert store.state() == ("SIGN_IN_REQUIRED", None)
    text = store.path.read_text(encoding="utf-8")
    assert ACCESS not in text and REFRESH not in text
    assert store.host_id() == host
    assert store.client_id() == "oaiapp_test"
    assert store.sign_out() is None


def test_no_token_appears_in_repr_str_or_errors(tmp_path: Path) -> None:
    store = _signed_in(tmp_path)
    tokens = _tokens()
    shown = " ".join([repr(tokens), str(tokens), repr(store), str(store)])
    for secret in (ACCESS, REFRESH):
        assert secret not in shown
    clock = Clock()
    failing = _signed_in(tmp_path, refresher=Refresher(RefreshRejected(REFRESH)), clock=clock)
    clock.now += 3590
    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        asyncio.run(failing.access_token())
    assert REFRESH not in str(caught.value) and REFRESH not in repr(caught.value)
    assert caught.value.__cause__ is None
