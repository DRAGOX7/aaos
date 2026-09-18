from __future__ import annotations

from hashlib import sha256

import pytest

from aaos.agent.contracts import TrustedTaskScope
from aaos.phase8.computation_contracts import COMPUTATION_CONTRACT_VERSION
from aaos.phase8.computation_policy import (
    ComputationCapabilityPolicy,
    validate_computation_request,
)
from aaos.rag import CourseScope, GlobalScope


def test_policy_requires_explicit_trusted_capability_and_scope() -> None:
    policy = ComputationCapabilityPolicy(
        enabled=True,
        allowed_scope_kinds=frozenset({"course"}),
    )
    assert policy.is_allowed(TrustedTaskScope(CourseScope(7))) is True
    assert policy.is_allowed(TrustedTaskScope(GlobalScope())) is False
    assert ComputationCapabilityPolicy().is_allowed(TrustedTaskScope(CourseScope(7))) is False


def test_policy_decision_does_not_depend_on_untrusted_code_text() -> None:
    policy = ComputationCapabilityPolicy(
        enabled=True,
        allowed_scope_kinds=frozenset({"course"}),
    )
    assert policy.is_allowed(TrustedTaskScope(CourseScope(7))) is True
    assert policy.is_allowed(TrustedTaskScope(CourseScope(8))) is True


def test_request_validation_requires_enabled_policy() -> None:
    from aaos.phase8.computation_contracts import (
        ComputationInput,
        ComputationLimits,
        ComputationRequest,
    )

    request = ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(
            ComputationInput(
                input_id="input-1",
                media_type="text/plain",
                content_sha256=sha256(b"x").hexdigest(),
                bounded_bytes=b"x",
            ),
        ),
        limits=ComputationLimits(5, 5, 64 * 1024 * 1024, 4096, 2, 4096),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )
    with pytest.raises(ValueError, match="denied"):
        validate_computation_request(request, policy=ComputationCapabilityPolicy())
