"""Bounded, process-local trusted references for multi-turn agent requests."""

from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable
from uuid import UUID, uuid4

from aaos.moodle.changes import ChangeField, ChangeKind, EntityType

from .intent_router import has_bilingual_write_intent
from .study_plan_contracts import StudyPlanContext

MAX_CONVERSATION_TURNS = 8
MAX_CONTEXT_ENTITIES = 16
MAX_CONTEXT_ENTITY_TITLE_CHARACTERS = 2048
MAX_CONTEXT_HINT_TITLE_CHARACTERS = 256
MAX_SERIALIZED_CONTEXT_BYTES = 4096
MAX_CONVERSATION_SESSIONS = 128
CONVERSATION_SESSION_TTL_SECONDS = 30 * 60

_SOURCE_REFERENCE = re.compile(r"^read-[1-8]$")
_SOURCE_TOOL = re.compile(
    r"^(?:academic\.(?:list_courses|get_course|list_assignments|get_assignment|list_documents)"
    r"|study_task\.(?:list|get|create|update|complete|acknowledge_deadline_change)"
    r"|moodle\.list_changes)$"
)
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}
_ORDINAL_REFERENCE = re.compile(
    r"\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"\s+(?:(one)|(course|assignment|document|task|change))\b"
)
_ENTITY_ID_REFERENCE = re.compile(r"\b(course|assignment|document|task)\s*#?\s*([1-9][0-9]{0,8})\b")
_TYPED_REFERENCE = re.compile(
    r"\bthat\s+(course|assignment|document|task|change)\b|"
    r"\bthis\s+(course|assignment|task|change)\b"
)
_GENERIC_REFERENCE = re.compile(
    r"\b(?:"
    r"(?:about|for|to|with|on|from|in|regarding)\s+(?:it|that)"
    r"|(?:related|relevant)\s+to\s+(?:it|that)"
    r"|(?:explain|describe|summarize|expand\s+on|compare|show|list|find|read|open|repeat|clarify)"
    r"\s+(?:it|that)"
    r"|(?:what|when|where|why|how|is|are|was|were|does|did|can|could|should|would|will|has|have)"
    r"\s+(?:it|that)"
    r"|(?:it|that)(?=\s*[?.!,;:]|$)"
    r"|(?:mark|complete|rename|update|change|set|reprioritize|schedule)\s+(?:it|that|this)\b"
    r"|(?:match|reflect|apply)\s+(?:it|that)\b"
    r"|(?:change|set)\s+its\s+(?:priority|title|name)\b"
    r")\b"
)
# A named topic in this message is query text, not a trusted record alias.
_LOCAL_TOPIC_PREFIX = re.compile(
    r"^what\s+(?:is|are)\s+(?P<topic>[^?!.;:]+)[?!.]\s*(?:please\s+)?$"
)
_LOCAL_EXPLANATION = re.compile(r"(?:explain|describe|summarize|clarify|expand\s+on)\s+it")
_DEICTIC_REFERENCE = re.compile(r"\b(?:it|its|this|that|these|those|them|they)\b")
_RECORD_OR_DEICTIC_TOPIC = re.compile(
    r"\b(?:it|this|that|these|those|them|they|one|my|our|your|their|his|her|its|"
    r"courses?|assignments?|documents?|tasks?|changes?|deadlines?)\b"
)
_CHANGE_REFERENCE = re.compile(
    r"\b(?:that|this|the)\s+"
    r"(?:(deadline|due\s+date|document|assignment|course|moodle)\s+)?changes?\b"
)
_CHANGE_VALUE_REFERENCE = re.compile(
    r"\b(deadlines?|due(?:\s+dates?)?|publication\s+date|document\s+version|course\s+code)\b"
)
_NEW_DOCUMENT_REFERENCE = re.compile(
    r"\b(?:new|latest|updated)\s+(?:document|version)\b|\bnew\s+version\b"
)
_CHANGED_ENTITY_REFERENCE = re.compile(
    r"\b(?:the\s+)?(assignment|document|course)\s+(?:that\s+)?changed\b"
)


class ContextEntityKind(StrEnum):
    COURSE = "course"
    ASSIGNMENT = "assignment"
    DOCUMENT = "document"
    STUDY_TASK = "study_task"
    MOODLE_CHANGE = "moodle_change"


