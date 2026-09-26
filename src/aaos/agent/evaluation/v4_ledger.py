"""Durable one-time state for the sealed V4 held-out evaluation."""

from __future__ import annotations

import importlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterator

LEDGER_SCHEMA_VERSION = "phase5-v4-heldout-ledger-v1"
EVALUATION_VERSION = "phase5-agent-benchmark-v4"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESERVATION = re.compile(r"^[0-9a-f]{32}$")
_LEDGER_KEYS = {
    "aggregate_report_sha256",
    "candidate_sha",
    "completion_status",
    "consumed_at_utc",
    "evaluation_version",
    "execution_count",
    "last_presemantic_release_at_utc",
    "reserved_at_utc",
    "reservation_id",
    "schema_version",
    "state",
}


class HeldoutState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"


class HeldoutLedgerError(RuntimeError):
    """A safe fail-closed ledger error with no path or record content."""


@dataclass(frozen=True, slots=True)
class HeldoutReservation:
    reservation_id: str
    candidate_sha: str


@dataclass(frozen=True, slots=True)
class PreSemanticFailureEvidence:
    """Trusted evidence produced only by the runner's pre-semantic preflight path."""

    preflight_failed: bool
    protected_case_materialized: bool
    model_or_tool_execution_started: bool
    scoring_started: bool
    case_result_produced: bool

    def proves_no_semantic_access(self) -> bool:
        return (
            self.preflight_failed
            and not self.protected_case_materialized
            and not self.model_or_tool_execution_started
            and not self.scoring_started
            and not self.case_result_produced
        )


@dataclass(frozen=True, slots=True)
class HeldoutLedgerStatus:
    state: HeldoutState
    candidate_sha: str | None
    reservation_id: str | None
    execution_count: int
    reserved_at_utc: str | None
    consumed_at_utc: str | None
    aggregate_report_sha256: str | None
    completion_status: str
    last_presemantic_release_at_utc: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "aggregate_report_sha256": self.aggregate_report_sha256,
            "candidate_sha": self.candidate_sha,
            "completion_status": self.completion_status,
            "consumed_at_utc": self.consumed_at_utc,
            "evaluation_version": EVALUATION_VERSION,
            "execution_count": self.execution_count,
            "last_presemantic_release_at_utc": self.last_presemantic_release_at_utc,
            "reserved_at_utc": self.reserved_at_utc,
            "reservation_id": self.reservation_id,
            "schema_version": LEDGER_SCHEMA_VERSION,
            "state": self.state.value,
        }


