"""Human-only provisioning for the confined Moodle Edge profile."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, cast

from aaos.moodle.browser_models import (
    BrowserPolicyError,
    acquire_browser_profile_lock,
    resolve_browser_profile_paths,
)
from aaos.moodle.models import AuthPaths, MoodleCapability, MoodleSettings, validate_capability

BrowserProvisionCode: TypeAlias = Literal[
    "BROWSER_AUTHENTICATED",
    "BROWSER_AUTH_REQUIRED",
    "USER_CANCELLED",
    "SESSION_EXPIRED",
    "BROWSER_PROVISIONING_FAILED",
]

_LOGIN_PATH = "/current/login/index.php"
_COURSE_INDEX_PATH = "/current/my/courses.php"
_INTERACTION_TIMEOUT_MS = 600_000


@dataclass(frozen=True, slots=True)
class BrowserProvisionResult:
    """Fixed, non-sensitive outcome of one human-controlled provisioning window."""

    code: BrowserProvisionCode


class _ProvisionPage(Protocol):
    url: str

    def goto(self, url: str, *, wait_until: str, timeout: int) -> object: ...

    def wait_for_url(self, url: str, *, timeout: int) -> object: ...


class _ProvisionContext(Protocol):
    def new_page(self) -> _ProvisionPage: ...

    def close(self) -> None: ...


PersistentContextFactory: TypeAlias = Callable[..., _ProvisionContext]


def run_configured_browser_auth() -> int:
    """Run only the human-controlled smoke-profile provisioning operation."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        _config, runtime_settings, paths = load_runtime_moodle_configuration()
        settings = replace(
            runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
        )
        result = BrowserSessionProvisioner(
            paths, cast(PersistentContextFactory, launch_msedge_persistent_context)
        ).provision(settings=settings)
    except (OSError, ValueError, TypeError):
        result = BrowserProvisionResult("BROWSER_PROVISIONING_FAILED")
    print(result.code)
    return 0 if result.code == "BROWSER_AUTHENTICATED" else 1


class BrowserSessionProvisioner:
    """Own exactly one headed, dedicated persistent Edge context at a time."""

    def __init__(
        self, paths: AuthPaths, launch_persistent_context: PersistentContextFactory
    ) -> None:
        self._paths = paths
        self._launch_persistent_context = launch_persistent_context

    def provision(
        self, *, settings: MoodleSettings, capability: MoodleCapability | None = None
    ) -> BrowserProvisionResult:
        if settings.mode != "browser":
            return BrowserProvisionResult("BROWSER_PROVISIONING_FAILED")
        if capability is not None:
            try:
                validate_capability(settings, capability)
            except ValueError:
                return BrowserProvisionResult("BROWSER_PROVISIONING_FAILED")
        try:
            profile = resolve_browser_profile_paths(self._paths, settings.base_origin)
            with acquire_browser_profile_lock(profile):
                context = self._launch_persistent_context(
                    channel="msedge",
                    headless=False,
                    user_data_dir=profile.profile_dir,
                    accept_downloads=False,
                    service_workers="block",
                )
                try:
                    page = context.new_page()
                    page.goto(
                        settings.base_origin + _LOGIN_PATH,
                        wait_until="domcontentloaded",
                        timeout=_INTERACTION_TIMEOUT_MS,
                    )
                    page.wait_for_url(
                        settings.base_origin + _COURSE_INDEX_PATH,
                        timeout=_INTERACTION_TIMEOUT_MS,
                    )
                    result = self._result_for_final_url(page.url, settings.base_origin)
                    if result.code == "BROWSER_AUTHENTICATED":
                        self._write_binding_marker(profile.binding_path, settings)
                    return result
                finally:
                    context.close()
        except TimeoutError:
            return BrowserProvisionResult("SESSION_EXPIRED")
        except (BrowserPolicyError, OSError, ValueError, TypeError):
            return BrowserProvisionResult("BROWSER_PROVISIONING_FAILED")

    @staticmethod
    def _result_for_final_url(url: object, origin: str) -> BrowserProvisionResult:
        if not isinstance(url, str):
            return BrowserProvisionResult("BROWSER_PROVISIONING_FAILED")
        if url == origin + _COURSE_INDEX_PATH:
            return BrowserProvisionResult("BROWSER_AUTHENTICATED")
        if url.startswith(origin + "/current/login/") or url.startswith(origin + "/current/auth/"):
            return BrowserProvisionResult("BROWSER_AUTH_REQUIRED")
        return BrowserProvisionResult("BROWSER_PROVISIONING_FAILED")

    @staticmethod
    def _write_binding_marker(binding_path: Path, settings: MoodleSettings) -> None:
        path = binding_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "site": settings.base_origin + "/current",
                        "alias": settings.auth_alias,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError):
            raise ValueError from None
