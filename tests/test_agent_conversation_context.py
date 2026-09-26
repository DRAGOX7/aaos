from __future__ import annotations

from uuid import UUID

import pytest

from aaos.agent.conversation_context import (
    MAX_CONTEXT_ENTITIES,
    MAX_CONVERSATION_TURNS,
    MAX_SERIALIZED_CONTEXT_BYTES,
    ContextEntityKind,
    ContextReferenceResolver,
    ContextResolutionStatus,
    ConversationContextStore,
    ConversationContextStoreError,
    ConversationContextUpdate,
    TrustedContextEntity,
    serialize_context_hint,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _entity(
    kind: ContextEntityKind,
    entity_id: int,
    title: str,
    *,
    course_id: int | None = None,
    course_title: str | None = None,
    turn: int = 1,
    source_ref: str = "read-1",
) -> TrustedContextEntity:
    return TrustedContextEntity(
        kind=kind,
        entity_id=entity_id,
        title=title,
        course_id=course_id,
        course_title=course_title,
        source_tool=f"academic.list_{kind.value}s",
        source_ref=source_ref,
        source_turn=turn,
    )


def _store_with_context(
    entities: tuple[TrustedContextEntity, ...],
    *,
    last_collection: tuple[TrustedContextEntity, ...] | None = None,
    active_entity: TrustedContextEntity | None = None,
    active_course: TrustedContextEntity | None = None,
    clock: FakeClock | None = None,
) -> tuple[ConversationContextStore, UUID]:
    store = ConversationContextStore(clock=clock or FakeClock())
    lease = store.begin_turn()
    store.finish_turn(
        lease,
        ConversationContextUpdate(
            entities=entities,
            last_collection=last_collection,
            active_entity=active_entity,
            active_course=active_course,
        ),
    )
    return store, lease.conversation_id


def test_second_item_resolves_from_trusted_assignment_order() -> None:
    assignments = (
        _entity(ContextEntityKind.ASSIGNMENT, 20, "First brief", course_id=7),
        _entity(ContextEntityKind.ASSIGNMENT, 21, "Second brief", course_id=7, source_ref="read-2"),
        _entity(ContextEntityKind.ASSIGNMENT, 22, "Third brief", course_id=7, source_ref="read-3"),
    )
    store, conversation_id = _store_with_context(assignments, last_collection=assignments)
    snapshot = store.begin_turn(conversation_id).snapshot

    resolved = ContextReferenceResolver().resolve("Explain the second one", snapshot)

    assert resolved.status is ContextResolutionStatus.RESOLVED
    assert resolved.entity == assignments[1]


def test_first_document_and_active_course_resolve_from_trusted_context() -> None:
    documents = (
        _entity(
            ContextEntityKind.DOCUMENT,
            30,
            "Lecture One",
            course_id=7,
            course_title="Deep Learning",
        ),
        _entity(
            ContextEntityKind.DOCUMENT,
            31,
            "Lecture Two",
            course_id=7,
            course_title="Deep Learning",
            source_ref="read-2",
        ),
    )
    store, conversation_id = _store_with_context(documents, last_collection=documents)
    snapshot = store.begin_turn(conversation_id).snapshot

    first_document = ContextReferenceResolver().resolve("Explain the first document", snapshot)
    course = ContextReferenceResolver().resolve("What documents are in this course?", snapshot)

    assert first_document.status is ContextResolutionStatus.RESOLVED
    assert first_document.entity == documents[0]
    assert course.status is ContextResolutionStatus.RESOLVED
    assert course.entity.kind is ContextEntityKind.COURSE
    assert course.entity.entity_id == 7
    assert course.entity.title == "Deep Learning"


def test_explicit_prior_entity_name_or_id_resolves_only_known_identity() -> None:
    assignment = _entity(
        ContextEntityKind.ASSIGNMENT,
        21,
        "Project Proposal",
        course_id=7,
    )
    store, conversation_id = _store_with_context((assignment,))
    snapshot = store.begin_turn(conversation_id).snapshot
    resolver = ContextReferenceResolver()

    by_name = resolver.resolve("Explain Project Proposal", snapshot)
    by_id = resolver.resolve("Show assignment #21", snapshot)
    unknown_id = resolver.resolve("Show assignment #999", snapshot)

    assert by_name.status is ContextResolutionStatus.RESOLVED
    assert by_name.entity == assignment
    assert by_id.status is ContextResolutionStatus.RESOLVED
    assert by_id.entity == assignment
    assert unknown_id.status is ContextResolutionStatus.MISSING


def test_ambiguous_pronoun_and_missing_ordinal_do_not_guess() -> None:
    assignments = (
        _entity(ContextEntityKind.ASSIGNMENT, 20, "First", course_id=7),
        _entity(ContextEntityKind.ASSIGNMENT, 21, "Second", course_id=7, source_ref="read-2"),
    )
    store, conversation_id = _store_with_context(assignments, last_collection=assignments)
    snapshot = store.begin_turn(conversation_id).snapshot
    resolver = ContextReferenceResolver()

    pronoun = resolver.resolve("Explain it", snapshot)
    missing = resolver.resolve("Explain the third one", snapshot)

    assert pronoun.status is ContextResolutionStatus.AMBIGUOUS
    assert pronoun.entity is None
    assert missing.status is ContextResolutionStatus.MISSING
    assert missing.entity is None


def test_stale_expired_and_cross_session_context_fail_closed() -> None:
    clock = FakeClock()
    course = _entity(ContextEntityKind.COURSE, 7, "Deep Learning")
    store, first_id = _store_with_context((course,), clock=clock)
    second_lease = store.begin_turn()
    second_id = second_lease.conversation_id
    store.abort_turn(second_lease)

    with pytest.raises(ConversationContextStoreError):
        store.begin_turn(UUID(int=2))
    second_snapshot_lease = store.begin_turn(second_id)
    assert second_snapshot_lease.snapshot.entities == ()
    store.abort_turn(second_snapshot_lease)
    clock.advance(store.session_ttl_seconds + 1)
    with pytest.raises(ConversationContextStoreError):
        store.begin_turn(first_id)


def test_context_entity_count_and_serialized_hint_are_bounded() -> None:
    entities = tuple(
        _entity(
            ContextEntityKind.DOCUMENT, index + 1, "🧠" * 2048, source_ref=f"read-{index % 8 + 1}"
        )
        for index in range(MAX_CONTEXT_ENTITIES + 10)
    )
    store, conversation_id = _store_with_context(entities, last_collection=entities)
    lease = store.begin_turn(conversation_id)

    assert len(lease.snapshot.entities) <= MAX_CONTEXT_ENTITIES
    assert len(lease.snapshot.last_collection) <= MAX_CONTEXT_ENTITIES
    reference = ContextReferenceResolver().resolve("the first document", lease.snapshot)
    encoded = serialize_context_hint(reference)
    assert len(encoded.encode("utf-8")) <= MAX_SERIALIZED_CONTEXT_BYTES


def test_turn_limit_and_expiry_are_deterministic() -> None:
    clock = FakeClock()
    store = ConversationContextStore(clock=clock)
    lease = store.begin_turn()
    conversation_id = lease.conversation_id
    store.finish_turn(lease, ConversationContextUpdate())
    for _ in range(MAX_CONVERSATION_TURNS - 1):
        next_lease = store.begin_turn(conversation_id)
        store.finish_turn(next_lease, ConversationContextUpdate())

    with pytest.raises(ConversationContextStoreError):
        store.begin_turn(conversation_id)


def test_resolver_does_not_treat_unrelated_text_as_a_context_reference() -> None:
    course = _entity(ContextEntityKind.COURSE, 7, "Deep Learning")
    store, conversation_id = _store_with_context(
        (course,), active_entity=course, active_course=course
    )
    snapshot = store.begin_turn(conversation_id).snapshot

    resolution = ContextReferenceResolver().resolve("List my Databases assignments", snapshot)
    ordinary_this = ContextReferenceResolver().resolve(
        "Explain this lecture using my documents.", snapshot
    )

    assert resolution.status is ContextResolutionStatus.NONE
    assert resolution.entity is None
    assert ordinary_this.status is ContextResolutionStatus.NONE
    assert ordinary_this.entity is None
    nonreferential_that = ContextReferenceResolver().resolve(
        "I think that I need a list of all courses", snapshot
    )

    assert nonreferential_that.status is ContextResolutionStatus.NONE
    assert nonreferential_that.entity is None


def test_generic_referential_phrasing_resolves_active_entity() -> None:
    course = _entity(ContextEntityKind.COURSE, 7, "Deep Learning")
    store, conversation_id = _store_with_context(
        (course,), active_entity=course, active_course=course
    )
    snapshot = store.begin_turn(conversation_id).snapshot

    resolution = ContextReferenceResolver().resolve("What documents are related to it?", snapshot)

    assert resolution.status is ContextResolutionStatus.RESOLVED
    assert resolution.entity == course


def test_short_entity_title_requires_phrase_boundaries() -> None:
    course = _entity(ContextEntityKind.COURSE, 7, "AI")
    store, conversation_id = _store_with_context((course,))
    snapshot = store.begin_turn(conversation_id).snapshot

    resolution = ContextReferenceResolver().resolve("Explain paid deadlines", snapshot)

    assert resolution.status is ContextResolutionStatus.NONE
    assert resolution.entity is None
