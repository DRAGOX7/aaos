"""Sign in with ChatGPT for plan usage (Phase 11, slice 11B).

Only the owner starts this, from the terminal (``aaos model chatgpt-plan sign-in``).
Guard rails:

* every host is a code constant (auth.openai.com, api.openai.com/v1); HTTPS only, no
  redirects and no proxies from the environment;
* each attempt has a fresh PKCE verifier (S256), ``state`` and ``nonce``;
* the callback listener binds 127.0.0.1 only, accepts one request on the exact path,
  logs nothing (the query carries the code), and gives up after a timeout;
* the code is exchanged with the client id OpenAI issued, never ``dynamic_agent_client``;
* the ID token is checked (signature against OpenAI's published keys, issuer, audience,
  expiry, nonce) and the plan-usage scope is required before anything is saved;
* failures carry a safe category only: no code, token or server text.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx2
import jwt

from .chatgpt_plan_credentials import (
    ChatGPTCredentialStore,
    RefreshRejected,
    TokenSet,
    token_set_from_response,
)
from .gateway import InferenceConnectionError

AUTH_ISSUER = "https://auth.openai.com"
AUTH_HOST = "auth.openai.com"
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
API_RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
PREFERRED_PORT = 1455
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "AAOS"

# Asymmetric signatures only: never "none" and never a shared-secret (HS*) algorithm.
_ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
_CALLBACK_KEYS = ("code", "state", "client_id", "scope", "error")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:|@-]{0,199}$")
_ERROR_WORD = re.compile(r"^[a-z_]{1,60}$")


class SignInFailed(Exception):
    """A sign-in that did not complete; ``category`` is the only detail kept."""

    def __init__(self, category: str) -> None:
        super().__init__(f"ChatGPT sign-in failed: {category}")
        self.category = category


@dataclass(frozen=True, slots=True)
class Pkce:
    """One attempt's PKCE verifier and challenge, with its ``state`` and ``nonce``."""

    verifier: str = field(repr=False)
    challenge: str
    state: str = field(repr=False)
    nonce: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SignInResult:
    """What a completed sign-in granted (no token values)."""

    scopes: tuple[str, ...]
    expires_at: float


def make_pkce() -> Pkce:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return Pkce(verifier, challenge, secrets.token_urlsafe(32), secrets.token_urlsafe(32))


def authorize_url(*, client_id: str | None, host_id: str, redirect_uri: str, pkce: Pkce) -> str:
    """The URL the owner's browser opens; the first sign-in registers this install."""
    params: dict[str, str] = {
        "client_id": client_id or DYNAMIC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": API_RESOURCE,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        "state": pkce.state,
        "nonce": pkce.nonce,
        "ext_agent_host_id": host_id,
    }
    if client_id is None:
        params["agent_name_hint"] = AGENT_NAME
    return f"{AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"


def _on_auth_host(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == AUTH_HOST
        and parts.port is None
        and parts.username is None
        and parts.password is None
    )


# --- the local callback listener -------------------------------------------------------------


