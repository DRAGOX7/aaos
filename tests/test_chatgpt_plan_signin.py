"""Phase 11B: the owner-started Sign in with ChatGPT flow and the CLI commands."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aaos.inference.chatgpt_plan_credentials import (
    PLAN_USAGE_SCOPE,
    ChatGPTCredentialStore,
    RefreshRejected,
)
from aaos.inference.chatgpt_plan_signin import (
    AUTH_ISSUER,
    AUTHORIZE_URL,
    DISCOVERY_URL,
    DYNAMIC_CLIENT_ID,
    TOKEN_URL,
    AuthClient,
    CallbackListener,
    SignInFailed,
    authorize_url,
    make_pkce,
    sign_in,
)
from aaos.inference.gateway import InferenceConnectionError

ISSUED_CLIENT = "oaiapp_issued_123"
SUBJECT = "user-abc"
ACCESS = "access-SECRET-from-exchange"
REFRESH = "refresh-SECRET-from-exchange"
CODE = "authorization-code-SECRET"
FULL_SCOPE = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"
JWKS_URI = "https://auth.openai.com/.well-known/jwks.json"
REVOCATION_URI = "https://auth.openai.com/api/accounts/oauth/revoke"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key()))
    jwk.update({"kid": "key-1", "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _id_token(nonce: str, /, **overrides: Any) -> str:
    overrides = dict(overrides)
    key = overrides.pop("_key", _KEY)
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": AUTH_ISSUER,
        "aud": ISSUED_CLIENT,
        "sub": SUBJECT,
        "iat": now,
        "exp": now + 3600,
        "nonce": nonce,
        "email": "owner@example.com",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})


class FakeAuthServer:
    """The parts of auth.openai.com the flow talks to, over a mock transport."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.token_scope = FULL_SCOPE
        self.id_token_overrides: dict[str, Any] = {}
        self.refresh_status = 200
        self.refresh_body: dict[str, Any] = {}
        self.discovery: dict[str, Any] = {
            "issuer": AUTH_ISSUER,
            "jwks_uri": JWKS_URI,
            "revocation_endpoint": REVOCATION_URI,
        }
        self.nonce: str | None = None

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == DISCOVERY_URL:
            return httpx2.Response(200, json=self.discovery)
        if url == JWKS_URI:
            return httpx2.Response(200, json=_jwks())
        if url == REVOCATION_URI:
            return httpx2.Response(200)
        if url == TOKEN_URL:
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                if self.refresh_status != 200:
                    return httpx2.Response(self.refresh_status, json=self.refresh_body)
                return httpx2.Response(
                    200,
                    json={
                        "access_token": "access-REFRESHED",
                        "refresh_token": "refresh-ROTATED",
                        "expires_in": 3600,
                        "scope": self.token_scope,
                        "token_type": "Bearer",
                    },
                )
            assert self.nonce is not None
            return httpx2.Response(
                200,
                json={
                    "access_token": ACCESS,
                    "refresh_token": REFRESH,
                    "id_token": _id_token(self.nonce, **self.id_token_overrides),
                    "expires_in": 3600,
                    "scope": self.token_scope,
                    "token_type": "Bearer",
                },
            )
        return httpx2.Response(404)

    def token_forms(self) -> list[dict[str, list[str]]]:
        return [parse_qs(r.content.decode()) for r in self.requests if str(r.url) == TOKEN_URL]


def _browser(
    server: FakeAuthServer,
    *,
    callback: Callable[[dict[str, list[str]]], dict[str, str]] | None = None,
    record: list[str] | None = None,
) -> Callable[[str], bool]:
    """A browser that 'approves' at once and calls the local listener back."""

    def open_browser(url: str) -> bool:
        if record is not None:
            record.append(url)
        query = parse_qs(urlsplit(url).query)
        server.nonce = query["nonce"][0]
        params = (
            callback(query)
            if callback
            else {"code": CODE, "state": query["state"][0], "client_id": ISSUED_CLIENT}
        )
        target = query["redirect_uri"][0] + "?" + urlencode(params)

        def call() -> None:
            time.sleep(0.05)
            try:
                urllib.request.urlopen(target, timeout=5).read()
            except Exception:
                pass

        threading.Thread(target=call, daemon=True).start()
        return True

    return open_browser


