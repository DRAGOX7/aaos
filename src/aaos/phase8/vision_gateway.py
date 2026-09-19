"""Typed, provider-free Phase 8 vision gateway contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from .vision_contracts import RenderedPage

VisionStatus = Literal["SUCCEEDED", "PARTIAL", "FAILED", "TIMED_OUT", "UNAVAILABLE"]
MAX_VISION_OUTPUT_BYTES = 1 * 1024 * 1024
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _validate_success_payload(payload: str, *, require_fact: bool = False) -> None:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("vision payload is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"facts", "interpretations"}:
        raise ValueError("vision payload fields are invalid")
    for field in ("facts", "interpretations"):
        entries = value[field]
        if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
            raise ValueError(f"vision {field} are invalid")
    if require_fact and not any(isinstance(item, str) and item.strip() for item in value["facts"]):
        raise ValueError("vision partial response requires a fact")


@dataclass(frozen=True, slots=True)
class VisionRequest:
    model_id: str
    model_revision: str
    prompt_revision: str
    max_output_bytes: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model_id, str)
            or not _SAFE_MODEL_ID.fullmatch(self.model_id)
            or ".." in self.model_id
            or "//" in self.model_id
        ):
            raise ValueError("model_id must be a safe model identifier")
        for name, value in (
            ("model_revision", self.model_revision),
            ("prompt_revision", self.prompt_revision),
        ):
            if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a safe identifier")
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or self.max_output_bytes <= 0
            or self.max_output_bytes > MAX_VISION_OUTPUT_BYTES
        ):
            raise ValueError("max_output_bytes exceeds the vision bound")


@dataclass(frozen=True, slots=True)
class VisionResponse:
    status: VisionStatus
    payload: str | None

    def __post_init__(self) -> None:
        if self.status not in {"SUCCEEDED", "PARTIAL", "FAILED", "TIMED_OUT", "UNAVAILABLE"}:
            raise ValueError("status is unsupported")
        if self.payload is not None and (
            not isinstance(self.payload, str)
            or len(self.payload.encode("utf-8")) > MAX_VISION_OUTPUT_BYTES
        ):
            raise ValueError("payload exceeds the vision bound")
        if self.status in {"SUCCEEDED", "PARTIAL"} and not self.payload:
            raise ValueError("successful response requires payload")
        if self.status in {"SUCCEEDED", "PARTIAL"}:
            _validate_success_payload(self.payload or "", require_fact=self.status == "PARTIAL")
        if self.status not in {"SUCCEEDED", "PARTIAL"} and self.payload is not None:
            raise ValueError("failed response cannot carry payload")


class VisionModelGateway(Protocol):
    def generate(self, image: RenderedPage, *, request: VisionRequest) -> VisionResponse: ...


def validate_vision_response(response: VisionResponse, *, request: VisionRequest) -> VisionResponse:
    if not isinstance(response, VisionResponse) or not isinstance(request, VisionRequest):
        raise TypeError("response and request must be typed vision contracts")
    if response.status not in {"SUCCEEDED", "PARTIAL"}:
        return response
    _validate_success_payload(response.payload or "", require_fact=response.status == "PARTIAL")
    return response


__all__ = [
    "MAX_VISION_OUTPUT_BYTES",
    "VisionModelGateway",
    "VisionRequest",
    "VisionResponse",
    "VisionStatus",
    "validate_vision_response",
]
