"""RED tests for the trusted local principal boundary."""

from __future__ import annotations

import pytest

from aaos.approval.contracts import ActorIdentity, TrustedApproverContext
from aaos.config import AppSettings
from aaos.web.principal import LocalPrincipalProvider, PrincipalUnavailable


def test_missing_local_principal_configuration_fails_closed() -> None:
    with pytest.raises(PrincipalUnavailable):
        LocalPrincipalProvider(AppSettings()).current()


def test_configured_local_principal_derives_actor_and_approver() -> None:
    settings = AppSettings(
        local_approver_identity="local-operator",
        local_approver_context_id="context-1",
    )
    principal = LocalPrincipalProvider(settings).current()

    assert principal == TrustedApproverContext("local-operator", "context-1")
    assert principal.approver_identity == ActorIdentity("local-operator")


@pytest.mark.parametrize(
    ("identity", "context"),
    (("", "context-1"), ("operator/unsafe", "context-1"), ("operator", "")),
)
def test_invalid_principal_settings_are_rejected(identity: str, context: str) -> None:
    with pytest.raises(ValueError):
        AppSettings(
            local_approver_identity=identity,
            local_approver_context_id=context,
        )


def test_deterministic_provider_can_be_injected_without_environment_mutation() -> None:
    expected = TrustedApproverContext("test-operator", "test-context")
    principal = LocalPrincipalProvider.from_trusted_value(expected)

    assert principal.current() == expected
