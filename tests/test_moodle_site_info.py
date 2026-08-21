from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx2
import pytest

from aaos.moodle.api import (
    MoodleApiError,
    MoodleApiTransport,
    SafeSiteInfo,
    SingleUseSiteInfoProbeAuthorization,
    SiteInfoProbeAuthorization,
)
from aaos.moodle.models import MoodleSettings

SETTINGS = MoodleSettings(
    source_system="moodle:just:student-all-courses",
    base_origin="https://elearn.just.edu.jo",
    mode="api",
    auth_alias="just",
)
CANONICAL_WWWROOT = "https://elearn.just.edu.jo/current"


class FakeAuthorization:
    def __init__(self) -> None:
        self.consumed = False

    def consume(self) -> None:
        if self.consumed:
            raise MoodleApiError("Moodle site-info probe already used")
        self.consumed = True


def _authorization() -> SiteInfoProbeAuthorization:
    return SingleUseSiteInfoProbeAuthorization()


class Reader:
    def __init__(self, token: str = "SECRET-REST-TOKEN") -> None:
        self.token = token
        self.calls = 0

    def read_api_token(self) -> str:
        self.calls += 1
        return self.token


def _probe(
    handler: Callable[[httpx2.Request], httpx2.Response],
    reader: Reader | None = None,
    *,
    canonical_wwwroot: str = CANONICAL_WWWROOT,
) -> tuple[SafeSiteInfo, Reader]:
    selected_reader = reader or Reader()

    async def scenario() -> SafeSiteInfo:
        transport = MoodleApiTransport(
            settings=SETTINGS,
            canonical_wwwroot=canonical_wwwroot,
            transport=httpx2.MockTransport(handler),
            credential_reader=selected_reader,
            authorization=_authorization(),
        )
        try:
            return await transport.get_site_info()
        finally:
            await transport.aclose()

    return asyncio.run(scenario()), selected_reader


def test_site_info_uses_one_fixed_read_operation_and_safe_fields() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = request.content.decode("utf-8")
        assert "SECRET-REST-TOKEN" in body
        assert "wsfunction=core_webservice_get_site_info" in body
        assert "moodlewsrestformat=json" in body
        return httpx2.Response(
            200,
            json={
                "siteurl": CANONICAL_WWWROOT,
                "release": "4.5.0",
                "functions": [
                    {"name": "core_webservice_get_site_info"},
                    {"name": "core_course_get_courses"},
                ],
                "username": "PRIVATE-USER",
                "fullname": "PRIVATE-NAME",
                "email": "PRIVATE-EMAIL",
                "token": "PRIVATE-TOKEN",
            },
        )

    result, reader = _probe(handler)

    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{CANONICAL_WWWROOT}/webservice/rest/server.php"
    assert seen[0].url.path != "/webservice/rest/server.php"
    assert result == SafeSiteInfo(
        canonical_wwwroot=CANONICAL_WWWROOT,
        release="4.5.0",
        available_functions=("core_webservice_get_site_info", "core_course_get_courses"),
    )
    assert reader.calls == 1
    assert "PRIVATE" not in repr(result)
    assert not hasattr(MoodleApiTransport, "fetch_courses")


def test_site_info_requires_explicit_probe_authorization() -> None:
    with pytest.raises(TypeError):
        MoodleApiTransport(  # type: ignore[call-arg]
            SETTINGS,
            CANONICAL_WWWROOT,
            httpx2.MockTransport(lambda _: httpx2.Response(500)),
            Reader(),
        )


def test_site_info_rejects_cross_origin_wwwroot_before_reading_credential() -> None:
    reader = Reader()

    with pytest.raises(MoodleApiError) as error:
        _probe(
            lambda _: httpx2.Response(500),
            reader,
            canonical_wwwroot="https://other.example.edu/moodle",
        )

    assert str(error.value) == "Moodle site-info configuration was invalid"
    assert reader.calls == 0


def test_site_info_preserves_any_validated_application_path() -> None:
    canonical_wwwroot = "https://elearn.just.edu.jo/academic/moodle"
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "siteurl": canonical_wwwroot,
                "functions": [{"name": "core_webservice_get_site_info"}],
            },
        )

    result, _ = _probe(handler, canonical_wwwroot=canonical_wwwroot)

    assert str(seen[0].url) == f"{canonical_wwwroot}/webservice/rest/server.php"
    assert result.canonical_wwwroot == canonical_wwwroot


def test_site_info_authorization_is_single_use() -> None:
    requests = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(
            200,
            json={
                "siteurl": CANONICAL_WWWROOT,
                "functions": [{"name": "core_webservice_get_site_info"}],
            },
        )

    async def scenario() -> None:
        transport = MoodleApiTransport(
            SETTINGS,
            CANONICAL_WWWROOT,
            httpx2.MockTransport(handler),
            Reader(),
            _authorization(),
        )
        try:
            await transport.get_site_info()
            with pytest.raises(MoodleApiError) as error:
                await transport.get_site_info()
            assert str(error.value) == "Moodle site-info probe already used"
        finally:
            await transport.aclose()

    asyncio.run(scenario())
    assert requests == 1


def test_site_info_authorization_cannot_be_reused_across_transports() -> None:
    requests = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx2.Response(
            200,
            json={
                "siteurl": CANONICAL_WWWROOT,
                "functions": [{"name": "core_webservice_get_site_info"}],
            },
        )

    async def scenario() -> None:
        authorization = _authorization()
        first = MoodleApiTransport(
            SETTINGS, CANONICAL_WWWROOT, httpx2.MockTransport(handler), Reader(), authorization
        )
        second = MoodleApiTransport(
            SETTINGS, CANONICAL_WWWROOT, httpx2.MockTransport(handler), Reader(), authorization
        )
        try:
            await first.get_site_info()
            with pytest.raises(MoodleApiError) as error:
                await second.get_site_info()
            assert str(error.value) == "Moodle site-info probe already used"
        finally:
            await first.aclose()
            await second.aclose()

    asyncio.run(scenario())
    assert requests == 1


def test_site_info_rejects_malformed_payload_without_raw_body() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"SECRET-RAW-BODY")

    with pytest.raises(MoodleApiError) as error:
        _probe(handler)

    assert str(error.value) == "Moodle site-info response was invalid"
    assert "SECRET-RAW-BODY" not in repr(error.value)


@pytest.mark.parametrize("status", [301, 302, 401, 403, 500])
def test_site_info_rejects_redirects_and_http_failures_without_details(status: int) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers={"location": "https://secret.example"})

    with pytest.raises(MoodleApiError) as error:
        _probe(handler)

    assert str(error.value) == "Moodle site-info request failed"
    assert "secret.example" not in repr(error.value)


def test_site_info_maps_transport_failure_to_fixed_error() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.RequestError("SECRET-TRANSPORT", request=None)

    with pytest.raises(MoodleApiError) as error:
        _probe(handler)

    assert str(error.value) == "Moodle site-info request failed"
    assert "SECRET-TRANSPORT" not in repr(error.value)
