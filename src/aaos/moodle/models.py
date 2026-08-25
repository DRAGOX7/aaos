"""Non-secret Moodle settings and capability contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import urlsplit

MoodleMode: TypeAlias = Literal["api", "browser"]
EntityType: TypeAlias = Literal["course", "assignment", "announcement", "remote_file_metadata"]
ApiOperation: TypeAlias = Literal["courses", "assignments", "announcements", "remote_files"]
BrowserRoute: TypeAlias = Literal[
    "course_index", "course_detail", "announcement_list", "resource_metadata"
]
PaginationStrategy: TypeAlias = Literal["offset_total", "cursor_exhaustion", "page_link_exhaustion"]
IdentityStrategy: TypeAlias = Literal["provider_id", "parent_scoped_provider_id"]
TimestampStrategy: TypeAlias = Literal["unix_utc", "iso_offset", "portal_timezone"]
RedirectPolicy: TypeAlias = Literal["disabled", "approved_same_origin"]

_ENTITY_TYPES = frozenset({"course", "assignment", "announcement", "remote_file_metadata"})
_API_OPERATIONS = frozenset({"courses", "assignments", "announcements", "remote_files"})
_BROWSER_ROUTES = frozenset(
    {"course_index", "course_detail", "announcement_list", "resource_metadata"}
)
_PAGINATION_STRATEGIES = frozenset({"offset_total", "cursor_exhaustion", "page_link_exhaustion"})
_IDENTITY_STRATEGIES = frozenset({"provider_id", "parent_scoped_provider_id"})
_TIMESTAMP_STRATEGIES = frozenset({"unix_utc", "iso_offset", "portal_timezone"})
_REDIRECT_POLICIES = frozenset({"disabled", "approved_same_origin"})


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError(f"{label} must be non-blank safe text")
    return normalized


def _origin(value: object) -> str:
    normalized = _text(value, "Moodle base origin").rstrip("/")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Moodle base origin must be a valid HTTPS origin") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        and ":" in parsed.netloc.rsplit("@", 1)[-1]
    ):
        raise ValueError("Moodle base origin must be a valid HTTPS origin")
    return normalized


def _alias(value: object) -> str:
    normalized = _text(value, "Moodle auth alias")
    if normalized in {".", ".."} or any(
        not (char.isalnum() or char in {"-", "_", "."}) for char in normalized
    ):
        raise ValueError("Moodle auth alias must be a safe single path component")
    return normalized


@dataclass(frozen=True, slots=True)
class MoodleSettings:
    source_system: str
    base_origin: str
    mode: MoodleMode
    auth_alias: str

    def __post_init__(self) -> None:
        source = _text(self.source_system, "Moodle source namespace").lower()
        if any(char.isspace() for char in source):
            raise ValueError("Moodle source namespace must not contain whitespace")
        if self.mode not in {"api", "browser"}:
            raise ValueError("Moodle mode must be api or browser")
        object.__setattr__(self, "source_system", source)
        object.__setattr__(self, "base_origin", _origin(self.base_origin))
        object.__setattr__(self, "auth_alias", _alias(self.auth_alias))


@dataclass(frozen=True, slots=True)
class MoodleCapability:
    source_system: str
    base_origin: str
    mode: MoodleMode
    complete_scope: str
    entity_scope: tuple[EntityType, ...]
    approved_api_operations: tuple[ApiOperation, ...]
    approved_browser_routes: tuple[BrowserRoute, ...]
    pagination_strategy: PaginationStrategy
    identity_strategy: IdentityStrategy
    timestamp_strategy: TimestampStrategy
    redirect_policy: RedirectPolicy
    redirect_origins: tuple[str, ...]
    required_version: str | None
    evidence_notes: str


@dataclass(frozen=True, slots=True)
class AuthPaths:
    api_token_path: Path
    browser_state_dir: Path


def resolve_auth_paths(settings: MoodleSettings, data_root: str | Path = Path("data")) -> AuthPaths:
    root = Path(data_root).expanduser().resolve()
    alias = _alias(settings.auth_alias)
    return AuthPaths(
        api_token_path=root / "credentials" / "moodle" / alias / "api_token",
        browser_state_dir=root / "auth" / "moodle" / alias,
    )


def validate_capability(settings: MoodleSettings, capability: MoodleCapability) -> None:
    if capability.source_system != settings.source_system:
        raise ValueError("Moodle capability source namespace does not match settings")
    if capability.base_origin != settings.base_origin:
        raise ValueError("Moodle capability origin does not match settings")
    if capability.mode != settings.mode:
        raise ValueError("Moodle capability mode does not match settings")
    if not _text(capability.complete_scope, "Moodle complete scope"):
        raise ValueError("Moodle complete scope must be non-blank")
    if set(capability.entity_scope) != _ENTITY_TYPES or len(capability.entity_scope) != len(
        _ENTITY_TYPES
    ):
        raise ValueError("Moodle capability must cover all four entity types exactly once")
    if any(operation not in _API_OPERATIONS for operation in capability.approved_api_operations):
        raise ValueError("Moodle capability contains an unsupported API operation")
    if len(set(capability.approved_api_operations)) != len(capability.approved_api_operations):
        raise ValueError("Moodle capability repeats an API operation")
    if any(route not in _BROWSER_ROUTES for route in capability.approved_browser_routes):
        raise ValueError("Moodle capability contains an unsupported browser route")
    if len(set(capability.approved_browser_routes)) != len(capability.approved_browser_routes):
        raise ValueError("Moodle capability repeats a browser route")
    if capability.mode == "api" and set(capability.approved_api_operations) != _API_OPERATIONS:
        raise ValueError("API capability must approve all required read operations")
    if capability.mode == "browser" and not capability.approved_browser_routes:
        raise ValueError("browser capability must approve at least one route")
    if capability.pagination_strategy not in _PAGINATION_STRATEGIES:
        raise ValueError("Moodle capability has an unsupported pagination strategy")
    if capability.identity_strategy not in _IDENTITY_STRATEGIES:
        raise ValueError("Moodle capability has an unsupported identity strategy")
    if capability.timestamp_strategy not in _TIMESTAMP_STRATEGIES:
        raise ValueError("Moodle capability has an unsupported timestamp strategy")
    if capability.redirect_policy not in _REDIRECT_POLICIES:
        raise ValueError("Moodle capability has an unsupported redirect policy")
    if capability.redirect_policy == "disabled" and capability.redirect_origins:
        raise ValueError("disabled redirect policy cannot approve redirect origins")
    if capability.redirect_policy == "approved_same_origin" and set(
        capability.redirect_origins
    ) != {settings.base_origin}:
        raise ValueError("approved redirect policy must bind the configured origin")
    if any(
        term in capability.evidence_notes.lower()
        for term in ("password", "token", "cookie", "session")
    ):
        raise ValueError("Moodle capability evidence must not contain secret-state terms")