class ContextResolutionStatus(StrEnum):
    NONE = "NONE"
    RESOLVED = "RESOLVED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


class ConversationContextErrorCode(StrEnum):
    UNKNOWN_SESSION = "UNKNOWN_SESSION"
    EXPIRED_SESSION = "EXPIRED_SESSION"
    TURN_LIMIT = "TURN_LIMIT"
    SESSION_BUSY = "SESSION_BUSY"
    SESSION_CAPACITY = "SESSION_CAPACITY"
    INVALID_LEASE = "INVALID_LEASE"


@dataclass(frozen=True, slots=True)
class TrustedMoodleChangeContext:
    """Validated, bounded Phase E change facts retained for one conversation."""

    change_id: str
    from_snapshot_id: int
    to_snapshot_id: int
    entity_type: EntityType
    change_kind: ChangeKind
    changed_fields: tuple[ChangeField, ...]
    before_title: str | None
    after_title: str | None
    before_course_code: str | None
    after_course_code: str | None
    before_due_at: datetime | None
    after_due_at: datetime | None
    before_published_at: datetime | None
    after_published_at: datetime | None
    before_document_version: int | None
    after_document_version: int | None
    detected_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.change_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", self.change_id
        ):
            raise ValueError("change_id must be a bounded trusted identifier")
        for name, snapshot_id in (
            ("from_snapshot_id", self.from_snapshot_id),
            ("to_snapshot_id", self.to_snapshot_id),
        ):
            if (
                isinstance(snapshot_id, bool)
                or not isinstance(snapshot_id, int)
                or snapshot_id <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.entity_type, EntityType) or not isinstance(
            self.change_kind, ChangeKind
        ):
            raise TypeError("change entity and kind must use closed Moodle enums")
        if (
            not isinstance(self.changed_fields, tuple)
            or not self.changed_fields
            or len(self.changed_fields) > len(ChangeField)
            or any(not isinstance(field, ChangeField) for field in self.changed_fields)
            or len(set(self.changed_fields)) != len(self.changed_fields)
        ):
            raise ValueError("changed_fields must be a bounded tuple of unique Moodle fields")
        if self.from_snapshot_id > self.to_snapshot_id or (
            self.from_snapshot_id == self.to_snapshot_id
            and not (
                self.entity_type is EntityType.DOCUMENT
                and ChangeField.DOCUMENT_VERSION in self.changed_fields
            )
        ):
            raise ValueError("change snapshots must be ordered except for document-version changes")
        for name, title_value in (
            ("before_title", self.before_title),
            ("after_title", self.after_title),
            ("before_course_code", self.before_course_code),
            ("after_course_code", self.after_course_code),
        ):
            if title_value is not None and (
                not isinstance(title_value, str) or len(title_value) > 2048
            ):
                raise ValueError(f"{name} must be bounded text or None")
        for name, timestamp_value in (
            ("before_due_at", self.before_due_at),
            ("after_due_at", self.after_due_at),
            ("before_published_at", self.before_published_at),
            ("after_published_at", self.after_published_at),
            ("detected_at", self.detected_at),
        ):
            if timestamp_value is not None and (
                not isinstance(timestamp_value, datetime)
                or timestamp_value.tzinfo is None
                or timestamp_value.utcoffset() is None
            ):
                raise ValueError(f"{name} must be timezone-aware")
            if timestamp_value is not None:
                object.__setattr__(self, name, timestamp_value.astimezone(timezone.utc))
        for name, document_version in (
            ("before_document_version", self.before_document_version),
            ("after_document_version", self.after_document_version),
        ):
            if document_version is not None and (
                isinstance(document_version, bool)
                or not isinstance(document_version, int)
                or document_version <= 0
            ):
                raise ValueError(f"{name} must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class TrustedContextEntity:
    """Canonical identity and provenance copied only from validated tool output."""

    kind: ContextEntityKind
    entity_id: int
    title: str | None
    course_id: int | None
    course_title: str | None
    source_tool: str
    source_ref: str
    source_turn: int
    document_version_id: int | None = None
    change: TrustedMoodleChangeContext | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ContextEntityKind):
            raise TypeError("kind must be a ContextEntityKind")
        if (
            isinstance(self.entity_id, bool)
            or not isinstance(self.entity_id, int)
            or self.entity_id <= 0
        ):
            raise ValueError("entity_id must be a positive integer")
        if self.course_id is not None and (
            isinstance(self.course_id, bool)
            or not isinstance(self.course_id, int)
            or self.course_id <= 0
        ):
            raise ValueError("course_id must be a positive integer or None")
        if self.kind is ContextEntityKind.COURSE and self.course_id not in (None, self.entity_id):
            raise ValueError("a course entity must link to its own canonical ID")
        if self.document_version_id is not None and (
            isinstance(self.document_version_id, bool)
            or not isinstance(self.document_version_id, int)
            or self.document_version_id <= 0
            or self.kind is not ContextEntityKind.DOCUMENT
        ):
            raise ValueError("document_version_id must be positive and belong to a document")
        if self.kind is ContextEntityKind.MOODLE_CHANGE:
            if not isinstance(self.change, TrustedMoodleChangeContext) or self.course_id is None:
                raise ValueError(
                    "Moodle change context requires typed change and course references"
                )
            if self.source_tool != "moodle.list_changes":
                raise ValueError("Moodle change context must come from the change reader")
        elif self.change is not None:
            raise ValueError("change details may only accompany a Moodle change context")
        for name, value in (("title", self.title), ("course_title", self.course_title)):
            if value is not None and (
                not isinstance(value, str) or len(value) > MAX_CONTEXT_ENTITY_TITLE_CHARACTERS
            ):
                raise ValueError(f"{name} must be bounded text or None")
        if not isinstance(self.source_tool, str) or not _SOURCE_TOOL.fullmatch(self.source_tool):
            raise ValueError("source_tool is not a supported academic read tool")
        if not isinstance(self.source_ref, str) or not _SOURCE_REFERENCE.fullmatch(self.source_ref):
            raise ValueError("source_ref must be a bounded academic evidence reference")
        if (
            isinstance(self.source_turn, bool)
            or not isinstance(self.source_turn, int)
            or not (1 <= self.source_turn <= MAX_CONVERSATION_TURNS)
        ):
            raise ValueError("source_turn must be within the bounded session")