def _store(tmp_path: Path) -> ChatGPTCredentialStore:
    return ChatGPTCredentialStore(tmp_path / "plan.json", lock_poll_seconds=0.01)


def _sign_in(
    tmp_path: Path, server: FakeAuthServer, *, timeout: float = 10.0, **browser: Any
) -> Any:
    store = _store(tmp_path)

    async def run() -> Any:
        auth = AuthClient(server.transport())
        try:
            return await sign_in(
                store,
                auth=auth,
                open_browser=_browser(server, **browser),
                timeout_seconds=timeout,
                preferred_port=0,
            )
        finally:
            await auth.aclose()

    return store, asyncio.run(run())


# --- the authorize request -------------------------------------------------------------------


def test_pkce_values_are_fresh_and_the_challenge_is_s256() -> None:
    first, second = make_pkce(), make_pkce()
    assert first.verifier != second.verifier and first.state != second.state
    assert first.nonce != second.nonce
    digest = hashlib.sha256(first.verifier.encode("ascii")).digest()
    assert first.challenge == base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert 43 <= len(first.verifier) <= 128


def test_the_first_sign_in_registers_and_a_later_one_uses_the_issued_client() -> None:
    pkce = make_pkce()
    first = parse_qs(
        urlsplit(
            authorize_url(
                client_id=None,
                host_id="urn:uuid:00000000-0000-4000-8000-000000000000",
                redirect_uri="http://127.0.0.1:1455/auth/callback",
                pkce=pkce,
            )
        ).query
    )
    assert first["client_id"] == [DYNAMIC_CLIENT_ID]
    assert first["agent_name_hint"] == ["AAOS"]
    assert first["ext_agent_host_id"] == ["urn:uuid:00000000-0000-4000-8000-000000000000"]
    assert first["response_type"] == ["code"]
    assert first["code_challenge_method"] == ["S256"]
    assert first["code_challenge"] == [pkce.challenge]
    assert first["resource"] == ["https://api.openai.com/v1"]
    assert set(first["scope"][0].split()) == set(FULL_SCOPE.split())
    later_url = authorize_url(
        client_id=ISSUED_CLIENT,
        host_id="urn:uuid:00000000-0000-4000-8000-000000000000",
        redirect_uri="http://127.0.0.1:1455/auth/callback",
        pkce=pkce,
    )
    later = parse_qs(urlsplit(later_url).query)
    assert later_url.startswith(AUTHORIZE_URL + "?")
    assert later["client_id"] == [ISSUED_CLIENT]
    assert "agent_name_hint" not in later
    assert later["ext_agent_host_id"] == first["ext_agent_host_id"]


# --- the whole flow --------------------------------------------------------------------------


def test_a_sign_in_saves_the_plan_tokens_through_the_store(tmp_path: Path) -> None:
    server = FakeAuthServer()
    seen: list[str] = []
    store, result = _sign_in(tmp_path, server, record=seen)
    assert result.scopes == tuple(sorted(FULL_SCOPE.split()))
    assert store.state()[0] == "SIGNED_IN"
    assert store.client_id() == ISSUED_CLIENT
    assert asyncio.run(store.access_token()) == ACCESS
    redirect = parse_qs(urlsplit(seen[0]).query)["redirect_uri"][0]
    assert redirect.startswith("http://127.0.0.1:") and redirect.endswith("/auth/callback")
    (form,) = server.token_forms()
    assert form["grant_type"] == ["authorization_code"]
    assert form["client_id"] == [ISSUED_CLIENT]  # never dynamic_agent_client
    assert form["code"] == [CODE]
    assert form["redirect_uri"] == [redirect]
    assert form["resource"] == ["https://api.openai.com/v1"]
    assert "code_verifier" in form
    saved = store.path.read_text(encoding="utf-8")
    assert "owner@example.com" not in saved and "id_token" not in saved


