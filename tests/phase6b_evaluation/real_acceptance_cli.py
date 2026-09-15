from __future__ import annotations

import ctypes
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from aaos.agent.contracts import JSONValue, PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import (
    dispatch_claimed_operation,
    load_executable_action,
)
from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ActionType,
    ActorIdentity,
    ClaimIdentity,
    OperationId,
    OperationState,
    Phase6ActionDefinition,
    RemotePrecondition,
    TrustedApproverContext,
)
from aaos.approval.repository import ApprovalRepository, read_states
from aaos.approval.service import (
    approve_approval,
    claim_action,
    prepare_action,
    reconcile_operation,
    record_adapter_outcome,
    recover_inflight_operation,
    request_approval,
)
from aaos.integrations.google_calendar.adapter import GoogleCalendarAdapter
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
)
from aaos.integrations.google_calendar.client import (
    GoogleCalendarClient,
    GoogleCalendarClientFactory,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
    calendar_target,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import (
    OperationRecordORM,
    PreparedActionORM,
)
from aaos.persistence.database import (
    create_database_engine,
    create_session_factory,
)

from .manifest import FROZEN_MANIFEST_SHA256

_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_DEFAULT_DERIVATION_VERSION = "calendar-provider-id-v1"

_REAL_ACCEPTANCE_PYTEST_FORBIDDEN = "REAL_ACCEPTANCE_PYTEST_FORBIDDEN"
_REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED = "REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class TrustedRealAcceptanceAuthorization:
    authorization_id: str
    issued_at: datetime
    manifest_fingerprint: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.authorization_id, str)
            or not self.authorization_id
            or len(self.authorization_id) > 200
        ):
            raise ValueError("authorization_id is invalid")

        if (
            not isinstance(self.issued_at, datetime)
            or self.issued_at.tzinfo is None
            or self.issued_at.utcoffset() is None
        ):
            raise ValueError("issued_at must be timezone-aware")

        if (
            not isinstance(self.manifest_fingerprint, str)
            or len(self.manifest_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.manifest_fingerprint)
        ):
            raise ValueError("manifest_fingerprint is invalid")


@dataclass(frozen=True, slots=True)
class RealAcceptanceReport:
    schema_version: str
    action_id: str
    operation_id: str
    action_state: str
    operation_state: str
    provider_kind: str
    started_at: datetime
    completed_at: datetime
    request_sha256: str
    prepared_action_sha256: str


def _policy() -> PermissionPolicy:
    return PermissionPolicy.from_values((PermissionClass.LOCAL_STATE_WRITE,))


def _definition() -> Phase6ActionDefinition:
    def validate(
        arguments: Mapping[str, JSONValue],
    ) -> Mapping[str, JSONValue]:
        required = {
            "summary",
            "start",
            "end",
            "timezone",
            "description",
            "location",
        }

        if set(arguments) != required:
            raise ValueError("calendar arguments do not match the closed schema")

        summary = arguments["summary"]
        start = arguments["start"]
        end = arguments["end"]
        timezone_name = arguments["timezone"]
        description = arguments["description"]
        location = arguments["location"]

        if not isinstance(summary, str):
            raise TypeError("summary must be a string")
        if not isinstance(start, str):
            raise TypeError("start must be a string")
        if not isinstance(end, str):
            raise TypeError("end must be a string")
        if not isinstance(timezone_name, str):
            raise TypeError("timezone must be a string")
        if description is not None and not isinstance(description, str):
            raise TypeError("description must be a string or null")
        if location is not None and not isinstance(location, str):
            raise TypeError("location must be a string or null")

        canonical = CanonicalCalendarEvent._create(
            summary,
            start,
            end,
            timezone_name,
            description,
            location,
        )

        return canonical_event_arguments(canonical)

    return Phase6ActionDefinition(
        action_type=ActionType("CREATE_EVENT"),
        tool_name="calendar_create_event",
        tool_schema_version="phase6b-calendar-v1",
        tool_definition_fingerprint="6" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="phase5-v1",
        policy_fingerprint="7" * 64,
        timeout_seconds=10,
        argument_validator=validate,
    )