@dataclass(frozen=True, slots=True)
class ConversationContextSnapshot:
    conversation_id: UUID
    turn_number: int
    entities: tuple[TrustedContextEntity, ...] = ()
    last_collection: tuple[TrustedContextEntity, ...] = ()
    active_entity: TrustedContextEntity | None = None
    active_course: TrustedContextEntity | None = None
    study_plan: StudyPlanContext | None = None
    # The conversation is about past (archived) courses, so a follow-up that
    # names no course keeps searching them.
    past_focus: bool = False
    # A question AAOS asked the user to complete by naming a course; it is
    # kept for the next turn only.
    pending_question: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, UUID):
            raise TypeError("conversation_id must be a UUID")
        if (
            isinstance(self.turn_number, bool)
            or not isinstance(self.turn_number, int)
            or not (0 <= self.turn_number <= MAX_CONVERSATION_TURNS)
        ):
            raise ValueError("turn_number is outside the bounded session")
        for name, values in (
            ("entities", self.entities),
            ("last_collection", self.last_collection),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > MAX_CONTEXT_ENTITIES
                or any(not isinstance(value, TrustedContextEntity) for value in values)
            ):
                raise ValueError(f"{name} must be a bounded tuple of trusted entities")
        if self.active_entity is not None and not isinstance(
            self.active_entity, TrustedContextEntity
        ):
            raise TypeError("active_entity must be a trusted entity or None")
        if self.active_course is not None and (
            not isinstance(self.active_course, TrustedContextEntity)
            or self.active_course.kind is not ContextEntityKind.COURSE
        ):
            raise TypeError("active_course must be a course entity or None")
        if self.study_plan is not None and not isinstance(self.study_plan, StudyPlanContext):
            raise TypeError("study_plan must be a typed bounded plan or None")