def test_a_later_sign_in_keeps_the_client_and_host(tmp_path: Path) -> None:
    server = FakeAuthServer()
    store, _ = _sign_in(tmp_path, server)
    host = store.host_id()
    seen: list[str] = []
    _sign_in(
        tmp_path,
        server,
        record=seen,
        callback=lambda q: {"code": CODE, "state": q["state"][0]},  # client_id may be omitted
    )
    query = parse_qs(urlsplit(seen[0]).query)
    assert query["client_id"] == [ISSUED_CLIENT]
    assert query["ext_agent_host_id"] == [host]
    assert store.state()[0] == "SIGNED_IN"


@pytest.mark.parametrize(
    ("callback", "category"),
    [
        (lambda q: {"code": CODE, "state": "forged", "client_id": ISSUED_CLIENT}, "STATE_MISMATCH"),
        (lambda q: {"error": "access_denied", "state": q["state"][0]}, "ACCESS_DENIED"),
        (lambda q: {"code": CODE, "state": q["state"][0]}, "NO_CLIENT_ID"),
    ],
    ids=["forged-state", "denied", "no-issued-client"],
)
def test_a_bad_callback_saves_nothing_and_exchanges_nothing(
    tmp_path: Path, callback: Callable[[Any], dict[str, str]], category: str
) -> None:
    server = FakeAuthServer()
    with pytest.raises(SignInFailed) as caught:
        _sign_in(tmp_path, server, callback=callback)
    assert caught.value.category == category
    assert server.token_forms() == []
    assert _store(tmp_path).state()[0] == "NOT_CONFIGURED"


def test_a_reauthorization_that_names_another_client_is_refused(tmp_path: Path) -> None:
    server = FakeAuthServer()
    _sign_in(tmp_path, server)
    with pytest.raises(SignInFailed) as caught:
        _sign_in(
            tmp_path,
            server,
            callback=lambda q: {"code": CODE, "state": q["state"][0], "client_id": "oaiapp_other"},
        )
    assert caught.value.category == "CLIENT_ID_MISMATCH"


