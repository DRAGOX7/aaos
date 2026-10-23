"""Owner-only storage for the ChatGPT-plan sign-in (Phase 11, slice 11A).

The only code that reads, writes or refreshes the ChatGPT-plan tokens. Guard rails:

* the file lives outside the repository (by default under %LOCALAPPDATA%\\AAOS), and a
  relative or in-repository path is refused;
* it is written atomically and readable only by its owner (a protected Windows access
  list naming only OWNER RIGHTS, or mode 0600); a file others can read is refused;
* refresh tokens are single-use, so every refresh runs under one lock file shared by
  every AAOS process, rereads the file first, and persists the rotated pair at once;
* a refresh the server rejects marks the sign-in as needing a new one, and is not
  retried; a network failure keeps the sign-in;
* no token appears in a repr, a str, an error message or an exception chain.

The ID token, the account email and the account name are never stored.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from .gateway import ModelUnavailableError

CHATGPT_PLAN_CREDENTIAL_PATH_ENVIRONMENT_KEY = "AAOS_CHATGPT_PLAN_CREDENTIAL_PATH"
PLAN_USAGE_SCOPE = "chatgpt.tokens.use.direct"

StoreState = Literal["NOT_CONFIGURED", "SIGNED_IN", "SIGN_IN_REQUIRED", "PLAN_USAGE_NOT_GRANTED"]

_FORMAT_VERSION = 1
_MAX_FILE_BYTES = 65536
_REFRESH_MARGIN_SECONDS = 120.0
_LOCK_WAIT_SECONDS = 20.0
_LOCK_STALE_SECONDS = 60.0
_HOST_ID = re.compile(
    r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:|@-]{0,199}$")
_SCOPE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
# The only access list AAOS writes on Windows: protected (no inherited entries), with
# one entry granting full access to whoever owns the file.
_OWNER_ONLY_SDDL = "D:P(A;;FA;;;OW)"


class ChatGPTPlanUnavailable(ModelUnavailableError):
    """The ChatGPT plan cannot answer; ``category`` says why, in safe words only."""

    def __init__(self, category: StoreState | str) -> None:
        super().__init__(f"ChatGPT plan unavailable: {category}")
        self.category = category


class RefreshRejected(Exception):
    """The token server refused a refresh for good: a new sign-in is needed."""


@dataclass(frozen=True, slots=True)
class TokenSet:
    """One access/refresh pair with its expiry and granted scopes."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.access_token, self.refresh_token):
            if not isinstance(value, str) or not value.strip() or len(value) > 16384:
                raise ValueError("tokens must be non-blank strings")
        if (
            isinstance(self.expires_at, bool)
            or not isinstance(self.expires_at, (int, float))
            or self.expires_at <= 0
        ):
            raise ValueError("expires_at must be a positive time")
        if not isinstance(self.scopes, tuple) or not all(
            isinstance(scope, str) and _SCOPE.match(scope) for scope in self.scopes
        ):
            raise ValueError("scopes must be safe scope names")

    def __str__(self) -> str:
        return f"TokenSet(expires_at={self.expires_at}, scopes={' '.join(self.scopes)})"

    @property
    def plan_usage_granted(self) -> bool:
        return PLAN_USAGE_SCOPE in self.scopes


@dataclass(frozen=True, slots=True)
class _Record:
    host_id: str
    client_id: str | None = None
    account_subject: str | None = field(default=None, repr=False)
    tokens: TokenSet | None = None
    needs_sign_in: bool = False
    refreshed_at: float | None = None


Refresher = Callable[[str, str], Awaitable[TokenSet]]


def credential_path_from_environment(environment: Mapping[str, str] | None = None) -> Path:
    """Where the sign-in is kept: the override, else the per-user local app data folder."""
    environment = os.environ if environment is None else environment
    override = environment.get(CHATGPT_PLAN_CREDENTIAL_PATH_ENVIRONMENT_KEY, "").strip()
    if override:
        return Path(override)
    base = environment.get("LOCALAPPDATA", "").strip()
    if base:
        return Path(base) / "AAOS" / "credentials" / "chatgpt-plan.json"
    return Path.home() / ".config" / "aaos" / "credentials" / "chatgpt-plan.json"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _checked_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ChatGPTPlanUnavailable("NOT_CONFIGURED")
    resolved = candidate.resolve()
    if resolved.is_relative_to(_repository_root()) or not resolved.name:
        raise ChatGPTPlanUnavailable("NOT_CONFIGURED")
    return resolved