@dataclass(frozen=True, slots=True)
class ConversationContextUpdate:
    entities: tuple[TrustedContextEntity, ...] = ()
    # None preserves the previous ordered collection; an empty tuple clears it.
    last_collection: tuple[TrustedContextEntity, ...] | None = None
    active_entity: TrustedContextEntity | None = None
    active_course: TrustedContextEntity | None = None
    # None preserves the last typed plan reference; no prose is retained.
    study_plan: StudyPlanContext | None = None
    # None preserves the conversation's past-course focus.
    past_focus: bool | None = None
    # Replaced every turn: a waiting question lasts one turn.
    pending_question: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entities, tuple) or any(
            not isinstance(entity, TrustedContextEntity) for entity in self.entities
        ):
            raise TypeError("entities must be a tuple of trusted entities")
        if self.last_collection is not None and (
            not isinstance(self.last_collection, tuple)
            or any(not isinstance(entity, TrustedContextEntity) for entity in self.last_collection)
        ):
            raise TypeError("last_collection must be a tuple of trusted entities or None")
        if self.active_entity is not None and not isinstance(
            self.active_entity, TrustedContextEntity
        ):
            raise TypeError("active_entity must be a trusted entity or None")
        if self.active_course is not None and (
            not isinstance(self.active_course, TrustedContextEntity)
            or self.active_course.kind is not ContextEntityKind.COURSE
        ):
            raise TypeError("active_course must be a course entity or None")
        if self.study_plan is not None and not isinstance(self.study_plan, StudyPlanContext):
            raise TypeError("study_plan must be a typed bounded plan or None")


@dataclass(frozen=True, slots=True)
class ConversationContextLease:
    conversation_id: UUID
    turn_number: int
    token: UUID
    snapshot: ConversationContextSnapshot


@dataclass(frozen=True, slots=True)
class ContextResolution:
    status: ContextResolutionStatus
    entity: TrustedContextEntity | None = None
    context_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ContextResolutionStatus):
            raise TypeError("status must be a ContextResolutionStatus")
        if self.status is ContextResolutionStatus.RESOLVED:
            if not isinstance(self.entity, TrustedContextEntity) or self.context_ref != "ctx-1":
                raise ValueError("a resolved reference requires one trusted entity handle")
        elif self.entity is not None or self.context_ref is not None:
            raise ValueError("unresolved context cannot carry an entity or handle")


class ConversationContextStoreError(RuntimeError):
    def __init__(self, code: ConversationContextErrorCode) -> None:
        if not isinstance(code, ConversationContextErrorCode):
            raise TypeError("code must be a ConversationContextErrorCode")
        self.code = code
        super().__init__(code.value)


@dataclass(slots=True)
class _StoredConversation:
    snapshot: ConversationContextSnapshot
    expires_at: float
    last_access: float
    pending_token: UUID | None = None