def test_no_callback_ends_the_attempt(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def run() -> None:
        auth = AuthClient(FakeAuthServer().transport())
        try:
            await sign_in(
                store,
                auth=auth,
                open_browser=lambda url: True,
                timeout_seconds=0.3,
                preferred_port=0,
            )
        finally:
            await auth.aclose()

    with pytest.raises(SignInFailed) as caught:
        asyncio.run(run())
    assert caught.value.category == "TIMEOUT"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "oaiapp_someone_else"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 600},
        {"nonce": "replayed"},
        {"_key": _OTHER_KEY},
    ],
    ids=["audience", "issuer", "expired", "nonce", "signature"],
)
def test_an_id_token_that_fails_validation_saves_nothing(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    server = FakeAuthServer()
    server.id_token_overrides = overrides
    with pytest.raises(SignInFailed) as caught:
        _sign_in(tmp_path, server)
    assert caught.value.category == "ID_TOKEN_INVALID"
    assert _store(tmp_path).state()[0] == "NOT_CONFIGURED"


def test_an_unsigned_id_token_is_refused(tmp_path: Path) -> None:
    server = FakeAuthServer()
    original = server.handle

    def unsigned(request: httpx2.Request) -> httpx2.Response:
        response = original(request)
        if str(request.url) == TOKEN_URL and response.status_code == 200:
            body = json.loads(response.content)
            claims = jwt.decode(body["id_token"], options={"verify_signature": False})
            body["id_token"] = jwt.encode(claims, key=None, algorithm="none")  # type: ignore[arg-type]
            return httpx2.Response(200, json=body)
        return response

    server.handle = unsigned  # type: ignore[method-assign]
    with pytest.raises(SignInFailed) as caught:
        _sign_in(tmp_path, server)
    assert caught.value.category == "ID_TOKEN_INVALID"


def test_a_sign_in_without_plan_usage_saves_nothing(tmp_path: Path) -> None:
    server = FakeAuthServer()
    server.token_scope = "openid profile email offline_access"
    with pytest.raises(SignInFailed) as caught:
        _sign_in(tmp_path, server)
    assert caught.value.category == "PLAN_USAGE_DECLINED"
    assert _store(tmp_path).state()[0] == "NOT_CONFIGURED"


def test_discovery_that_points_off_the_auth_host_is_refused(tmp_path: Path) -> None:
    server = FakeAuthServer()
    server.discovery["jwks_uri"] = "https://keys.example.com/jwks.json"
    with pytest.raises(SignInFailed) as caught:
        _sign_in(tmp_path, server)
    assert caught.value.category == "ID_TOKEN_INVALID"


def test_no_secret_appears_in_a_failure(tmp_path: Path) -> None:
    server = FakeAuthServer()
    server.id_token_overrides = {"aud": "other"}
    with pytest.raises(SignInFailed) as caught:
        _sign_in(tmp_path, server)
    shown = str(caught.value) + repr(caught.value)
    for secret in (CODE, ACCESS, REFRESH):
        assert secret not in shown
    assert caught.value.__cause__ is None


# --- the listener ----------------------------------------------------------------------------


def test_the_listener_binds_loopback_only_and_waits_for_the_exact_path() -> None:
    listener = CallbackListener(preferred_port=0)
    redirect = listener.start()
    try:
        assert listener.bound_host == "127.0.0.1"
        assert redirect == f"http://127.0.0.1:{listener.port}/auth/callback"
        with pytest.raises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{listener.port}/callback?code=x", timeout=5)
        urllib.request.urlopen(redirect + "?code=abc&state=s", timeout=5).read()
        assert listener.wait(5.0) == {"code": "abc", "state": "s"}
    finally:
        listener.close()


def test_a_busy_preferred_port_falls_back_to_a_free_one() -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    listener = CallbackListener(preferred_port=busy)
    try:
        listener.start()
        assert listener.port != busy
    finally:
        listener.close()
        blocker.close()


# --- refresh and revocation ------------------------------------------------------------------


def test_refresh_returns_the_rotated_pair() -> None:
    server = FakeAuthServer()

    async def run() -> Any:
        auth = AuthClient(server.transport())
        try:
            return await auth.refresh(ISSUED_CLIENT, REFRESH)
        finally:
            await auth.aclose()

    tokens = asyncio.run(run())
    assert tokens.access_token == "access-REFRESHED"
    assert tokens.refresh_token == "refresh-ROTATED"
    assert PLAN_USAGE_SCOPE in tokens.scopes
    (form,) = server.token_forms()
    assert form == {
        "grant_type": ["refresh_token"],
        "client_id": [ISSUED_CLIENT],
        "refresh_token": [REFRESH],
        "resource": ["https://api.openai.com/v1"],
    }


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, {"error": "invalid_grant"}, RefreshRejected),
        (401, {"error": "refresh_token_reused"}, RefreshRejected),
        (400, {"error": "invalid_client"}, RefreshRejected),
        (503, {}, InferenceConnectionError),
    ],
    ids=["invalid-grant", "reused", "invalid-client", "server-down"],
)
def test_refresh_failures_say_whether_a_new_sign_in_is_needed(
    status: int, body: dict[str, Any], expected: type[BaseException]
) -> None:
    server = FakeAuthServer()
    server.refresh_status, server.refresh_body = status, body

    async def run() -> Any:
        auth = AuthClient(server.transport())
        try:
            return await auth.refresh(ISSUED_CLIENT, REFRESH)
        finally:
            await auth.aclose()

    with pytest.raises(expected) as caught:
        asyncio.run(run())
    assert REFRESH not in str(caught.value)


