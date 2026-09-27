"""Bounded, process-local trusted references for multi-turn agent requests."""

from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable
from uuid import UUID, uuid4

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
    r"|study_task\.(?:list|get|create|update|complete))$"
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
    r"\s+(?:(one)|(course|assignment|document|task))\b"
)
_ENTITY_ID_REFERENCE = re.compile(r"\b(course|assignment|document|task)\s*#?\s*([1-9][0-9]{0,8})\b")
_TYPED_REFERENCE = re.compile(
    r"\bthat\s+(course|assignment|document|task)\b|\bthis\s+(course|task)\b"
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
    r"|(?:mark|complete|rename|update|change|set|reprioritize)\s+(?:it|that|this)\b"
    r"|(?:change|set)\s+its\s+(?:priority|title|name)\b"
    r")\b"
)


class ContextEntityKind(StrEnum):
    COURSE = "course"
    ASSIGNMENT = "assignment"
    DOCUMENT = "document"
    STUDY_TASK = "study_task"


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


@dataclass(frozen=True, slots=True)
class ConversationContextUpdate:
    entities: tuple[TrustedContextEntity, ...] = ()
    # None preserves the previous ordered collection; an empty tuple clears it.
    last_collection: tuple[TrustedContextEntity, ...] | None = None
    active_entity: TrustedContextEntity | None = None
    active_course: TrustedContextEntity | None = None

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

        typed = _TYPED_REFERENCE.search(normalized)
        if typed is not None:
            if normalized.find("this course") >= 0:
                return _choose(_course_entities(snapshot))
            kind_text = typed.group(1) or typed.group(2)
            if kind_text is not None:
                kind = (
                    ContextEntityKind.STUDY_TASK
                    if kind_text == "task"
                    else ContextEntityKind(kind_text)
                )
                return _choose(
                    tuple(entity for entity in _all_entities(snapshot) if entity.kind is kind)
                )

        if _GENERIC_REFERENCE.search(normalized) is not None:
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
    value = {
        "reference": resolution.context_ref,
        "kind": entity.kind.value,
        "title": _preview(entity.title),
        "course_title": _preview(entity.course_title),
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
    seen: set[tuple[ContextEntityKind, int]] = set()
    for entity in entities:
        key = (entity.kind, entity.entity_id)
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
    "serialize_context_hint",
]