class ConversationContextStore:
    """Bounded in-memory store; IDs are opaque handles and state never reaches disk."""

    max_turns = MAX_CONVERSATION_TURNS
    max_entities = MAX_CONTEXT_ENTITIES
    max_sessions = MAX_CONVERSATION_SESSIONS
    session_ttl_seconds = CONVERSATION_SESSION_TTL_SECONDS

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._sessions: dict[UUID, _StoredConversation] = {}

    def begin_turn(self, conversation_id: UUID | None = None) -> ConversationContextLease:
        if conversation_id is not None and not isinstance(conversation_id, UUID):
            raise TypeError("conversation_id must be a UUID or None")
        now = self._now()
        with self._lock:
            self._purge_expired(now)
            if conversation_id is None:
                self._make_capacity(now)
                conversation_id = uuid4()
                empty = ConversationContextSnapshot(conversation_id, 0)
                session = _StoredConversation(empty, now + self.session_ttl_seconds, now)
                self._sessions[conversation_id] = session
            else:
                existing_session = self._sessions.get(conversation_id)
                if existing_session is None:
                    raise ConversationContextStoreError(
                        ConversationContextErrorCode.UNKNOWN_SESSION
                    )
                if existing_session.pending_token is not None:
                    raise ConversationContextStoreError(ConversationContextErrorCode.SESSION_BUSY)
                if existing_session.snapshot.turn_number >= self.max_turns:
                    del self._sessions[conversation_id]
                    raise ConversationContextStoreError(ConversationContextErrorCode.TURN_LIMIT)
                session = existing_session

            token = uuid4()
            turn_number = session.snapshot.turn_number + 1
            session.pending_token = token
            session.last_access = now
            session.expires_at = now + self.session_ttl_seconds
            snapshot = ConversationContextSnapshot(
                conversation_id,
                turn_number,
                session.snapshot.entities,
                session.snapshot.last_collection,
                session.snapshot.active_entity,
                session.snapshot.active_course,
                session.snapshot.study_plan,
                session.snapshot.past_focus,
                session.snapshot.pending_question,
            )
            return ConversationContextLease(conversation_id, turn_number, token, snapshot)

    def finish_turn(
        self,
        lease: ConversationContextLease,
        update: ConversationContextUpdate,
    ) -> ConversationContextSnapshot:
        if not isinstance(lease, ConversationContextLease):
            raise TypeError("lease must be a ConversationContextLease")
        if not isinstance(update, ConversationContextUpdate):
            raise TypeError("update must be a ConversationContextUpdate")
        now = self._now()
        with self._lock:
            session = self._sessions.get(lease.conversation_id)
            self._validate_lease(session, lease, now)
            assert session is not None
            collection = (
                session.snapshot.last_collection
                if update.last_collection is None
                else update.last_collection[: self.max_entities]
            )
            recent_input = (*update.entities, *session.snapshot.entities)
            if update.last_collection is not None:
                # Preserve the exact database order before older, recency-only entities.
                recent_input = (*collection, *recent_input)
            entities = _deduplicate_entities(recent_input)[: self.max_entities]
            active_entity = update.active_entity
            if active_entity is not None and active_entity not in entities:
                active_entity = None
            active_course = update.active_course
            if active_course is not None and active_course not in entities:
                active_course = None
            snapshot = ConversationContextSnapshot(
                lease.conversation_id,
                lease.turn_number,
                entities,
                tuple(entity for entity in collection if entity in entities),
                active_entity,
                active_course,
                update.study_plan or session.snapshot.study_plan,
                session.snapshot.past_focus if update.past_focus is None else update.past_focus,
                update.pending_question,
            )
            session.snapshot = snapshot
            session.pending_token = None
            session.last_access = now
            session.expires_at = now + self.session_ttl_seconds
            return snapshot

    def abort_turn(self, lease: ConversationContextLease) -> None:
        if not isinstance(lease, ConversationContextLease):
            raise TypeError("lease must be a ConversationContextLease")
        now = self._now()
        with self._lock:
            session = self._sessions.get(lease.conversation_id)
            self._validate_lease(session, lease, now)
            assert session is not None
            session.pending_token = None
            session.last_access = now
            session.expires_at = now + self.session_ttl_seconds

    def _validate_lease(
        self,
        session: _StoredConversation | None,
        lease: ConversationContextLease,
        now: float,
    ) -> None:
        if session is None or now >= session.expires_at:
            self._sessions.pop(lease.conversation_id, None)
            raise ConversationContextStoreError(ConversationContextErrorCode.EXPIRED_SESSION)
        if (
            session.pending_token != lease.token
            or lease.turn_number != session.snapshot.turn_number + 1
        ):
            raise ConversationContextStoreError(ConversationContextErrorCode.INVALID_LEASE)

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, value in self._sessions.items() if now >= value.expires_at]
        for key in expired:
            del self._sessions[key]

    def _make_capacity(self, now: float) -> None:
        self._purge_expired(now)
        if len(self._sessions) < self.max_sessions:
            return
        idle = [
            (session.last_access, key)
            for key, session in self._sessions.items()
            if session.pending_token is None
        ]
        if not idle:
            raise ConversationContextStoreError(ConversationContextErrorCode.SESSION_CAPACITY)
        _, oldest_id = min(idle)
        del self._sessions[oldest_id]

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("conversation context clock must return a number")
        if not math.isfinite(float(value)):
            raise ValueError("conversation context clock must be finite")
        return float(value)


