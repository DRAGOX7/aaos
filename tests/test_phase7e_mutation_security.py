"""RED tests for the Phase 7E same-origin mutation fence."""

from __future__ import annotations

import pytest

from aaos.web.mutation_security import MutationRequestRejected, enforce_mutation_fence


def test_same_origin_loopback_json_mutation_is_allowed() -> None:
    enforce_mutation_fence(
        host="127.0.0.1:8000",
        origin="http://127.0.0.1:3000",
        content_type="application/json",
    )


@pytest.mark.parametrize(
    ("host", "origin", "content_type"),
    (
        ("evil.example", "http://127.0.0.1:3000", "application/json"),
        ("127.0.0.1:8000", "https://evil.example", "application/json"),
        ("127.0.0.1:8000", "http://127.0.0.1:3000", "text/plain"),
        ("127.0.0.1:8000", "not-an-origin", "application/json"),
        ("127.0.0.1:8000", "http://127.0.0.1:3000", None),
    ),
)
def test_foreign_or_malformed_mutation_request_fails_closed(
    host: str, origin: str, content_type: str | None
) -> None:
    with pytest.raises(MutationRequestRejected):
        enforce_mutation_fence(host=host, origin=origin, content_type=content_type)


def test_mutation_fence_does_not_accept_browser_authority_fields() -> None:
    with pytest.raises(MutationRequestRejected):
        enforce_mutation_fence(
            host="127.0.0.1:8000",
            origin="http://127.0.0.1:3000",
            content_type="application/json",
            authority_fields=("approver_identity",),
        )