# --- owner-only files ------------------------------------------------------------------------


def _restrict_to_owner(path: Path) -> None:
    if sys.platform == "win32":
        _set_windows_dacl(path, _OWNER_ONLY_SDDL)
    else:
        os.chmod(path, 0o600)


def is_owner_only(path: Path) -> bool:
    """True when only the file's owner can read it (checked, never assumed)."""
    try:
        if sys.platform == "win32":
            return _windows_dacl(path) == _OWNER_ONLY_SDDL
        return (path.stat().st_mode & 0o077) == 0
    except OSError:
        return False


def _set_windows_dacl(path: Path, sddl: str) -> None:
    import ctypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        ctypes.c_wchar_p(sddl), 1, ctypes.byref(descriptor), None
    ):
        raise OSError("could not build the owner-only access list")
    try:
        if not advapi.SetFileSecurityW(ctypes.c_wchar_p(str(path)), 4, descriptor):
            raise OSError("could not restrict the credential file")
    finally:
        kernel.LocalFree(descriptor)


def _windows_dacl(path: Path) -> str | None:
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    needed = wintypes.DWORD()
    advapi.GetFileSecurityW(ctypes.c_wchar_p(str(path)), 4, None, 0, ctypes.byref(needed))
    if needed.value == 0:
        return None
    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi.GetFileSecurityW(
        ctypes.c_wchar_p(str(path)), 4, buffer, needed, ctypes.byref(needed)
    ):
        return None
    text = ctypes.c_wchar_p()
    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        buffer, 1, 4, ctypes.byref(text), None
    ):
        return None
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


# --- the store -------------------------------------------------------------------------------