class ContextReferenceResolver:
    """Resolve only explicit, bounded references against a trusted snapshot."""

    def resolve(
        self,
        question: str,
        snapshot: ConversationContextSnapshot,
    ) -> ContextResolution:
        if not isinstance(question, str) or not isinstance(snapshot, ConversationContextSnapshot):
            raise TypeError("question and snapshot must be trusted typed inputs")
        normalized = _normalize(question)
        ordinal = _ORDINAL_REFERENCE.search(normalized)
        if ordinal is not None:
            position = _ORDINALS[ordinal.group(1)] - 1
            kind_text = ordinal.group(3)
            collection = snapshot.last_collection
            if kind_text is not None:
                expected_kind = (
                    ContextEntityKind.STUDY_TASK
                    if kind_text == "task"
                    else ContextEntityKind.MOODLE_CHANGE
                    if kind_text == "change"
                    else ContextEntityKind(kind_text)
                )
                collection = tuple(entity for entity in collection if entity.kind is expected_kind)
            if position >= len(collection):
                return ContextResolution(ContextResolutionStatus.MISSING)
            return _resolved(collection[position])

        explicit_id = _ENTITY_ID_REFERENCE.search(normalized)
        if explicit_id is not None:
            kind = (
                ContextEntityKind.STUDY_TASK
                if explicit_id.group(1) == "task"
                else ContextEntityKind(explicit_id.group(1))
            )
            entity_id = int(explicit_id.group(2))
            matches = tuple(
                entity
                for entity in _all_entities(snapshot)
                if entity.kind is kind and entity.entity_id == entity_id
            )
            return _choose(matches)

        if _NEW_DOCUMENT_REFERENCE.search(normalized) is not None:
            return _choose(
                tuple(
                    entity
                    for entity in _unique_entities(_all_entities(snapshot))
                    if entity.kind is ContextEntityKind.MOODLE_CHANGE
                    and entity.change is not None
                    and entity.change.entity_type is EntityType.DOCUMENT
                    and entity.change.after_document_version is not None
                )
            )

        changed_entity = _CHANGED_ENTITY_REFERENCE.search(normalized)
        if changed_entity is not None:
            changed_entity_type = EntityType(changed_entity.group(1).upper())
            return _choose(
                tuple(
                    entity
                    for entity in _unique_entities(_all_entities(snapshot))
                    if entity.kind is ContextEntityKind.MOODLE_CHANGE
                    and entity.change is not None
                    and entity.change.entity_type is changed_entity_type
                )
            )

        change_reference = _CHANGE_REFERENCE.search(normalized)
        if change_reference is not None:
            qualifier = change_reference.group(1)
            field = ChangeField.DUE_AT if qualifier in {"deadline", "due date"} else None
            change_entity_type = (
                EntityType(qualifier.upper())
                if qualifier in {"document", "assignment", "course"}
                else None
            )
            changes = tuple(
                entity
                for entity in _unique_entities(_all_entities(snapshot))
                if entity.kind is ContextEntityKind.MOODLE_CHANGE
                and entity.change is not None
                and (change_entity_type is None or entity.change.entity_type is change_entity_type)
                and (field is None or field in entity.change.changed_fields)
            )
            return _choose(changes)

        typed = _TYPED_REFERENCE.search(normalized)
        if typed is not None:
            if normalized.find("this course") >= 0:
                return _choose(_course_entities(snapshot))
            kind_text = typed.group(1) or typed.group(2)
            if kind_text is not None:
                kind = (
                    ContextEntityKind.STUDY_TASK
                    if kind_text == "task"
                    else ContextEntityKind.MOODLE_CHANGE
                    if kind_text == "change"
                    else ContextEntityKind(kind_text)
                )
                if kind in {ContextEntityKind.ASSIGNMENT, ContextEntityKind.DOCUMENT}:
                    expected_entity_type = EntityType(kind.value.upper())
                    recent_changes = tuple(
                        entity
                        for entity in snapshot.last_collection
                        if entity.kind is ContextEntityKind.MOODLE_CHANGE
                        and entity.change is not None
                        and entity.change.entity_type is expected_entity_type
                    )
                    if recent_changes:
                        return _choose(_unique_entities(recent_changes))
                entities = tuple(
                    entity for entity in _all_entities(snapshot) if entity.kind is kind
                )
                if entities:
                    return _choose(entities)
                if kind in {ContextEntityKind.ASSIGNMENT, ContextEntityKind.DOCUMENT}:
                    entity_type = EntityType(kind.value.upper())
                    return _choose(
                        tuple(
                            entity
                            for entity in _unique_entities(_all_entities(snapshot))
                            if entity.kind is ContextEntityKind.MOODLE_CHANGE
                            and entity.change is not None
                            and entity.change.entity_type is entity_type
                        )
                    )
                return ContextResolution(ContextResolutionStatus.MISSING)

        value_reference = _CHANGE_VALUE_REFERENCE.search(normalized)
        if value_reference is not None:
            referenced_field = {
                "deadline": ChangeField.DUE_AT,
                "deadlines": ChangeField.DUE_AT,
                "due": ChangeField.DUE_AT,
                "due date": ChangeField.DUE_AT,
                "due dates": ChangeField.DUE_AT,
                "publication date": ChangeField.PUBLISHED_AT,
                "document version": ChangeField.DOCUMENT_VERSION,
                "course code": ChangeField.COURSE_CODE,
            }[value_reference.group(1)]
            all_changes = tuple(
                entity
                for entity in _unique_entities(_all_entities(snapshot))
                if entity.kind is ContextEntityKind.MOODLE_CHANGE
            )
            changes = tuple(
                entity
                for entity in all_changes
                if entity.change is not None and referenced_field in entity.change.changed_fields
            )
            if changes:
                return _choose(changes)
            if all_changes:
                return ContextResolution(ContextResolutionStatus.MISSING)

        generic = _GENERIC_REFERENCE.search(normalized)
        if generic is not None:
            topic = _LOCAL_TOPIC_PREFIX.fullmatch(normalized[: generic.start()])
            if (
                topic is not None
                and _RECORD_OR_DEICTIC_TOPIC.search(topic.group("topic")) is None
                and _LOCAL_EXPLANATION.fullmatch(generic.group()) is not None
                and _DEICTIC_REFERENCE.search(normalized[generic.end() :]) is None
                and not has_bilingual_write_intent(normalized)
            ):
                # "What is KNIME? Explain it ..." refers within this question.
                # Explicit IDs/typed/ordinal references above still fail closed;
                # further pronouns must still resolve against trusted context.
                return ContextResolution(ContextResolutionStatus.NONE)
            if snapshot.active_entity is not None:
                return _resolved(snapshot.active_entity)
            candidates = _unique_entities(snapshot.last_collection or snapshot.entities)
            return _choose(candidates)

        explicit = _explicit_name_matches(normalized, snapshot)
        if explicit:
            longest = max(len(_normalize(entity.title or "")) for entity in explicit)
            return _choose(
                tuple(
                    entity for entity in explicit if len(_normalize(entity.title or "")) == longest
                )
            )
        return ContextResolution(ContextResolutionStatus.NONE)


