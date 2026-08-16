from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx2
import pytest

from aaos.moodle.models import MoodleSettings
from aaos.moodle.public_config import (
    MoodlePublicConfigError,
    MoodlePublicConfigTransport,
    parse_public_config,
)

SETTINGS = MoodleSettings(
    source_system="moodle:just:summer-2026:bootstrap",
    base_origin="https://elearn.just.edu.jo",
    mode="api",
    auth_alias="just-summer-2026",
)


def _payload() -> dict[str, object]:
    return {
        "wwwroot": "https://elearn.just.edu.jo/current",
        "httpswwwroot": "https://elearn.just.edu.jo/current",
        "enablewebservices": 1,
        "enablemobilewebservice": 1,
        "typeoflogin": 2,
        "launchurl": "https://elearn.just.edu.jo/login/index.php",
        "identityproviders": [
            {
                "name": "Microsoft",
                "url": "https://elearn.just.edu.jo/auth/oauth2/login.php?id=7",
            }
        ],
        "privateaccesskey": "must-not-escape",
        "userinfo": {"email": "private@example.invalid"},
    }


def _response_payload() -> list[dict[str, object]]:
    return [{"error": False, "data": _payload()}]


def test_parse_public_config_keeps_only_safe_typed_fields() -> None:
    result = parse_public_config(
        json.dumps(_response_payload()).encode("utf-8"), "https://elearn.just.edu.jo/current"
    )

    assert result.canonical_wwwroot == "https://elearn.just.edu.jo/current"
    assert result.httpswwwroot == "https://elearn.just.edu.jo/current"
    assert result.enablewebservices is True
    assert result.enablemobilewebservice is True
    assert result.typeoflogin == 2
    assert result.launchurl == "https://elearn.just.edu.jo/login/index.php"
    assert result.identity_providers[0].name == "Microsoft"
    assert result.identity_providers[0].oauth_provider_id == 7
    assert not hasattr(result, "privateaccesskey")


@pytest.mark.parametrize("field", ["enablewebservices", "enablemobilewebservice"])
@pytest.mark.parametrize(("raw", "expected"), [(0, False), (1, True)])
def test_parse_numeric_service_flags_as_booleans(field: str, raw: object, expected: bool) -> None:
    payload = _payload()
    payload[field] = raw
    result = parse_public_config(
        json.dumps([{"error": False, "data": payload}]).encode(),
        "https://elearn.just.edu.jo/current",
    )
    assert getattr(result, field) is expected