class V4HeldoutLedger:
    """Cross-process locked JSON ledger with atomic durable state replacement."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not self.path.name or self.path.name in {".", ".."}:
            raise HeldoutLedgerError("ledger path is invalid")

    def status(self) -> HeldoutLedgerStatus:
        """Read one atomic snapshot without creating files or directories."""

        try:
            if self.path.is_symlink() or self.path.parent.is_symlink():
                raise HeldoutLedgerError("ledger storage is ambiguous; failing closed")
            return self._read_locked()
        except HeldoutLedgerError:
            raise
        except Exception:
            raise HeldoutLedgerError("ledger status unavailable; failing closed") from None

    def reserve(self, candidate_sha: str) -> HeldoutReservation:
        normalized_sha = self._validate_sha(candidate_sha)
        try:
            with self._exclusive_lock():
                current = self._read_locked()
                if current.state is not HeldoutState.AVAILABLE:
                    raise HeldoutLedgerError("held-out permit unavailable; failing closed")
                reservation = HeldoutReservation(uuid.uuid4().hex, normalized_sha)
                reserved = HeldoutLedgerStatus(
                    state=HeldoutState.RESERVED,
                    candidate_sha=normalized_sha,
                    reservation_id=reservation.reservation_id,
                    execution_count=0,
                    reserved_at_utc=self._now(),
                    consumed_at_utc=None,
                    aggregate_report_sha256=None,
                    completion_status="reserved",
                    last_presemantic_release_at_utc=current.last_presemantic_release_at_utc,
                )
                self._write_locked(reserved)
                return reservation
        except HeldoutLedgerError:
            raise
        except Exception:
            raise HeldoutLedgerError("held-out permit acquisition failed; failing closed") from None

    def consume(self, reservation_id: str) -> None:
        normalized_id = self._validate_reservation_id(reservation_id)
        try:
            with self._exclusive_lock():
                current = self._read_locked()
                if (
                    current.state is not HeldoutState.RESERVED
                    or current.reservation_id != normalized_id
                    or current.completion_status != "reserved"
                ):
                    raise HeldoutLedgerError("held-out reservation is not actionable")
                consumed = replace(
                    current,
                    state=HeldoutState.CONSUMED,
                    execution_count=1,
                    consumed_at_utc=self._now(),
                    completion_status="running",
                )
                self._write_locked(consumed)
        except HeldoutLedgerError:
            raise
        except Exception:
            raise HeldoutLedgerError("held-out consumption could not be persisted") from None

    def finish(
        self,
        reservation_id: str,
        *,
        completion_status: str,
        aggregate_report_sha256: str | None,
    ) -> None:
        normalized_id = self._validate_reservation_id(reservation_id)
        if completion_status not in {"complete", "failed", "unknown"}:
            raise HeldoutLedgerError("completion status is invalid")
        if aggregate_report_sha256 is not None and not _SHA256.fullmatch(aggregate_report_sha256):
            raise HeldoutLedgerError("aggregate report identity is invalid")
        if completion_status == "complete" and aggregate_report_sha256 is None:
            raise HeldoutLedgerError("completed evaluation requires an aggregate identity")
        try:
            with self._exclusive_lock():
                current = self._read_locked()
                if (
                    current.state is not HeldoutState.CONSUMED
                    or current.reservation_id != normalized_id
                    or current.completion_status != "running"
                ):
                    raise HeldoutLedgerError("consumed evaluation is not actionable")
                self._write_locked(
                    replace(
                        current,
                        completion_status=completion_status,
                        aggregate_report_sha256=aggregate_report_sha256,
                    )
                )
        except HeldoutLedgerError:
            raise
        except Exception:
            raise HeldoutLedgerError("held-out completion could not be persisted") from None

    def release_presemantic(
        self,
        reservation_id: str,
        evidence: PreSemanticFailureEvidence,
    ) -> None:
        normalized_id = self._validate_reservation_id(reservation_id)
        if (
            not isinstance(evidence, PreSemanticFailureEvidence)
            or not evidence.proves_no_semantic_access()
        ):
            raise HeldoutLedgerError("pre-semantic release lacks trusted proof")
        try:
            with self._exclusive_lock():
                current = self._read_locked()
                if (
                    current.state is not HeldoutState.RESERVED
                    or current.reservation_id != normalized_id
                ):
                    raise HeldoutLedgerError("reservation cannot be released")
                available = replace(
                    self._available(),
                    last_presemantic_release_at_utc=self._now(),
                )
                self._write_locked(available)
        except HeldoutLedgerError:
            raise
        except Exception:
            raise HeldoutLedgerError("pre-semantic release could not be persisted") from None

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        if self.path.is_symlink() or lock_path.is_symlink() or self.path.parent.is_symlink():
            raise HeldoutLedgerError("ledger storage is ambiguous; failing closed")
        descriptor: int | None = None
        locked = False
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            yield
        finally:
            if descriptor is not None:
                try:
                    if locked and os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    elif locked:
                        fcntl = importlib.import_module("fcntl")
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _read_locked(self) -> HeldoutLedgerStatus:
        if not self.path.exists():
            return self._available()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            raise HeldoutLedgerError("ledger is corrupt or unreadable; failing closed") from None
        if not isinstance(payload, dict) or set(payload) != _LEDGER_KEYS:
            raise HeldoutLedgerError("ledger schema is corrupt; failing closed")
        try:
            if (
                payload["schema_version"] != LEDGER_SCHEMA_VERSION
                or payload["evaluation_version"] != EVALUATION_VERSION
            ):
                raise ValueError
            state = HeldoutState(payload["state"])
            candidate_sha = payload["candidate_sha"]
            if candidate_sha is not None and not (
                isinstance(candidate_sha, str) and _SHA1.fullmatch(candidate_sha)
            ):
                raise ValueError
            reservation_id = payload["reservation_id"]
            if reservation_id is not None and not (
                isinstance(reservation_id, str) and _RESERVATION.fullmatch(reservation_id)
            ):
                raise ValueError
            execution_count = payload["execution_count"]
            if (
                isinstance(execution_count, bool)
                or not isinstance(execution_count, int)
                or execution_count not in {0, 1}
            ):
                raise ValueError
            timestamps = (
                payload["reserved_at_utc"],
                payload["consumed_at_utc"],
                payload["last_presemantic_release_at_utc"],
            )
            for timestamp in timestamps:
                if timestamp is not None:
                    parsed = datetime.fromisoformat(timestamp)
                    if parsed.tzinfo is None:
                        raise ValueError
            digest = payload["aggregate_report_sha256"]
            if digest is not None and not (isinstance(digest, str) and _SHA256.fullmatch(digest)):
                raise ValueError
            completion_status = payload["completion_status"]
            if not isinstance(completion_status, str):
                raise ValueError
            record = HeldoutLedgerStatus(
                state=state,
                candidate_sha=candidate_sha,
                reservation_id=reservation_id,
                execution_count=execution_count,
                reserved_at_utc=payload["reserved_at_utc"],
                consumed_at_utc=payload["consumed_at_utc"],
                aggregate_report_sha256=digest,
                completion_status=completion_status,
                last_presemantic_release_at_utc=payload["last_presemantic_release_at_utc"],
            )
            self._validate_invariants(record)
            return record
        except (TypeError, ValueError, KeyError):
            raise HeldoutLedgerError(
                "ledger values are corrupt or ambiguous; failing closed"
            ) from None

    def _write_locked(self, record: HeldoutLedgerStatus) -> None:
        payload = json.dumps(
            record.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".v4-heldout-", suffix=".tmp", dir=self.path.parent
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name != "nt":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception:
            raise HeldoutLedgerError("ledger update failed; state is unknown and blocked") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _validate_invariants(record: HeldoutLedgerStatus) -> None:
        if record.state is HeldoutState.AVAILABLE:
            if (
                record.candidate_sha is not None
                or record.reservation_id is not None
                or record.execution_count != 0
                or record.reserved_at_utc is not None
                or record.consumed_at_utc is not None
                or record.aggregate_report_sha256 is not None
                or record.completion_status != "not_started"
            ):
                raise ValueError
        elif record.state is HeldoutState.RESERVED:
            if (
                record.candidate_sha is None
                or record.reservation_id is None
                or record.execution_count != 0
                or record.reserved_at_utc is None
                or record.consumed_at_utc is not None
                or record.aggregate_report_sha256 is not None
                or record.completion_status != "reserved"
            ):
                raise ValueError
        elif record.state is HeldoutState.CONSUMED:
            if (
                record.candidate_sha is None
                or record.reservation_id is None
                or record.execution_count != 1
                or record.reserved_at_utc is None
                or record.consumed_at_utc is None
                or record.completion_status not in {"running", "complete", "failed", "unknown"}
                or (
                    record.completion_status == "complete"
                    and record.aggregate_report_sha256 is None
                )
            ):
                raise ValueError
        else:
            raise ValueError

    @staticmethod
    def _available() -> HeldoutLedgerStatus:
        return HeldoutLedgerStatus(
            state=HeldoutState.AVAILABLE,
            candidate_sha=None,
            reservation_id=None,
            execution_count=0,
            reserved_at_utc=None,
            consumed_at_utc=None,
            aggregate_report_sha256=None,
            completion_status="not_started",
            last_presemantic_release_at_utc=None,
        )

    @staticmethod
    def _validate_sha(value: str) -> str:
        if not isinstance(value, str) or not _SHA1.fullmatch(value):
            raise HeldoutLedgerError("candidate identity is invalid")
        return value

    @staticmethod
    def _validate_reservation_id(value: str) -> str:
        if not isinstance(value, str) or not _RESERVATION.fullmatch(value):
            raise HeldoutLedgerError("reservation identity is invalid")
        return value

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