def serialize_context_hint(resolution: ContextResolution) -> str:
    """Serialize one resolved alias without exposing canonical database IDs."""

    if not isinstance(resolution, ContextResolution):
        raise TypeError("resolution must be a ContextResolution")
    if resolution.status is not ContextResolutionStatus.RESOLVED:
        return "{}"
    assert resolution.entity is not None and resolution.context_ref is not None
    entity = resolution.entity
    value: dict[str, object] = {
        "reference": resolution.context_ref,
        "kind": entity.kind.value,
        "title": _preview(entity.title),
        "course_title": _preview(entity.course_title),
    }
    if entity.change is not None:
        change = entity.change
        value["change"] = {
            "entity_type": change.entity_type.value,
            "change_kind": change.change_kind.value,
            "changed_fields": tuple(field.value for field in change.changed_fields),
            "before_title": _preview(change.before_title),
            "after_title": _preview(change.after_title),
            "before_course_code": _preview(change.before_course_code),
            "after_course_code": _preview(change.after_course_code),
            "before_due_at": change.before_due_at.isoformat() if change.before_due_at else None,
            "after_due_at": change.after_due_at.isoformat() if change.after_due_at else None,
            "before_published_at": (
                change.before_published_at.isoformat() if change.before_published_at else None
            ),
            "after_published_at": (
                change.after_published_at.isoformat() if change.after_published_at else None
            ),
            "before_document_version": change.before_document_version,
            "after_document_version": change.after_document_version,
        }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SERIALIZED_CONTEXT_BYTES:
        value["title"] = None
        value["course_title"] = None
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SERIALIZED_CONTEXT_BYTES:
        raise ValueError("serialized conversation context exceeded its bound")
    return encoded


