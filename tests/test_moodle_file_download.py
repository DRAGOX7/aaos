from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from aaos.documents.contracts import (
    MAX_PDF_BYTES,
    DocumentIngestionFailure,
    IngestionErrorCode,
    RemoteFileSelection,
)
from aaos.moodle.file_download import (
    BrowserAuthenticatedFetcher,
    MoodleFileAuthorizer,
    open_phase3_fetcher_session,
)
from aaos.moodle.models import AuthPaths, MoodleSettings

ORIGIN = "https://moodle.example.edu"
WWWROOT = ORIGIN + "/current"
SOURCE_SYSTEM = "moodle:example:phase3"
RESOURCE_URL = WWWROOT + "/mod/resource/view.php?id=42"
PLUGINFILE_URL = WWWROOT + "/pluginfile.php/7/mod_resource/content/1/lecture.pdf"


def selection(
    *,
    remote_file_id: int = 11,
    course_id: int = 3,
    source_system: str = SOURCE_SYSTEM,
    source_id: str = "resource-cm:42",
    title: str = "Lecture",
) -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=remote_file_id,
        course_id=course_id,
        source_system=source_system,
        source_id=source_id,
        title=title,
    )


def assert_failure(
    caught: pytest.ExceptionInfo[DocumentIngestionFailure], code: IngestionErrorCode
) -> None:
    assert caught.value.code is code
    assert str(caught.value) == "document ingestion failed"


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.body_reads = 0

    def body(self) -> bytes:
        self.body_reads += 1
        return self._body


class FakeRequest:
    def __init__(self, outcomes: list[FakeResponse | BaseException]) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeContext:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request


def authorizer() -> MoodleFileAuthorizer:
    return MoodleFileAuthorizer(
        canonical_wwwroot=WWWROOT,
        expected_source_system=SOURCE_SYSTEM,
    )


def test_authorizer_builds_the_only_initial_resource_url_and_redacts_it() -> None:
    permit = authorizer().authorize(selection())

    assert permit.remote_file_id == 11
    assert permit.source_id == "resource-cm:42"
    assert permit.initial_url == RESOURCE_URL
    assert RESOURCE_URL not in repr(permit)


@pytest.mark.parametrize(
    "invalid",
    [
        selection(source_system="moodle:other"),
        selection(remote_file_id=0),
        selection(course_id=0),
        selection(source_id="resource-cm:0"),
        selection(source_id="resource-cm:-1"),
        selection(source_id="resource-cm:42/extra"),
        selection(source_id="pluginfile.php/42"),
    ],
)
def test_authorizer_rejects_any_selection_outside_the_fixed_resource_identity(
    invalid: RemoteFileSelection,
) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        authorizer().authorize(invalid)
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_ROUTE)


@pytest.mark.parametrize(
    "location",
    [
        "https://other.example.edu/current/pluginfile.php/7/mod_resource/content/1/a.pdf",
        "http://moodle.example.edu/current/pluginfile.php/7/mod_resource/content/1/a.pdf",
        "file:///tmp/a.pdf",
        "data:application/pdf,content",
        "javascript:alert(1)",
        "https://user@moodle.example.edu/current/pluginfile.php/7/mod_resource/content/1/a.pdf",
        "/current/pluginfile.php/7/mod_resource/content/1/a.pdf#fragment",
        "/currentevil/pluginfile.php/7/mod_resource/content/1/a.pdf",
        "/current/course/view.php?id=3",
        "/current/pluginfile.php/7/mod_assign/intro/1/a.pdf",
        "/current/pluginfile.php/7/mod_resource/other/1/a.pdf",
        "/current/pluginfile.php/7/mod_resource/content/1/%2e%2e/private.pdf",
    ],
)
def test_redirect_authorization_rejects_every_non_resource_continuation(location: str) -> None:
    permit = authorizer().authorize(selection())
    with pytest.raises(DocumentIngestionFailure) as caught:
        authorizer().authorize_redirect(
            permit=permit,
            current_url=permit.initial_url,
            location=location,
        )
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_REDIRECT)


@pytest.mark.parametrize(
    "wwwroot",
    [
        "https://127.0.0.1/current",
        "https://localhost/current",
        "https://10.0.0.4/current",
        "https://169.254.1.2/current",
        "https://224.0.0.1/current",
    ],
)
def test_authorizer_rejects_literal_non_public_network_origins(wwwroot: str) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        MoodleFileAuthorizer(
            canonical_wwwroot=wwwroot,
            expected_source_system=SOURCE_SYSTEM,
        )
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_ROUTE)


def test_authorizer_accepts_only_the_resource_pluginfile_continuation() -> None:
    permit = authorizer().authorize(selection())
    target = authorizer().authorize_redirect(
        permit=permit,
        current_url=permit.initial_url,
        location="/current/pluginfile.php/7/mod_resource/content/1/lecture.pdf",
    )
    authorizer().authorize_final_url(permit=permit, url=target)
    assert target == PLUGINFILE_URL


