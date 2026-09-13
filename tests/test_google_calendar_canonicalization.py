from datetime import datetime, timedelta, timezone
import pytest
from aaos.approval.contracts import OperationId
from aaos.integrations.google_calendar.contracts import CalendarCreateRequest, CanonicalCalendarEvent
from aaos.integrations.google_calendar.canonicalization import *

NOW=datetime(2026,9,14,12,tzinfo=timezone.utc)
def req(**kw): return CalendarCreateRequest("Meeting", NOW+timedelta(hours=1), NOW+timedelta(hours=2), "UTC", **kw)
def test_canonical_rfc3339_is_stable(): assert canonicalize_calendar_request(req(), now=NOW).start.endswith("Z")
def test_absent_optionals_have_one_representation(): assert canonical_event_arguments(canonicalize_calendar_request(req(), now=NOW))["description"] is None
def test_summary_limit():
 with pytest.raises(ValueError): canonicalize_calendar_request(CalendarCreateRequest("x"*101, NOW+timedelta(hours=1), NOW+timedelta(hours=2), "UTC"), now=NOW)
def test_provider_id_is_deterministic(): assert derive_provider_event_id(OperationId("op"), derivation_version="v1")==derive_provider_event_id(OperationId("op"), derivation_version="v1")
def test_different_operation_ids_produce_different_provider_ids(): assert derive_provider_event_id(OperationId("op"), derivation_version="v1") != derive_provider_event_id(OperationId("op2"), derivation_version="v1")
