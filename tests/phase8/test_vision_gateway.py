from __future__ import annotations

import json
from hashlib import sha256

import pytest

from aaos.phase8.vision_contracts import RenderedPage
from aaos.phase8.vision_gateway import VisionRequest


def _image() -> RenderedPage:
    content = b"png"
    return RenderedPage(7, 2, "image/png", 64, 64, sha256(content).hexdigest(), content)


def _request() -> VisionRequest:
    return VisionRequest("local-vision", "rev-1", "prompt-1", 4096)


def test_fake_success_preserves_separate_facts_and_interpretations() -> None:
    from aaos.phase8.fake_vision import FakeVisionGateway, FakeVisionScenario

    gateway = FakeVisionGateway(FakeVisionScenario.SUCCESS)
    response = gateway.generate(_image(), request=_request())

    assert response.status == "SUCCEEDED"
    payload = json.loads(response.payload or "")
    assert set(payload) == {"facts", "interpretations"}
    assert isinstance(payload["facts"], list)
    assert isinstance(payload["interpretations"], list)


@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        ("TIMEOUT", "TIMED_OUT"),
        ("UNAVAILABLE", "UNAVAILABLE"),
        ("MALFORMED_OUTPUT", "FAILED"),
    ],
)
def test_fake_failure_scenarios_never_become_success(scenario: str, status: str) -> None:
    from aaos.phase8.fake_vision import FakeVisionGateway, FakeVisionScenario

    response = FakeVisionGateway(FakeVisionScenario(scenario)).generate(
        _image(), request=_request()
    )
    assert response.status == status
    assert response.status != "SUCCEEDED"


def test_request_validation_rejects_unbounded_or_blank_identity() -> None:
    from aaos.phase8.vision_gateway import VisionRequest

    with pytest.raises(ValueError):
        VisionRequest("", "rev-1", "prompt-1", 100)
    with pytest.raises(ValueError):
        VisionRequest("vision", "rev-1", "prompt-1", 0)


def test_approved_local_model_identifier_is_not_treated_as_a_path() -> None:
    from aaos.phase8.vision_gateway import VisionRequest

    request = VisionRequest("qwen/qwen3.5-4b", "rev-1", "prompt-1", 100)
    assert request.model_id == "qwen/qwen3.5-4b"


def test_gateway_rejects_wrong_image_type() -> None:
    from aaos.phase8.fake_vision import FakeVisionGateway

    with pytest.raises(TypeError):
        FakeVisionGateway().generate(object(), request=_request())  # type: ignore[arg-type]


def test_malformed_success_payload_is_rejected_without_fabricated_result() -> None:
    from aaos.phase8.vision_gateway import VisionResponse, validate_vision_response

    malformed = object.__new__(VisionResponse)
    object.__setattr__(malformed, "status", "SUCCEEDED")
    object.__setattr__(malformed, "payload", '{"instructions":"execute code"}')
    with pytest.raises(ValueError, match="payload"):
        validate_vision_response(malformed, request=_request())


def test_vision_response_rejects_malformed_success_payload_at_boundary() -> None:
    from aaos.phase8.vision_gateway import VisionResponse

    with pytest.raises(ValueError, match="payload"):
        VisionResponse("SUCCEEDED", '{"instructions":"execute code"}')