class _OneShotServer(http.server.HTTPServer):
    result: dict[str, str] | None = None
    received: threading.Event


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _OneShotServer

    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        parts = urlsplit(self.path)
        if parts.path != CALLBACK_PATH or self.server.received.is_set():
            self.send_response(404)
            self.end_headers()
            return
        query = parse_qs(parts.query, keep_blank_values=False)
        self.server.result = {
            key: values[0] for key, values in query.items() if key in _CALLBACK_KEYS and values
        }
        self.server.received.set()
        body = (
            b"<!doctype html><title>AAOS</title><p>AAOS received the sign-in. "
            b"You can close this tab and return to the terminal.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # the request line carries the authorization code: never print it


class CallbackListener:
    """Wait on 127.0.0.1 for exactly one browser callback on ``/auth/callback``."""

    bound_host = "127.0.0.1"

    def __init__(self, preferred_port: int = PREFERRED_PORT) -> None:
        self._preferred_port = preferred_port
        self._server: _OneShotServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("listener not started")
        return int(self._server.server_address[1])

    def start(self) -> str:
        """Bind, preferring the documented port and falling back to a free one."""
        server: _OneShotServer | None = None
        for port in dict.fromkeys((self._preferred_port, 0)):
            try:
                server = _OneShotServer((self.bound_host, port), _CallbackHandler)
                break
            except OSError:
                continue
        if server is None:
            raise SignInFailed("LISTENER_UNAVAILABLE")
        server.received = threading.Event()
        server.timeout = 0.2
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{self.bound_host}:{self.port}{CALLBACK_PATH}"

    def wait(self, timeout_seconds: float) -> dict[str, str] | None:
        if self._server is None:
            raise RuntimeError("listener not started")
        if not self._server.received.wait(timeout_seconds):
            return None
        return dict(self._server.result or {})

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


# --- talking to auth.openai.com --------------------------------------------------------------


class AuthClient:
    """The token, key and revocation calls, all on the fixed auth host."""

    def __init__(
        self, transport: httpx2.AsyncBaseTransport | None = None, *, timeout: float = 15.0
    ) -> None:
        self._client = httpx2.AsyncClient(
            transport=transport or httpx2.AsyncHTTPTransport(),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str) -> Any:
        response = await self._client.get(url)
        if not response.is_success:
            raise ValueError("auth server request failed")
        return response.json()

    async def _discovery_document(self) -> dict[str, Any]:
        if self._discovery is None:
            document = await self._get_json(DISCOVERY_URL)
            if (
                not isinstance(document, dict)
                or document.get("issuer") != AUTH_ISSUER
                or not _on_auth_host(document.get("jwks_uri"))
            ):
                raise ValueError("unexpected discovery document")
            revocation = document.get("revocation_endpoint")
            if revocation is not None and not _on_auth_host(revocation):
                raise ValueError("unexpected revocation endpoint")
            self._discovery = document
        return self._discovery

    async def _signing_keys(self, *, reload: bool = False) -> jwt.PyJWKSet:
        if self._jwks is None or reload:
            discovery = await self._discovery_document()
            self._jwks = await self._get_json(str(discovery["jwks_uri"]))
        return jwt.PyJWKSet.from_dict(self._jwks)

    async def exchange_code(
        self, *, client_id: str, code: str, verifier: str, redirect_uri: str
    ) -> dict[str, Any]:
        form = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": API_RESOURCE,
        }
        try:
            response = await self._client.post(TOKEN_URL, data=form)
        except httpx2.HTTPError:
            raise SignInFailed("NETWORK") from None
        if not response.is_success:
            raise SignInFailed("EXCHANGE_FAILED")
        try:
            payload = response.json()
        except ValueError:
            raise SignInFailed("EXCHANGE_FAILED") from None
        if not isinstance(payload, dict):
            raise SignInFailed("EXCHANGE_FAILED")
        return payload

    async def validate_id_token(self, id_token: object, *, client_id: str, nonce: str) -> str:
        """Check the ID token fully and return its subject (the account identity)."""
        try:
            if not isinstance(id_token, str) or not id_token:
                raise ValueError("no id token")
            header = jwt.get_unverified_header(id_token)
            algorithm = header.get("alg")
            if algorithm not in _ALLOWED_ALGORITHMS:
                raise ValueError("algorithm not allowed")
            key = _find_key(await self._signing_keys(), header.get("kid"))
            if key is None:  # keys rotate: fetch them once more for an unfamiliar kid
                key = _find_key(await self._signing_keys(reload=True), header.get("kid"))
            if key is None:
                raise ValueError("unknown signing key")
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=[algorithm],
                audience=client_id,
                issuer=AUTH_ISSUER,
                leeway=5,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
            claimed_nonce = claims.get("nonce")
            if not isinstance(claimed_nonce, str) or not secrets.compare_digest(
                claimed_nonce, nonce
            ):
                raise ValueError("nonce mismatch")
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError("no subject")
            return subject
        except Exception:
            raise SignInFailed("ID_TOKEN_INVALID") from None

    async def refresh(self, client_id: str, refresh_token: str) -> TokenSet:
        """Exchange the single-use refresh token for a new pair (the store's refresher)."""
        form = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": API_RESOURCE,
        }
        try:
            response = await self._client.post(TOKEN_URL, data=form)
        except httpx2.HTTPError:
            raise InferenceConnectionError("ChatGPT sign-in server could not be reached") from None
        if response.status_code in {400, 401, 403}:
            raise RefreshRejected(f"refresh refused: {_error_word(response)}")
        if not response.is_success:
            raise InferenceConnectionError(
                f"ChatGPT sign-in server failed (status {response.status_code})"
            )
        try:
            return token_set_from_response(response.json(), now=time.time())
        except ValueError:
            raise InferenceConnectionError("ChatGPT sign-in server sent an odd reply") from None

    async def revoke(self, client_id: str, refresh_token: str) -> bool:
        """Revoke the refresh token at the discovered endpoint; True when it worked."""
        try:
            endpoint = (await self._discovery_document()).get("revocation_endpoint")
            if not _on_auth_host(endpoint):
                return False
            response = await self._client.post(
                str(endpoint),
                data={
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": client_id,
                },
            )
        except (httpx2.HTTPError, ValueError):
            return False
        return response.is_success