class ChatGPTCredentialStore:
    """Load, save and refresh the ChatGPT-plan sign-in for every AAOS process."""

    def __init__(
        self,
        path: Path,
        *,
        refresher: Refresher | None = None,
        clock: Callable[[], float] = time.time,
        lock_poll_seconds: float = 0.1,
    ) -> None:
        self._path = _checked_path(path)
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._refresher = refresher
        self._clock = clock
        self._lock_poll = lock_poll_seconds

    def __repr__(self) -> str:
        return f"ChatGPTCredentialStore(path={str(self._path)!r})"

    __str__ = __repr__

    @property
    def path(self) -> Path:
        return self._path

    def set_refresher(self, refresher: Refresher) -> None:
        self._refresher = refresher

    # --- reading ---------------------------------------------------------------------------

    def _load(self) -> _Record | None:
        """The record on disk; None when there is none. Raises when it cannot be trusted."""
        if not self._path.exists():
            return None
        try:
            if not self._path.is_file() or self._path.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError
            if not is_owner_only(self._path):
                raise ValueError
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return _record_from_json(data)
        except Exception:
            raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED") from None

    def state(self) -> tuple[StoreState, float | None]:
        """What the sign-in can do now, and when its access token expires. No network."""
        try:
            record = self._load()
        except ChatGPTPlanUnavailable:
            return "SIGN_IN_REQUIRED", None
        if record is None or (record.client_id is None and record.tokens is None):
            return "NOT_CONFIGURED", None  # never signed in on this install
        if record.tokens is None or record.needs_sign_in or record.client_id is None:
            return "SIGN_IN_REQUIRED", None
        if not record.tokens.plan_usage_granted:
            return "PLAN_USAGE_NOT_GRANTED", record.tokens.expires_at
        return "SIGNED_IN", record.tokens.expires_at

    def host_id(self) -> str:
        """This install's opaque host identifier, made once and then always reused."""
        try:
            record = self._load()
        except ChatGPTPlanUnavailable:
            record = None
        if record is not None:
            return record.host_id
        new = _Record(host_id=f"urn:uuid:{uuid.uuid4()}")
        self._write_locked(new)
        return new.host_id

    def client_id(self) -> str | None:
        """The client id OpenAI issued at the first sign-in (not a secret)."""
        try:
            record = self._load()
        except ChatGPTPlanUnavailable:
            return None
        return record.client_id if record is not None else None

    # --- writing ---------------------------------------------------------------------------

    def save_sign_in(self, *, client_id: str, account_subject: str, tokens: TokenSet) -> None:
        """Store a validated sign-in, keeping this install's host id."""
        if not isinstance(client_id, str) or not _SAFE_IDENTIFIER.match(client_id):
            raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
        if not isinstance(account_subject, str) or not account_subject.strip():
            raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
        host = self.host_id()
        self._write_locked(
            _Record(
                host_id=host,
                client_id=client_id,
                account_subject=account_subject,
                tokens=tokens,
                refreshed_at=self._clock(),
            )
        )

    def sign_out(self) -> TokenSet | None:
        """Forget the tokens (kept: host id and client id). Returns them for revocation."""
        try:
            record = self._load()
        except ChatGPTPlanUnavailable:
            record = None
        if record is None or record.tokens is None:
            return None
        self._write_locked(replace(record, tokens=None, needs_sign_in=False, account_subject=None))
        return record.tokens

    def _write_locked(self, record: _Record) -> None:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            nonce = self._try_lock()
            if nonce is not None:
                break
            if time.monotonic() >= deadline:
                raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
            time.sleep(self._lock_poll)
        try:
            self._write_atomic(_record_to_json(record))
        finally:
            self._unlock(nonce)

    def _write_atomic(self, content: bytes) -> None:
        """Write ``content`` as an owner-only file, replacing the old one in one step."""
        temporary = self._path.with_name(f".{self._path.name}.{secrets.token_hex(8)}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600
            )
            try:
                _restrict_to_owner(temporary)
                os.write(descriptor, content)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self._path)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED") from None

    # --- the shared lock -------------------------------------------------------------------

    def _try_lock(self) -> bytes | None:
        nonce = secrets.token_hex(16).encode("ascii")
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self._break_if_stale()
            return None
        except OSError:
            return None
        try:
            os.write(descriptor, nonce)
        finally:
            os.close(descriptor)
        return nonce

    def _break_if_stale(self) -> None:
        try:
            age = time.time() - self._lock_path.stat().st_mtime
            if age > _LOCK_STALE_SECONDS:
                self._lock_path.unlink()
        except OSError:
            return

    def _unlock(self, nonce: bytes) -> None:
        try:
            if self._lock_path.read_bytes() == nonce:
                self._lock_path.unlink()
        except OSError:
            return

    async def _lock(self) -> bytes:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            nonce = self._try_lock()
            if nonce is not None:
                return nonce
            if time.monotonic() >= deadline:
                raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
            await asyncio.sleep(self._lock_poll)

    # --- access tokens ---------------------------------------------------------------------

    def _usable(self, record: _Record | None) -> tuple[_Record, TokenSet]:
        if record is None or (record.client_id is None and record.tokens is None):
            raise ChatGPTPlanUnavailable("NOT_CONFIGURED")
        if record.tokens is None or record.needs_sign_in or record.client_id is None:
            raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
        if not record.tokens.plan_usage_granted:
            raise ChatGPTPlanUnavailable("PLAN_USAGE_NOT_GRANTED")
        return record, record.tokens

    def _fresh(self, tokens: TokenSet) -> bool:
        return tokens.expires_at - self._clock() > _REFRESH_MARGIN_SECONDS

    async def access_token(self) -> str:
        """A valid access token, refreshed first when it is about to expire."""
        _, tokens = self._usable(self._load())
        if self._fresh(tokens):
            return tokens.access_token
        return await self._refresh(lambda current: not self._fresh(current))

    async def force_refresh(self, *, rejected_access_token: str) -> str:
        """Replace an access token the server refused (unless another caller already did)."""
        return await self._refresh(lambda current: current.access_token == rejected_access_token)

    async def _refresh(self, still_needed: Callable[[TokenSet], bool]) -> str:
        nonce = await self._lock()
        try:
            # Another process or request may have refreshed while this one waited.
            record, tokens = self._usable(self._load())
            if not still_needed(tokens):
                return tokens.access_token
            if self._refresher is None or record.client_id is None:
                raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
            try:
                new_tokens = await self._refresher(record.client_id, tokens.refresh_token)
            except RefreshRejected:
                self._write_atomic(_record_to_json(replace(record, needs_sign_in=True)))
                raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED") from None
            if not isinstance(new_tokens, TokenSet):
                raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED")
            if not new_tokens.scopes:  # a refresh that omits scope keeps the grant
                new_tokens = replace(new_tokens, scopes=tokens.scopes)
            updated = replace(record, tokens=new_tokens, refreshed_at=self._clock())
            self._write_atomic(_record_to_json(updated))
            _, usable = self._usable(updated)
            return usable.access_token
        finally:
            self._unlock(nonce)