def test_final_authorization_rejects_the_initial_resource_route() -> None:
    permit = authorizer().authorize(selection())
    with pytest.raises(DocumentIngestionFailure) as caught:
        authorizer().authorize_final_url(permit=permit, url=permit.initial_url)
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_ROUTE)


def test_fetch_follows_one_manual_redirect_and_reads_the_body_once() -> None:
    redirect = FakeResponse(302, headers={"location": PLUGINFILE_URL})
    final = FakeResponse(
        200,
        headers={
            "content-length": "8",
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="lecture.pdf"',
        },
        body=b"%PDF-1.7",
    )
    request = FakeRequest([redirect, final])

    fetched = BrowserAuthenticatedFetcher(
        context=AnyContext(FakeContext(request)), authorizer=authorizer()
    ).fetch(selection())

    assert fetched.body == b"%PDF-1.7"
    assert fetched.source_filename == "lecture.pdf"
    assert fetched.advisory_media_type == "application/pdf"
    assert final.body_reads == 1
    assert request.calls == [
        (RESOURCE_URL, {"max_redirects": 0, "timeout": 120_000}),
        (PLUGINFILE_URL, {"max_redirects": 0, "timeout": 120_000}),
    ]


class AnyContext:
    """Test adapter that keeps fake transport details out of production types."""

    def __new__(cls, value: object) -> Any:
        return value


def test_fetch_rejects_redirect_cycle_and_redirect_overflow() -> None:
    cycle = FakeRequest([FakeResponse(302, headers={"location": RESOURCE_URL})])
    with pytest.raises(DocumentIngestionFailure) as caught:
        BrowserAuthenticatedFetcher(
            context=AnyContext(FakeContext(cycle)), authorizer=authorizer()
        ).fetch(selection())
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    locations = [
        f"{WWWROOT}/pluginfile.php/{number}/mod_resource/content/1/a.pdf" for number in range(1, 7)
    ]
    overflow = FakeRequest(
        [FakeResponse(302, headers={"location": location}) for location in locations]
    )
    with pytest.raises(DocumentIngestionFailure) as caught:
        BrowserAuthenticatedFetcher(
            context=AnyContext(FakeContext(overflow)), authorizer=authorizer()
        ).fetch(selection())
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_REDIRECT)


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (PlaywrightTimeoutError("redacted"), IngestionErrorCode.NETWORK_TIMEOUT),
        (PlaywrightError("redacted"), IngestionErrorCode.NETWORK_FAILURE),
        (FakeResponse(404), IngestionErrorCode.NETWORK_FAILURE),
    ],
)
def test_fetch_maps_transport_failures_to_fixed_codes(
    outcome: FakeResponse | BaseException, code: IngestionErrorCode
) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        BrowserAuthenticatedFetcher(
            context=AnyContext(FakeContext(FakeRequest([outcome]))), authorizer=authorizer()
        ).fetch(selection())
    assert_failure(caught, code)
    assert "redacted" not in str(caught.value)


def test_fetch_rejects_declared_oversize_before_reading() -> None:
    redirect = FakeResponse(302, headers={"location": PLUGINFILE_URL})
    final = FakeResponse(200, headers={"content-length": str(MAX_PDF_BYTES + 1)}, body=b"x")
    with pytest.raises(DocumentIngestionFailure) as caught:
        BrowserAuthenticatedFetcher(
            context=AnyContext(FakeContext(FakeRequest([redirect, final]))),
            authorizer=authorizer(),
        ).fetch(selection())
    assert_failure(caught, IngestionErrorCode.DOWNLOAD_TOO_LARGE)
    assert final.body_reads == 0


def test_fetch_rejects_actual_oversize_after_one_read() -> None:
    redirect = FakeResponse(302, headers={"location": PLUGINFILE_URL})
    final = FakeResponse(200, body=b"x" * (MAX_PDF_BYTES + 1))
    with pytest.raises(DocumentIngestionFailure) as caught:
        BrowserAuthenticatedFetcher(
            context=AnyContext(FakeContext(FakeRequest([redirect, final]))),
            authorizer=authorizer(),
        ).fetch(selection())
    assert_failure(caught, IngestionErrorCode.DOWNLOAD_TOO_LARGE)
    assert final.body_reads == 1


def test_fetch_falls_back_from_unsafe_content_disposition_filename() -> None:
    request = FakeRequest(
        [
            FakeResponse(302, headers={"location": PLUGINFILE_URL}),
            FakeResponse(
                200,
                headers={"content-disposition": 'attachment; filename="../private.pdf"'},
                body=b"%PDF",
            ),
        ]
    )
    fetched = BrowserAuthenticatedFetcher(
        context=AnyContext(FakeContext(request)), authorizer=authorizer()
    ).fetch(selection())
    assert fetched.source_filename == "resource-42.pdf"


