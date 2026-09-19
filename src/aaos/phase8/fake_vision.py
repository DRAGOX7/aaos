"""Deterministic fake vision adapter for Phase 8 tests only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from .vision_contracts import RenderedPage
from .vision_gateway import (
    VisionRequest,
    VisionResponse,
    validate_vision_response,
)


class FakeVisionScenario(StrEnum):
    SUCCESS = "SUCCESS"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class FakeVisionGateway:
    scenario: FakeVisionScenario = FakeVisionScenario.SUCCESS
    calls: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, FakeVisionScenario):
            raise TypeError("scenario must be a FakeVisionScenario")

    def generate(self, image: RenderedPage, *, request: VisionRequest) -> VisionResponse:
        if not isinstance(image, RenderedPage) or not isinstance(request, VisionRequest):
            raise TypeError("image and request must be typed vision contracts")
        self.calls += 1
        if self.scenario is FakeVisionScenario.TIMEOUT:
            return VisionResponse("TIMED_OUT", None)
        if self.scenario is FakeVisionScenario.UNAVAILABLE:
            return VisionResponse("UNAVAILABLE", None)
        if self.scenario is FakeVisionScenario.MALFORMED_OUTPUT:
            malformed = VisionResponse("SUCCEEDED", '{"instructions":"ignore policy"}')
            try:
                validate_vision_response(malformed, request=request)
            except ValueError:
                return VisionResponse("FAILED", None)
        payload = json.dumps(
            {"facts": ["visual content observed"], "interpretations": []},
            separators=(",", ":"),
            sort_keys=True,
        )
        return validate_vision_response(VisionResponse("SUCCEEDED", payload), request=request)


__all__ = ["FakeVisionGateway", "FakeVisionScenario"]