def _find_key(keyset: jwt.PyJWKSet, kid: object) -> jwt.PyJWK | None:
    keys = list(keyset.keys)
    if kid is None:
        return keys[0] if len(keys) == 1 else None
    return next((key for key in keys if key.key_id == kid), None)


def _error_word(response: httpx2.Response) -> str:
    try:
        word = response.json().get("error")
    except (ValueError, AttributeError):
        return f"http_{response.status_code}"
    return word if isinstance(word, str) and _ERROR_WORD.match(word) else "unknown"


# --- the flow --------------------------------------------------------------------------------


async def sign_in(
    store: ChatGPTCredentialStore,
    *,
    auth: AuthClient,
    open_browser: Callable[[str], object],
    timeout_seconds: float = 300.0,
    preferred_port: int = PREFERRED_PORT,
    now: Callable[[], float] = time.time,
) -> SignInResult:
    """Run one owner-started sign-in and save it through ``store`` when it is valid."""
    host_id = store.host_id()
    known_client = store.client_id()
    pkce = make_pkce()
    listener = CallbackListener(preferred_port)
    redirect_uri = listener.start()
    try:
        open_browser(
            authorize_url(
                client_id=known_client, host_id=host_id, redirect_uri=redirect_uri, pkce=pkce
            )
        )
        params = await asyncio.to_thread(listener.wait, timeout_seconds)
    finally:
        listener.close()
    if params is None:
        raise SignInFailed("TIMEOUT")
    if not secrets.compare_digest(params.get("state", ""), pkce.state):
        raise SignInFailed("STATE_MISMATCH")
    if params.get("error"):
        raise SignInFailed(
            "ACCESS_DENIED" if params["error"] == "access_denied" else "AUTHORIZATION_FAILED"
        )
    code = params.get("code")
    if not code:
        raise SignInFailed("AUTHORIZATION_FAILED")
    returned_client = params.get("client_id")
    if known_client is None:
        if not returned_client or not _CLIENT_ID.match(returned_client):
            raise SignInFailed("NO_CLIENT_ID")
        client_id = returned_client
    else:
        if returned_client and returned_client != known_client:
            raise SignInFailed("CLIENT_ID_MISMATCH")
        client_id = known_client
    payload = await auth.exchange_code(
        client_id=client_id, code=code, verifier=pkce.verifier, redirect_uri=redirect_uri
    )
    callback_scopes = tuple(sorted(set(params.get("scope", "").split())))
    try:
        tokens = token_set_from_response(payload, now=now(), previous_scopes=callback_scopes)
    except ValueError:
        raise SignInFailed("EXCHANGE_FAILED") from None
    subject = await auth.validate_id_token(
        payload.get("id_token"), client_id=client_id, nonce=pkce.nonce
    )
    if not tokens.plan_usage_granted:
        raise SignInFailed("PLAN_USAGE_DECLINED")
    store.save_sign_in(client_id=client_id, account_subject=subject, tokens=tokens)
    return SignInResult(scopes=tokens.scopes, expires_at=tokens.expires_at)