def _canonical_request_sha256(
    canonical: CanonicalCalendarEvent,
) -> str:
    encoded = json.dumps(
        canonical_event_arguments(canonical),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _validate_authorization(
    authorization: TrustedRealAcceptanceAuthorization | None,
) -> TrustedRealAcceptanceAuthorization:
    if not isinstance(
        authorization,
        TrustedRealAcceptanceAuthorization,
    ):
        raise RuntimeError(_REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED)

    if authorization.manifest_fingerprint != FROZEN_MANIFEST_SHA256:
        raise RuntimeError(_REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED)

    return authorization


def _adapter(
    repository: ApprovalRepository,
    config: GoogleCalendarConfig,
    client: GoogleCalendarClient,
    *,
    derivation_version: str,
) -> GoogleCalendarAdapter:
    def load_expected(
        operation_id: OperationId,
    ):
        return load_executable_action(
            repository,
            operation_id,
        )

    return GoogleCalendarAdapter(
        client,
        config,
        derivation_version=derivation_version,
        expected_action_loader=load_expected,
    )


def _durable_operation_snapshot(
    repository: ApprovalRepository,
    operation_id: OperationId,
) -> tuple[ActionState, OperationState, int]:
    with repository.transaction() as session:
        action_state, operation_state = read_states(
            session,
            operation_id,
        )
        operation = session.get(
            OperationRecordORM,
            operation_id.value,
        )
        if operation is None:
            raise RuntimeError("REAL_ACCEPTANCE_OPERATION_MISSING")
        revision = operation.revision

    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RuntimeError("REAL_ACCEPTANCE_OPERATION_REVISION_INVALID")

    return action_state, operation_state, revision


def _durable_report(
    *,
    repository: ApprovalRepository,
    action_id,
    operation_id: OperationId,
    started_at: datetime,
    completed_at: datetime,
    request_sha256: str,
    prepared_action_sha256: str,
) -> RealAcceptanceReport:
    action_state, operation_state, _ = _durable_operation_snapshot(
        repository,
        operation_id,
    )

    return RealAcceptanceReport(
        schema_version="phase6b-real-acceptance-v1",
        action_id=action_id.value,
        operation_id=operation_id.value,
        action_state=action_state.value,
        operation_state=operation_state.value,
        provider_kind="google-calendar",
        started_at=started_at,
        completed_at=completed_at,
        request_sha256=request_sha256,
        prepared_action_sha256=prepared_action_sha256,
    )


def _run_real_acceptance_with_client(
    *,
    authorization: TrustedRealAcceptanceAuthorization,
    request: CalendarCreateRequest,
    repository: ApprovalRepository,
    config: GoogleCalendarConfig,
    client: GoogleCalendarClient,
    approver_context: TrustedApproverContext,
    now: datetime,
    derivation_version: str = _DEFAULT_DERIVATION_VERSION,
) -> RealAcceptanceReport:
    _validate_authorization(authorization)

    if not isinstance(request, CalendarCreateRequest):
        raise TypeError("request must be a CalendarCreateRequest")
    if not isinstance(repository, ApprovalRepository):
        raise TypeError("repository must be an ApprovalRepository")
    if not isinstance(config, GoogleCalendarConfig):
        raise TypeError("config must be a GoogleCalendarConfig")
    if not isinstance(
        approver_context,
        TrustedApproverContext,
    ):
        raise TypeError("approver_context must be a TrustedApproverContext")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    canonical = canonicalize_calendar_request(
        request,
        now=now,
    )

    # Task 7 is deliberately a tiny future event.
    start = datetime.fromisoformat(canonical.start.replace("Z", "+00:00"))
    end = datetime.fromisoformat(canonical.end.replace("Z", "+00:00"))
    duration = end - start

    if duration != timedelta(minutes=15):
        raise ValueError("real acceptance duration must be exactly 15 minutes")

    target = calendar_target(config)
    permission_policy = _policy()
    definition = _definition()

    prepared = prepare_action(
        repository,
        definition,
        actor_identity=ActorIdentity("phase6b-real-acceptance-operator"),
        target_identity=target,
        arguments=canonical_event_arguments(canonical),
        remote_precondition=RemotePrecondition(
            "google-calendar",
            target.resource,
            derivation_version,
            "absent",
        ),
        action_expires_at=now + timedelta(minutes=10),
        capability_policy=permission_policy,
        now=now,
    )

    requested = request_approval(
        repository,
        prepared.action_id,
        approver_context,
        now + timedelta(minutes=5),
        now + timedelta(seconds=1),
    )

    approve_approval(
        repository,
        requested.approval_id,
        approver_context,
        now + timedelta(seconds=2),
    )

    claimed = claim_action(
        repository,
        prepared.action_id,
        capability_policy=permission_policy,
        definition=definition,
        now=now + timedelta(seconds=3),
        claim_identity=ClaimIdentity("phase6b-real-acceptance-worker"),
    )

    if not claimed.claimed or claimed.operation_id is None:
        raise RuntimeError("REAL_ACCEPTANCE_CLAIM_FAILED")

    operation_id = claimed.operation_id

    calendar_adapter = _adapter(
        repository,
        config,
        client,
        derivation_version=derivation_version,
    )

    dispatch_result = dispatch_claimed_operation(
        repository,
        calendar_adapter,
        operation_id,
        capability_policy=permission_policy,
        definition=definition,
        now=now + timedelta(seconds=4),
    )

    if not dispatch_result.adapter_invoked or dispatch_result.adapter_outcome is None:
        return _durable_report(
            repository=repository,
            action_id=prepared.action_id,
            operation_id=operation_id,
            started_at=now,
            completed_at=now + timedelta(seconds=4),
            request_sha256=_canonical_request_sha256(canonical),
            prepared_action_sha256=(prepared.prepared_action_sha256),
        )

    (
        executing_action_state,
        executing_operation_state,
        executing_revision,
    ) = _durable_operation_snapshot(
        repository,
        operation_id,
    )

    if (
        executing_action_state is not ActionState.EXECUTING
        or executing_operation_state is not OperationState.EXECUTING
    ):
        raise RuntimeError("REAL_ACCEPTANCE_EXECUTING_FENCE_MISSING")

    recorded = record_adapter_outcome(
        repository,
        operation_id,
        expected_revision=executing_revision,
        outcome=dispatch_result.adapter_outcome,
    )

    # Unknown provider outcomes are never retried. They get one
    # read-only reconciliation through the same Calendar adapter.
    if recorded.committed and recorded.operation_state is OperationState.UNKNOWN_OUTCOME:
        (
            _unknown_action_state,
            unknown_operation_state,
            unknown_revision,
        ) = _durable_operation_snapshot(
            repository,
            operation_id,
        )

        if unknown_operation_state is not OperationState.UNKNOWN_OUTCOME:
            raise RuntimeError("REAL_ACCEPTANCE_UNKNOWN_STATE_DRIFT")

        reconcile_operation(
            repository,
            calendar_adapter,
            operation_id,
            expected_revision=unknown_revision,
            now=now + timedelta(seconds=6),
        )

    return _durable_report(
        repository=repository,
        action_id=prepared.action_id,
        operation_id=operation_id,
        started_at=now,
        completed_at=now + timedelta(seconds=6),
        request_sha256=_canonical_request_sha256(canonical),
        prepared_action_sha256=prepared.prepared_action_sha256,
    )


def _required_operator_value(name: str) -> str:
    value = os.environ.get(name)

    if (
        value is None
        or not value
        or len(value) > 4096
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED")

    return value


def _stable_acceptance_root() -> Path:
    # Task 7 is a Windows operator acceptance path.
    #
    # Resolve Local AppData through the Windows Shell API rather than
    # trusting the caller-controlled LOCALAPPDATA process variable.
    if os.name != "nt":
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED")

    buffer = ctypes.create_unicode_buffer(32768)

    try:
        result = ctypes.windll.shell32.SHGetFolderPathW(
            None,
            0x001C,  # CSIDL_LOCAL_APPDATA
            None,
            0,
            buffer,
        )
    except (AttributeError, OSError):
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED") from None

    if result != 0 or not buffer.value:
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED")

    try:
        base = Path(buffer.value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED") from None

    return base / "aaos-phase6b-real-acceptance"


def _trusted_operator_context() -> tuple[
    GoogleCalendarConfig,
    TrustedApproverContext,
    Path,
]:
    calendar_id = _required_operator_value("AAOS_PHASE6B_GOOGLE_CALENDAR_ID")
    service_identity = _required_operator_value("AAOS_PHASE6B_GOOGLE_SERVICE_IDENTITY")
    credential_path = _required_operator_value("AAOS_PHASE6B_GOOGLE_CREDENTIAL_PATH")
    approver_identity = _required_operator_value("AAOS_PHASE6B_APPROVER_IDENTITY")
    approver_context_id = _required_operator_value("AAOS_PHASE6B_APPROVER_CONTEXT_ID")

    try:
        credential = Path(credential_path)

        if not credential.is_absolute():
            raise ValueError

        root = _stable_acceptance_root()
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED") from None

    config = GoogleCalendarConfig(
        calendar_id=calendar_id,
        expected_service_identity=service_identity,
        oauth_scope=_EVENTS_SCOPE,
        credential_path=str(credential),
        provider_adapter_version="calendar-adapter-v1",
        enabled=True,
    )

    approver = TrustedApproverContext(
        approver_identity,
        approver_context_id,
    )

    return config, approver, root


@dataclass(frozen=True, slots=True)
class _ExistingAcceptance:
    operation_id: OperationId
    action_id: ActionId
    operation_state: OperationState
    revision: int
    prepared_action_sha256: str


def _existing_acceptance(
    repository: ApprovalRepository,
) -> _ExistingAcceptance | None:
    with repository.transaction() as session:
        rows = tuple(session.execute(select(OperationRecordORM)).scalars())

        if not rows:
            return None

        if len(rows) != 1:
            raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID")

        row = rows[0]

        prepared = session.get(
            PreparedActionORM,
            row.action_id,
        )

        if prepared is None:
            raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID")

        try:
            operation_id = OperationId(row.operation_id)
            action_id = ActionId(row.action_id)
            operation_state = OperationState(row.state)
        except (TypeError, ValueError):
            raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID") from None

        revision = row.revision

        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID")

        prepared_hash = prepared.prepared_action_sha256

        if not isinstance(prepared_hash, str) or len(prepared_hash) != 64:
            raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID")

        return _ExistingAcceptance(
            operation_id=operation_id,
            action_id=action_id,
            operation_state=operation_state,
            revision=revision,
            prepared_action_sha256=prepared_hash,
        )


def _durable_request_hash(
    repository: ApprovalRepository,
    operation_id: OperationId,
) -> str:
    executable = load_executable_action(
        repository,
        operation_id,
    )

    try:
        parsed = json.loads(executable.canonical_arguments.decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID") from None

    encoded = json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _claim_real_acceptance_once(root: Path) -> None:
    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    claim_path = root / "real-acceptance.claim"

    try:
        descriptor = os.open(
            claim_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        raise RuntimeError("REAL_ACCEPTANCE_ALREADY_ATTEMPTED") from None

    try:
        os.write(
            descriptor,
            b"phase6b-real-acceptance-claimed\n",
        )
    finally:
        os.close(descriptor)


def _recover_existing_acceptance(
    *,
    repository: ApprovalRepository,
    existing: _ExistingAcceptance,
    config: GoogleCalendarConfig,
    client: GoogleCalendarClient,
    now: datetime,
) -> RealAcceptanceReport:
    current = existing

    # A process may have died after the provider accepted the write
    # but before its adapter result reached durable Phase 6A state.
    #
    # Recovery must NEVER call dispatch or execute again.
    if current.operation_state is OperationState.EXECUTING:
        recovery = recover_inflight_operation(
            repository,
            current.operation_id,
            now=now,
        )

        if recovery.execute_calls != 0:
            raise RuntimeError("REAL_ACCEPTANCE_RECOVERY_WRITE_FORBIDDEN")

        refreshed = _existing_acceptance(repository)

        if refreshed is None:
            raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID")

        current = refreshed

    if current.operation_state not in {
        OperationState.UNKNOWN_OUTCOME,
        OperationState.RECONCILIATION_PENDING,
    }:
        # CLAIMED / NOT_DISPATCHED / terminal states never create a
        # second provider operation from the restart path.
        raise RuntimeError("REAL_ACCEPTANCE_ALREADY_ATTEMPTED")

    calendar_adapter = _adapter(
        repository,
        config,
        client,
        derivation_version=_DEFAULT_DERIVATION_VERSION,
    )

    reconciliation = reconcile_operation(
        repository,
        calendar_adapter,
        current.operation_id,
        expected_revision=current.revision,
        now=now + timedelta(seconds=1),
    )

    del reconciliation

    final = _existing_acceptance(repository)

    if final is None:
        raise RuntimeError("REAL_ACCEPTANCE_STATE_INVALID")

    return _durable_report(
        repository=repository,
        action_id=final.action_id,
        operation_id=final.operation_id,
        started_at=now,
        completed_at=now + timedelta(seconds=1),
        request_sha256=_durable_request_hash(
            repository,
            final.operation_id,
        ),
        prepared_action_sha256=(final.prepared_action_sha256),
    )


def run_real_acceptance(
    *,
    authorization: TrustedRealAcceptanceAuthorization,
    request: CalendarCreateRequest,
) -> RealAcceptanceReport:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(_REAL_ACCEPTANCE_PYTEST_FORBIDDEN)

    _validate_authorization(authorization)

    (
        config,
        approver_context,
        acceptance_root,
    ) = _trusted_operator_context()

    database_path = acceptance_root / "phase6b-real-acceptance.sqlite"

    engine = create_database_engine(database_path)

    try:
        Base.metadata.create_all(engine)

        repository = ApprovalRepository(create_session_factory(engine))

        existing = _existing_acceptance(repository)

        if existing is not None:
            # Terminal and non-uncertain prior states fail before real
            # client construction.
            if existing.operation_state not in {
                OperationState.EXECUTING,
                OperationState.UNKNOWN_OUTCOME,
                OperationState.RECONCILIATION_PENDING,
            }:
                raise RuntimeError("REAL_ACCEPTANCE_ALREADY_ATTEMPTED")

            # This branch can perform provider READS only through
            # GoogleCalendarAdapter.reconcile().
            client = GoogleCalendarClientFactory.build(config)

            return _recover_existing_acceptance(
                repository=repository,
                existing=existing,
                config=config,
                client=client,
                now=datetime.now(ZoneInfo("UTC")),
            )

        # New acceptance path. The atomic claim is still the concurrency
        # fence preventing two fresh processes from reaching dispatch.
        client = GoogleCalendarClientFactory.build(config)

        _claim_real_acceptance_once(acceptance_root)

        current = datetime.now(ZoneInfo("UTC"))

        return _run_real_acceptance_with_client(
            authorization=authorization,
            request=request,
            repository=repository,
            config=config,
            client=client,
            approver_context=approver_context,
            now=current,
            derivation_version=(_DEFAULT_DERIVATION_VERSION),
        )
    finally:
        engine.dispose()


_STOP_GATE = "HUMAN_AUTHORIZATION_FOR_PHASE6B_REAL_GOOGLE_ACCEPTANCE"


def _trusted_cli_authorization() -> TrustedRealAcceptanceAuthorization:
    authorization_id = _required_operator_value("AAOS_PHASE6B_REAL_AUTHORIZATION_ID")
    issued_at_raw = _required_operator_value("AAOS_PHASE6B_REAL_AUTHORIZATION_ISSUED_AT")
    manifest_fingerprint = _required_operator_value("AAOS_PHASE6B_REAL_MANIFEST_FINGERPRINT")

    try:
        issued_at = datetime.fromisoformat(issued_at_raw.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError("REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED") from None

    authorization = TrustedRealAcceptanceAuthorization(
        authorization_id=authorization_id,
        issued_at=issued_at,
        manifest_fingerprint=manifest_fingerprint,
    )

    return _validate_authorization(authorization)


def _build_cli_acceptance_request() -> CalendarCreateRequest:
    timezone_name = _required_operator_value("AAOS_PHASE6B_TIMEZONE")

    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise RuntimeError("REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED") from None

    now = datetime.now(zone).replace(microsecond=0)
    start = now + timedelta(minutes=10)
    end = start + timedelta(minutes=15)

    unique_stamp = start.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    return CalendarCreateRequest(
        summary=("AAOS Phase 6B Acceptance " + unique_stamp),
        start=start,
        end=end,
        timezone=timezone_name,
        description="AAOS_PHASE6B_REAL_ACCEPTANCE",
        location=None,
    )


def _safe_report_json(
    report: RealAcceptanceReport,
) -> str:
    return json.dumps(
        {
            "schema_version": report.schema_version,
            "action_id": report.action_id,
            "operation_id": report.operation_id,
            "action_state": report.action_state,
            "operation_state": report.operation_state,
            "provider_kind": report.provider_kind,
            "started_at": report.started_at.isoformat(),
            "completed_at": report.completed_at.isoformat(),
            "request_sha256": report.request_sha256,
            "prepared_action_sha256": (report.prepared_action_sha256),
        },
        sort_keys=True,
    )


def main() -> int:
    # Invoking the module without explicit authorization is always
    # a safe, no-provider-write stop.
    try:
        authorization = _trusted_cli_authorization()
        request = _build_cli_acceptance_request()
    except (
        RuntimeError,
        TypeError,
        ValueError,
    ):
        print(f"STOP_GATE={_STOP_GATE}")
        return 2

    try:
        report = run_real_acceptance(
            authorization=authorization,
            request=request,
        )
    except RuntimeError as error:
        safe_codes = {
            "REAL_ACCEPTANCE_ALREADY_ATTEMPTED",
            "REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED",
            "REAL_ACCEPTANCE_OPERATOR_CONTEXT_REQUIRED",
            "REAL_ACCEPTANCE_PYTEST_FORBIDDEN",
        }

        code = str(error) if str(error) in safe_codes else "REAL_ACCEPTANCE_FAILED"

        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": code,
                },
                sort_keys=True,
            )
        )
        return 1
    except Exception:
        # Never print provider/credential exception text.
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": "REAL_ACCEPTANCE_FAILED",
                },
                sort_keys=True,
            )
        )
        return 1

    print(_safe_report_json(report))

    if report.action_state == "CONFIRMED_SUCCESS" and report.operation_state == "CONFIRMED_SUCCESS":
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