def _resolved(entity: TrustedContextEntity) -> ContextResolution:
    return ContextResolution(ContextResolutionStatus.RESOLVED, entity, "ctx-1")


def _choose(entities: tuple[TrustedContextEntity, ...]) -> ContextResolution:
    unique = _unique_entities(entities)
    if not unique:
        return ContextResolution(ContextResolutionStatus.MISSING)
    if len(unique) > 1:
        return ContextResolution(ContextResolutionStatus.AMBIGUOUS)
    return _resolved(unique[0])


def _all_entities(snapshot: ConversationContextSnapshot) -> tuple[TrustedContextEntity, ...]:
    values = (*snapshot.last_collection, *snapshot.entities)
    if snapshot.active_entity is not None:
        values = (*values, snapshot.active_entity)
    if snapshot.active_course is not None:
        values = (*values, snapshot.active_course)
    return _unique_entities(values)


def _course_entities(snapshot: ConversationContextSnapshot) -> tuple[TrustedContextEntity, ...]:
    candidates: list[TrustedContextEntity] = []
    if snapshot.active_course is not None:
        candidates.append(snapshot.active_course)
    for entity in _all_entities(snapshot):
        if entity.kind is ContextEntityKind.COURSE:
            candidates.append(entity)
        elif entity.course_id is not None:
            candidates.append(
                TrustedContextEntity(
                    ContextEntityKind.COURSE,
                    entity.course_id,
                    entity.course_title,
                    entity.course_id,
                    entity.course_title,
                    entity.source_tool,
                    entity.source_ref,
                    entity.source_turn,
                )
            )
    return _unique_entities(tuple(candidates))


def _explicit_name_matches(
    normalized_question: str,
    snapshot: ConversationContextSnapshot,
) -> tuple[TrustedContextEntity, ...]:
    matches: list[TrustedContextEntity] = []
    candidates = (*_all_entities(snapshot), *_course_entities(snapshot))
    for entity in candidates:
        title = _normalize(entity.title or "")
        if (
            title
            and len(title) >= 2
            and re.search(rf"(?<!\w){re.escape(title)}(?!\w)", normalized_question)
        ):
            matches.append(entity)
    return _unique_entities(tuple(matches))


def _unique_entities(
    entities: tuple[TrustedContextEntity, ...],
) -> tuple[TrustedContextEntity, ...]:
    unique: list[TrustedContextEntity] = []
    seen: set[tuple[ContextEntityKind, int | str]] = set()
    for entity in entities:
        identity: int | str = (
            entity.change.change_id
            if entity.kind is ContextEntityKind.MOODLE_CHANGE and entity.change is not None
            else entity.entity_id
        )
        key = (entity.kind, identity)
        if key not in seen:
            seen.add(key)
            unique.append(entity)
    return tuple(unique)


def _deduplicate_entities(
    entities: tuple[TrustedContextEntity, ...],
) -> tuple[TrustedContextEntity, ...]:
    return _unique_entities(entities)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _preview(value: str | None) -> str | None:
    return None if value is None else value[:MAX_CONTEXT_HINT_TITLE_CHARACTERS]


__all__ = [
    "CONVERSATION_SESSION_TTL_SECONDS",
    "MAX_CONTEXT_ENTITIES",
    "MAX_CONVERSATION_SESSIONS",
    "MAX_CONVERSATION_TURNS",
    "MAX_SERIALIZED_CONTEXT_BYTES",
    "ContextEntityKind",
    "ContextReferenceResolver",
    "ContextResolution",
    "ContextResolutionStatus",
    "ConversationContextLease",
    "ConversationContextSnapshot",
    "ConversationContextStore",
    "ConversationContextStoreError",
    "ConversationContextUpdate",
    "TrustedContextEntity",
    "TrustedMoodleChangeContext",
    "serialize_context_hint",
]