def test_fetcher_session_uses_one_existing_profile_lease_and_closes_before_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.file_download as module

    settings = MoodleSettings(SOURCE_SYSTEM, ORIGIN, "browser", "phase3-test")
    auth_paths = AuthPaths(tmp_path / "unused-token", tmp_path / "auth")
    profile_root = auth_paths.browser_state_dir
    profile_root.mkdir(parents=True)
    (profile_root / "playwright-edge-profile.binding.json").write_text(
        json.dumps({"version": 1, "site": WWWROOT, "alias": settings.auth_alias}),
        encoding="utf-8",
    )
    events: list[str] = []

    class Page:
        url = WWWROOT + "/my/courses.php"

        def goto(self, *_args: object, **_kwargs: object) -> None:
            events.append("goto")

        def close(self) -> None:
            events.append("page_closed")

    class Context:
        request = FakeRequest([])

        def new_page(self) -> Page:
            events.append("page_created")
            return Page()

        def close(self) -> None:
            lock = profile_root / ".playwright-edge-profile.lock"
            assert lock.exists()
            events.append("context_closed")

    def launch(**kwargs: object) -> Context:
        assert kwargs == {
            "channel": "msedge",
            "headless": False,
            "user_data_dir": profile_root / "playwright-edge-profile",
            "accept_downloads": False,
            "service_workers": "block",
        }
        events.append("context_launched")
        return Context()

    monkeypatch.setattr(module, "launch_msedge_persistent_context", launch)
    monkeypatch.setattr(module, "wait_for_authenticated_course_index", lambda *_a, **_k: True)

    with open_phase3_fetcher_session(
        canonical_wwwroot=WWWROOT,
        expected_source_system=SOURCE_SYSTEM,
        settings=settings,
        auth_paths=auth_paths,
    ) as fetcher:
        assert isinstance(fetcher, BrowserAuthenticatedFetcher)
        assert (profile_root / ".playwright-edge-profile.lock").exists()
        events.append("yielded")

    assert events == [
        "context_launched",
        "page_created",
        "goto",
        "page_closed",
        "yielded",
        "context_closed",
    ]
    assert not (profile_root / ".playwright-edge-profile.lock").exists()


def test_fetcher_session_fails_safely_when_context_cannot_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.file_download as module

    settings = MoodleSettings(SOURCE_SYSTEM, ORIGIN, "browser", "phase3-test")
    auth_paths = AuthPaths(tmp_path / "unused-token", tmp_path / "auth")
    profile_root = auth_paths.browser_state_dir
    profile_root.mkdir(parents=True)
    (profile_root / "playwright-edge-profile.binding.json").write_text(
        json.dumps({"version": 1, "site": WWWROOT, "alias": settings.auth_alias}),
        encoding="utf-8",
    )

    class Page:
        url = WWWROOT + "/my/courses.php"

        def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

        def close(self) -> None:
            return None

    class Context:
        request = FakeRequest([])

        def new_page(self) -> Page:
            return Page()

        def close(self) -> None:
            assert (profile_root / ".playwright-edge-profile.lock").exists()
            raise PlaywrightError("sentinel-secret")

    monkeypatch.setattr(module, "launch_msedge_persistent_context", lambda **_kwargs: Context())
    monkeypatch.setattr(module, "wait_for_authenticated_course_index", lambda *_a, **_k: True)

    with pytest.raises(DocumentIngestionFailure) as caught:
        with open_phase3_fetcher_session(
            canonical_wwwroot=WWWROOT,
            expected_source_system=SOURCE_SYSTEM,
            settings=settings,
            auth_paths=auth_paths,
        ):
            pass
    assert_failure(caught, IngestionErrorCode.NETWORK_FAILURE)
    assert "sentinel-secret" not in str(caught.value)
    assert not (profile_root / ".playwright-edge-profile.lock").exists()


def test_fetcher_session_rejects_a_canonical_root_not_bound_to_the_existing_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.file_download as module

    settings = MoodleSettings(SOURCE_SYSTEM, ORIGIN, "browser", "phase3-test")
    monkeypatch.setattr(
        module,
        "launch_msedge_persistent_context",
        lambda **_kwargs: pytest.fail("browser launch must not be reached"),
    )
    with pytest.raises(DocumentIngestionFailure) as caught:
        with open_phase3_fetcher_session(
            canonical_wwwroot=ORIGIN + "/other",
            expected_source_system=SOURCE_SYSTEM,
            settings=settings,
            auth_paths=AuthPaths(tmp_path / "unused-token", tmp_path / "auth"),
        ):
            pass
    assert_failure(caught, IngestionErrorCode.UNAUTHORIZED_ROUTE)