@pytest.mark.parametrize("field", ["enablewebservices", "enablemobilewebservice"])
@pytest.mark.parametrize("raw", [-1, 2, 99, 1.0, "1", "0", None, {}, [], True, False])
def test_parse_rejects_non_integer_service_flags(field: str, raw: object) -> None:
    payload = _payload()
    payload[field] = raw
    with pytest.raises(MoodlePublicConfigError):
        parse_public_config(
            json.dumps([{"error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )


def test_parse_rejects_non_local_or_non_numeric_identity_provider() -> None:
    payload = _payload()
    payload["identityproviders"] = [
        {"name": "Microsoft", "url": "https://login.microsoftonline.com/oauth2?id=7"}
    ]
    with pytest.raises(MoodlePublicConfigError):
        parse_public_config(
            json.dumps([{"index": 0, "error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )


def test_parse_accepts_same_origin_identity_provider_on_nonfixed_path() -> None:
    payload = _payload()
    payload["identityproviders"] = [
        {
            "name": "Microsoft",
            "url": "https://elearn.just.edu.jo/auth/plugin/login.php?id=7",
        }
    ]
    result = parse_public_config(
        json.dumps([{"error": False, "data": payload}]).encode(),
        "https://elearn.just.edu.jo/current",
    )
    assert result.identity_providers[0].oauth_provider_id == 7


def test_just_shaped_public_config_fixture_parses_without_provider_path_assumption() -> None:
    payload = _payload()
    payload["enablewebservices"] = 0
    payload["enablemobilewebservice"] = 1
    payload["identityproviders"] = [
        {
            "name": "Microsoft",
            "iconurl": "https://elearn.just.edu.jo/theme/icon.png",
            "url": "https://elearn.just.edu.jo/auth/plugin/login.php?id=7",
        }
    ]
    result = parse_public_config(
        json.dumps([{"error": False, "data": payload}]).encode(),
        "https://elearn.just.edu.jo/current",
    )
    assert result.enablewebservices is False
    assert result.enablemobilewebservice is True
    assert result.identity_providers[0].oauth_provider_id == 7


def test_provider_without_oauth_id_is_retained_without_candidate() -> None:
    payload = _payload()
    payload["identityproviders"] = [
        {
            "name": "Microsoft",
            "iconurl": "https://elearn.just.edu.jo/theme/icon.png",
            "url": "https://elearn.just.edu.jo/auth/plugin/login.php",
        }
    ]
    result = parse_public_config(
        json.dumps([{"error": False, "data": payload}]).encode(),
        "https://elearn.just.edu.jo/current",
    )
    assert result.identity_providers[0].oauth_provider_id is None


def test_invalid_provider_path_reports_safe_internal_code() -> None:
    payload = _payload()
    payload["identityproviders"] = [{"name": "Microsoft", "url": "not-a-url"}]
    with pytest.raises(MoodlePublicConfigError) as error:
        parse_public_config(
            json.dumps([{"error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )
    assert error.value.code == "invalid_provider_url"
    assert str(error.value) == "Moodle public configuration response was invalid"


def _assert_internal_code(payload: dict[str, object], expected: str) -> None:
    with pytest.raises(MoodlePublicConfigError) as error:
        parse_public_config(
            json.dumps([{"error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )
    assert error.value.code == expected
    assert str(error.value) == "Moodle public configuration response was invalid"


def test_internal_codes_identify_top_level_field_validators() -> None:
    payload = _payload()
    payload["wwwroot"] = "not-a-url"
    _assert_internal_code(payload, "invalid_wwwroot")
    payload = _payload()
    payload["httpswwwroot"] = "not-a-url"
    _assert_internal_code(payload, "invalid_httpswwwroot")
    payload = _payload()
    payload["enablewebservices"] = 2
    _assert_internal_code(payload, "invalid_enablewebservices")
    payload = _payload()
    payload["enablemobilewebservice"] = 2
    _assert_internal_code(payload, "invalid_enablemobilewebservice")
    payload = _payload()
    payload["typeoflogin"] = 9
    _assert_internal_code(payload, "invalid_typeoflogin")
    payload = _payload()
    payload["launchurl"] = "not-a-url"
    _assert_internal_code(payload, "invalid_launchurl")
    payload = _payload()
    payload["identityproviders"] = "not-an-array"
    _assert_internal_code(payload, "invalid_identityproviders_type")


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("not-an-object", "invalid_provider_entry"),
        ({"url": "https://elearn.just.edu.jo/login?id=7"}, "invalid_provider_name"),
        ({"name": "Provider", "url": "https://other.example/login?id=7"}, "provider_cross_origin"),
        (
            {"name": "Provider", "url": "https://elearn.just.edu.jo/login?id=abc"},
            "provider_invalid_id",
        ),
        (
            {"name": "Provider", "url": "https://elearn.just.edu.jo/login?id=7&id=8"},
            "provider_invalid_id",
        ),
    ],
)
def test_internal_codes_identify_provider_validators(provider: object, expected: str) -> None:
    payload = _payload()
    payload["identityproviders"] = [provider]
    _assert_internal_code(payload, expected)


def test_internal_code_identifies_duplicate_provider_ids() -> None:
    payload = _payload()
    payload["identityproviders"] = [
        {"name": "One", "url": "https://elearn.just.edu.jo/login?id=7"},
        {"name": "Two", "url": "https://elearn.just.edu.jo/other?id=7"},
    ]
    _assert_internal_code(payload, "provider_duplicate_id")


@pytest.mark.parametrize(
    "provider_url",
    [
        "https://elearn.just.edu.jo/auth/oauth2/login.php?id=abc",
        "https://elearn.just.edu.jo/auth/oauth2/login.php?id=0",
        "https://elearn.just.edu.jo/auth/oauth2/login.php?id=7&extra=1",
    ],
)
def test_parse_rejects_malformed_local_identity_provider_ids(provider_url: str) -> None:
    payload = _payload()
    payload["identityproviders"] = [{"name": "Provider", "url": provider_url}]
    with pytest.raises(MoodlePublicConfigError):
        parse_public_config(
            json.dumps([{"index": 0, "error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )


def test_parse_rejects_response_for_wrong_same_origin_path() -> None:
    payload = _payload()
    payload["wwwroot"] = "https://elearn.just.edu.jo/other"
    payload["httpswwwroot"] = "https://elearn.just.edu.jo/other"
    with pytest.raises(MoodlePublicConfigError):
        parse_public_config(
            json.dumps([{"index": 0, "error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )


def test_transport_rejects_noncanonical_probe_url() -> None:
    with pytest.raises(MoodlePublicConfigError):
        MoodlePublicConfigTransport(
            SETTINGS,
            "https://elearn.just.edu.jo/other",
            httpx2.MockTransport(lambda _: httpx2.Response(200)),
        )


def test_transport_uses_normalized_canonical_probe_url() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_response_payload())

    transport = MoodlePublicConfigTransport(
        SETTINGS,
        " https://elearn.just.edu.jo/current ",
        httpx2.MockTransport(handler),
    )
    try:
        asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())
    assert requests[0].url.path == "/current/lib/ajax/service-nologin.php"


def test_transport_posts_one_unauthenticated_public_config_request() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_response_payload())

    transport = MoodlePublicConfigTransport(
        SETTINGS,
        "https://elearn.just.edu.jo/current",
        httpx2.MockTransport(handler),
    )
    try:
        result = asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())

    assert result.typeoflogin == 2
    assert len(requests) == 1
    assert requests[0].url.path == "/current/lib/ajax/service-nologin.php"
    assert dict(requests[0].url.params) == {
        "info": "tool_mobile_get_public_config",
        "lang": "en",
    }
    assert json.loads(requests[0].content) == [
        {"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}
    ]
    assert "wstoken" not in requests[0].content.decode()


def test_transport_maps_backend_and_protocol_failures_to_fixed_error() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="secret backend detail")

    transport = MoodlePublicConfigTransport(
        SETTINGS, "https://elearn.just.edu.jo/current", httpx2.MockTransport(handler)
    )
    try:
        with pytest.raises(MoodlePublicConfigError) as error:
            asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())

    assert str(error.value) == "Moodle public configuration request failed"


@pytest.mark.parametrize(
    "response",
    [
        [{"index": 0, "error": False}],
        [{"index": 0, "error": {}, "data": _payload()}],
        {"error": False, "data": _payload()},
        [
            {"index": 0, "error": False, "data": _payload()},
            {"index": 1, "error": False, "data": _payload()},
        ],
    ],
)
def test_parse_rejects_invalid_ajax_envelopes(response: object) -> None:
    with pytest.raises(MoodlePublicConfigError):
        parse_public_config(json.dumps(response).encode(), "https://elearn.just.edu.jo/current")


def test_parse_classifies_top_level_ajax_error_without_leaking_details() -> None:
    with pytest.raises(MoodlePublicConfigError) as error:
        parse_public_config(
            json.dumps({"error": True, "exception": "private backend detail"}).encode(),
            "https://elearn.just.edu.jo/current",
        )
    assert error.value.category == "ajax_error"
    assert str(error.value) == "Moodle public configuration reported an AJAX error"


def test_parse_classifies_operation_ajax_error_without_leaking_details() -> None:
    with pytest.raises(MoodlePublicConfigError) as error:
        parse_public_config(
            json.dumps(
                [{"index": 0, "error": True, "exception": "private backend detail"}]
            ).encode(),
            "https://elearn.just.edu.jo/current",
        )
    assert error.value.category == "ajax_error"
    assert str(error.value) == "Moodle public configuration reported an AJAX error"


def test_parse_rejects_duplicate_provider_ids() -> None:
    payload = _payload()
    payload["identityproviders"] = [
        {"name": "One", "url": "https://elearn.just.edu.jo/auth/oauth2/login.php?id=7"},
        {"name": "Two", "url": "https://elearn.just.edu.jo/auth/oauth2/login.php?id=7"},
    ]
    with pytest.raises(MoodlePublicConfigError):
        parse_public_config(
            json.dumps([{"index": 0, "error": False, "data": payload}]).encode(),
            "https://elearn.just.edu.jo/current",
        )


def test_transport_rejects_oversized_response_before_parsing() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"{" + b"x" * (256 * 1024))

    transport = MoodlePublicConfigTransport(
        SETTINGS, "https://elearn.just.edu.jo/current", httpx2.MockTransport(handler)
    )
    try:
        with pytest.raises(MoodlePublicConfigError) as error:
            asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())
    assert str(error.value) == "Moodle public configuration response was invalid"


def test_transport_bounds_streamed_response() -> None:
    class OversizedStream(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"{" + b"x" * (256 * 1024)

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            stream=OversizedStream(),
        )

    transport = MoodlePublicConfigTransport(
        SETTINGS, "https://elearn.just.edu.jo/current", httpx2.MockTransport(handler)
    )
    try:
        with pytest.raises(MoodlePublicConfigError) as error:
            asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())
    assert str(error.value) == "Moodle public configuration response was invalid"


def test_transport_rejects_unexpected_redirect() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://login.example.invalid"})

    transport = MoodlePublicConfigTransport(
        SETTINGS, "https://elearn.just.edu.jo/current", httpx2.MockTransport(handler)
    )
    try:
        with pytest.raises(MoodlePublicConfigError) as error:
            asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())
    assert str(error.value) == "Moodle public configuration request failed"


def test_transport_rejects_non_json_content_type() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=b"<html>not json</html>", headers={"content-type": "text/html"}
        )

    transport = MoodlePublicConfigTransport(
        SETTINGS, "https://elearn.just.edu.jo/current", httpx2.MockTransport(handler)
    )
    try:
        with pytest.raises(MoodlePublicConfigError) as error:
            asyncio.run(transport.get_public_config())
    finally:
        asyncio.run(transport.aclose())
    assert str(error.value) == "Moodle public configuration response was invalid"