def test_revocation_posts_the_refresh_token_to_the_discovered_endpoint() -> None:
    server = FakeAuthServer()

    async def run() -> bool:
        auth = AuthClient(server.transport())
        try:
            return await auth.revoke(ISSUED_CLIENT, REFRESH)
        finally:
            await auth.aclose()

    assert asyncio.run(run()) is True
    (request,) = [r for r in server.requests if str(r.url) == REVOCATION_URI]
    assert parse_qs(request.content.decode()) == {
        "token": [REFRESH],
        "token_type_hint": ["refresh_token"],
        "client_id": [ISSUED_CLIENT],
    }


def test_redirects_are_never_followed() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://evil.example/"})

    async def run() -> None:
        auth = AuthClient(httpx2.MockTransport(handler))
        try:
            await auth.refresh(ISSUED_CLIENT, REFRESH)
        finally:
            await auth.aclose()

    with pytest.raises((RefreshRejected, InferenceConnectionError)):
        asyncio.run(run())


# --- the CLI ---------------------------------------------------------------------------------


def _cli(tmp_path: Path, argv: list[str], server: FakeAuthServer) -> tuple[int, str]:
    from aaos.inference.chatgpt_plan_cli import run_chatgpt_plan_command

    lines: list[str] = []
    code = run_chatgpt_plan_command(
        argv,
        environment={"AAOS_CHATGPT_PLAN_CREDENTIAL_PATH": str(tmp_path / "plan.json")},
        out=lines.append,
        auth_factory=lambda: AuthClient(server.transport()),
        open_browser=_browser(server),
        timeout_seconds=10.0,
        preferred_port=0,
    )
    return code, "\n".join(lines)


def test_the_cli_signs_in_reports_status_and_signs_out_without_showing_a_token(
    tmp_path: Path,
) -> None:
    server = FakeAuthServer()
    code, output = _cli(tmp_path, ["status"], server)
    assert code == 0 and "NOT_CONFIGURED" in output and "sign-in" in output
    assert server.requests == []  # status never contacts OpenAI

    code, output = _cli(tmp_path, ["sign-in"], server)
    assert code == 0
    assert "SIGNED_IN" in output and "chatgpt.com/settings/usage" in output
    code, status_output = _cli(tmp_path, ["status"], server)
    assert "SIGNED_IN" in status_output

    code, signed_out = _cli(tmp_path, ["sign-out"], server)
    assert code == 0 and "revoked" in signed_out
    assert _store(tmp_path).state()[0] == "SIGN_IN_REQUIRED"
    for text in (output, status_output, signed_out):
        for secret in (ACCESS, REFRESH, CODE):
            assert secret not in text


def test_a_failed_cli_sign_in_says_why_in_plain_words(tmp_path: Path) -> None:
    server = FakeAuthServer()
    server.token_scope = "openid profile email offline_access"
    code, output = _cli(tmp_path, ["sign-in"], server)
    assert code == 1
    assert "PLAN_USAGE_DECLINED" in output and "nothing was saved" in output


def test_the_cli_refuses_unknown_commands_and_in_repository_paths(tmp_path: Path) -> None:
    from aaos.inference.chatgpt_plan_cli import run_chatgpt_plan_command

    lines: list[str] = []
    assert run_chatgpt_plan_command(["login"], environment={}, out=lines.append) == 2
    repository = Path(__file__).resolve().parents[1] / "data" / "credentials" / "plan.json"
    assert (
        run_chatgpt_plan_command(
            ["status"],
            environment={"AAOS_CHATGPT_PLAN_CREDENTIAL_PATH": str(repository)},
            out=lines.append,
        )
        == 1
    )
    assert "outside the AAOS repository" in lines[-1]
