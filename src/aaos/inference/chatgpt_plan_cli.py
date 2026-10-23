"""``aaos model chatgpt-plan sign-in | status | sign-out`` (Phase 11, slice 11B).

Sign-in is started here and only here: no browser route can start it (spec D1). The
output names states, scopes and times, never a token or the authorization code.
"""

from __future__ import annotations

import asyncio
import os
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from .chatgpt_plan_credentials import (
    ChatGPTCredentialStore,
    ChatGPTPlanUnavailable,
    credential_path_from_environment,
)
from .chatgpt_plan_signin import PREFERRED_PORT, AuthClient, SignInFailed, SignInResult, sign_in

USAGE = "usage: aaos model chatgpt-plan sign-in | status | sign-out"
USAGE_SETTINGS_URL = "https://chatgpt.com/settings/usage"

_FAILURE_HELP = {
    "TIMEOUT": "No answer came back from the browser in time. Run sign-in again.",
    "ACCESS_DENIED": "The sign-in was cancelled in the browser.",
    "STATE_MISMATCH": "The browser answer did not belong to this sign-in. Run sign-in again.",
    "PLAN_USAGE_DECLINED": (
        "You signed in but did not allow AAOS to use your ChatGPT plan, so nothing was saved."
    ),
    "ID_TOKEN_INVALID": "OpenAI's answer could not be verified, so nothing was saved.",
    "CLIENT_ID_MISMATCH": "OpenAI answered for a different app registration; nothing was saved.",
    "NO_CLIENT_ID": "OpenAI did not finish registering AAOS; nothing was saved. Try again.",
    "NETWORK": "AAOS could not reach OpenAI. Check the connection and try again.",
    "EXCHANGE_FAILED": "OpenAI refused to complete the sign-in. Run sign-in again.",
}


def _store(environment: Mapping[str, str]) -> ChatGPTCredentialStore:
    return ChatGPTCredentialStore(credential_path_from_environment(environment))


def _when(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def run_chatgpt_plan_command(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
    auth_factory: Callable[[], AuthClient] = AuthClient,
    open_browser: Callable[[str], object] = webbrowser.open,
    timeout_seconds: float = 300.0,
    preferred_port: int | None = None,
) -> int:
    environment = os.environ if environment is None else environment
    command = list(argv)
    if command not in (["sign-in"], ["status"], ["sign-out"]):
        out(USAGE)
        return 2
    try:
        store = _store(environment)
    except ChatGPTPlanUnavailable:
        out(
            "The credential path must be absolute and outside the AAOS repository "
            "(AAOS_CHATGPT_PLAN_CREDENTIAL_PATH)."
        )
        return 1

    if command == ["status"]:
        state, expires_at = store.state()
        out(f"ChatGPT plan: {state}")
        out(f"Credential file: {store.path}")
        if state == "SIGNED_IN":
            out(f"Access token renews automatically; current one expires {_when(expires_at)}.")
        elif state in {"NOT_CONFIGURED", "SIGN_IN_REQUIRED"}:
            out("Run: aaos model chatgpt-plan sign-in")
        return 0

    if command == ["sign-out"]:
        tokens = store.sign_out()
        client_id = store.client_id()
        if tokens is None:
            out("ChatGPT plan: no sign-in to remove.")
            return 0
        revoked = False
        if client_id is not None:
            revoked = asyncio.run(_revoke(auth_factory, client_id, tokens.refresh_token))
        out("ChatGPT plan: signed out; the tokens were removed from this computer.")
        out(
            "OpenAI confirmed the sign-in is revoked."
            if revoked
            else f"OpenAI could not confirm the revocation. You can also revoke AAOS at "
            f"{USAGE_SETTINGS_URL}."
        )
        return 0

    out("Signing in with your ChatGPT plan. Your browser will open an OpenAI page.")
    out(
        "Once signed in, AAOS can send questions and course excerpts to OpenAI when you "
        "choose the ChatGPT plan model."
    )

    def opener(url: str) -> object:
        out("If the browser does not open, paste this address into it:")
        out(url)
        return open_browser(url)

    try:
        result = asyncio.run(_sign_in(store, auth_factory, opener, timeout_seconds, preferred_port))
    except SignInFailed as failure:
        out(f"Sign-in failed: {failure.category}")
        out(_FAILURE_HELP.get(failure.category, "Run sign-in again."))
        return 1
    except ChatGPTPlanUnavailable as failure:
        out(f"Sign-in could not be saved: {failure.category}")
        return 1
    out("ChatGPT plan: SIGNED_IN")
    out(f"Granted: {' '.join(result.scopes)}")
    out(
        f"Tip: set a weekly limit for AAOS at {USAGE_SETTINGS_URL} so it cannot use "
        "more of your plan than you want."
    )
    return 0


async def _sign_in(
    store: ChatGPTCredentialStore,
    auth_factory: Callable[[], AuthClient],
    opener: Callable[[str], object],
    timeout_seconds: float,
    preferred_port: int | None,
) -> SignInResult:
    auth = auth_factory()
    try:
        return await sign_in(
            store,
            auth=auth,
            open_browser=opener,
            timeout_seconds=timeout_seconds,
            preferred_port=PREFERRED_PORT if preferred_port is None else preferred_port,
        )
    finally:
        await auth.aclose()


async def _revoke(auth_factory: Callable[[], AuthClient], client_id: str, token: str) -> bool:
    auth = auth_factory()
    try:
        return await auth.revoke(client_id, token)
    finally:
        await auth.aclose()