# --- the file format -------------------------------------------------------------------------


def token_set_from_response(
    payload: Any, *, now: float, previous_scopes: tuple[str, ...] = ()
) -> TokenSet:
    """Read a token-endpoint response; refuses anything incomplete."""
    if not isinstance(payload, dict):
        raise ValueError("token response is not an object")
    access, refresh = payload.get("access_token"), payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if (
        isinstance(expires_in, bool)
        or not isinstance(expires_in, (int, float))
        or not 0 < expires_in <= 86400 * 30
    ):
        raise ValueError("token response has no usable expiry")
    raw_scope = payload.get("scope")
    if isinstance(raw_scope, str) and raw_scope.strip():
        scopes = tuple(sorted(set(raw_scope.split())))
    else:
        scopes = previous_scopes
    return TokenSet(
        access_token=access if isinstance(access, str) else "",
        refresh_token=refresh if isinstance(refresh, str) else "",
        expires_at=now + float(expires_in),
        scopes=scopes,
    )


def _record_to_json(record: _Record) -> bytes:
    tokens = record.tokens
    data: dict[str, object] = {
        "version": _FORMAT_VERSION,
        "host_id": record.host_id,
        "client_id": record.client_id,
        "account_subject": record.account_subject,
        "needs_sign_in": record.needs_sign_in,
        "refreshed_at": record.refreshed_at,
        "tokens": None
        if tokens is None
        else {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
            "scopes": list(tokens.scopes),
        },
    }
    return json.dumps(data, indent=2).encode("utf-8")


def _record_from_json(data: Any) -> _Record:
    if not isinstance(data, dict) or data.get("version") != _FORMAT_VERSION:
        raise ValueError("unknown credential format")
    host_id = data.get("host_id")
    if not isinstance(host_id, str) or not _HOST_ID.match(host_id):
        raise ValueError("bad host id")
    client_id = data.get("client_id")
    if client_id is not None and (
        not isinstance(client_id, str) or not _SAFE_IDENTIFIER.match(client_id)
    ):
        raise ValueError("bad client id")
    subject = data.get("account_subject")
    if subject is not None and not isinstance(subject, str):
        raise ValueError("bad subject")
    needs_sign_in = data.get("needs_sign_in", False)
    refreshed_at = data.get("refreshed_at")
    if not isinstance(needs_sign_in, bool) or (
        refreshed_at is not None and not isinstance(refreshed_at, (int, float))
    ):
        raise ValueError("bad flags")
    raw_tokens = data.get("tokens")
    tokens: TokenSet | None = None
    if raw_tokens is not None:
        if not isinstance(raw_tokens, dict) or not isinstance(raw_tokens.get("scopes"), list):
            raise ValueError("bad tokens")
        tokens = TokenSet(
            access_token=raw_tokens.get("access_token"),  # type: ignore[arg-type]
            refresh_token=raw_tokens.get("refresh_token"),  # type: ignore[arg-type]
            expires_at=raw_tokens.get("expires_at"),  # type: ignore[arg-type]
            scopes=tuple(raw_tokens["scopes"]),
        )
    return _Record(
        host_id=host_id,
        client_id=client_id,
        account_subject=subject,
        tokens=tokens,
        needs_sign_in=needs_sign_in,
        refreshed_at=refreshed_at,
    )
