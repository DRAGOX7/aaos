from __future__ import annotations

from datetime import datetime, timezone
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
    TrustedMoodleChangeContext,
    serialize_context_hint,
)
from aaos.moodle.changes import ChangeField, ChangeKind, EntityType


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
        source_tool=(
            "study_task.list"
            if kind is ContextEntityKind.STUDY_TASK
            else f"academic.list_{kind.value}s"
        ),
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
    assert course.entity is not None
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


def test_this_assignment_resolves_only_a_trusted_assignment() -> None:
    assignment = _entity(
        ContextEntityKind.ASSIGNMENT,
        21,
        "Project Proposal",
        course_id=7,
    )
    store, conversation_id = _store_with_context((assignment,))
    snapshot = store.begin_turn(conversation_id).snapshot

    resolved = ContextReferenceResolver().resolve(
        "Put this assignment deadline on my calendar", snapshot
    )

    assert resolved.status is ContextResolutionStatus.RESOLVED
    assert resolved.entity == assignment


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


@pytest.mark.parametrize(
    "question",
    ("Mark it complete", "Rename it", "Change its priority", "Schedule it Friday evening"),
)
def test_task_action_language_resolves_only_the_active_trusted_reference(
    question: str,
) -> None:
    task = _entity(ContextEntityKind.ASSIGNMENT, 21, "Proof set", course_id=7)
    store, conversation_id = _store_with_context((task,), active_entity=task)
    snapshot = store.begin_turn(conversation_id).snapshot

    resolved = ContextReferenceResolver().resolve(question, snapshot)

    assert resolved.status is ContextResolutionStatus.RESOLVED
    assert resolved.entity == task


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


@pytest.mark.parametrize("question", ("Mark this task complete", "Rename that task"))
def test_typed_study_task_references_resolve_active_task(question: str) -> None:
    task = _entity(ContextEntityKind.STUDY_TASK, 27, "Review chapter 5", course_id=7)
    store, conversation_id = _store_with_context((task,), active_entity=task)
    snapshot = store.begin_turn(conversation_id).snapshot

    resolution = ContextReferenceResolver().resolve(question, snapshot)

    assert resolution.status is ContextResolutionStatus.RESOLVED
    assert resolution.entity == task


def test_short_entity_title_requires_phrase_boundaries() -> None:
    course = _entity(ContextEntityKind.COURSE, 7, "AI")
    store, conversation_id = _store_with_context((course,))
    snapshot = store.begin_turn(conversation_id).snapshot

    resolution = ContextReferenceResolver().resolve("Explain paid deadlines", snapshot)

    assert resolution.status is ContextResolutionStatus.NONE
    assert resolution.entity is None


def _change_entity(
    change_id: str,
    entity_id: int = 20,
    *,
    entity_type: EntityType = EntityType.ASSIGNMENT,
    changed_field: ChangeField = ChangeField.DUE_AT,
    from_snapshot_id: int = 10,
    to_snapshot_id: int = 11,
) -> TrustedContextEntity:
    return TrustedContextEntity(
        kind=ContextEntityKind.MOODLE_CHANGE,
        entity_id=entity_id,
        title="Neural Networks Report",
        course_id=7,
        course_title="Deep Learning",
        source_tool="moodle.list_changes",
        source_ref="read-1",
        source_turn=1,
        change=TrustedMoodleChangeContext(
            change_id=change_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            entity_type=entity_type,
            change_kind=ChangeKind.MODIFIED,
            changed_fields=(changed_field,),
            before_title="Neural Networks Report",
            after_title="Neural Networks Report",
            before_course_code=None,
            after_course_code=None,
            before_due_at=(
                datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
                if changed_field is ChangeField.DUE_AT
                else None
            ),
            after_due_at=(
                datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
                if changed_field is ChangeField.DUE_AT
                else None
            ),
            before_published_at=None,
            after_published_at=None,
            before_document_version=(1 if changed_field is ChangeField.DOCUMENT_VERSION else None),
            after_document_version=(2 if changed_field is ChangeField.DOCUMENT_VERSION else None),
            detected_at=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
        ),
    )


def test_resolver_selects_trusted_moodle_change_and_serializes_safe_deadlines() -> None:
    store = ConversationContextStore()
    lease = store.begin_turn()
    change = _change_entity("10-11-1")
    store.finish_turn(
        lease,
        ConversationContextUpdate(
            entities=(change,),
            last_collection=(change,),
            active_entity=change,
        ),
    )
    followup = store.begin_turn(lease.conversation_id)
    resolver = ContextReferenceResolver()

    explicit = resolver.resolve("Explain that change", followup.snapshot)
    ordinal = resolver.resolve("Explain the first change", followup.snapshot)
    missing = resolver.resolve("Explain the second change", followup.snapshot)
    hint = serialize_context_hint(explicit)

    assert explicit.status is ContextResolutionStatus.RESOLVED
    assert explicit.entity == change
    assert ordinal.entity == change
    assert missing.status is ContextResolutionStatus.MISSING
    assert '"before_due_at":"2026-09-30T12:00:00+00:00"' in hint
    assert '"after_due_at":"2026-10-02T12:00:00+00:00"' in hint
    assert '"entity_id"' not in hint
    store.abort_turn(followup)


def test_change_resolution_is_ambiguous_without_a_unique_active_change() -> None:
    first = _change_entity("10-11-1", entity_id=20)
    second = _change_entity("10-11-2", entity_id=21)
    store = ConversationContextStore()
    lease = store.begin_turn()
    store.finish_turn(
        lease,
        ConversationContextUpdate(
            entities=(first, second),
            last_collection=(first, second),
        ),
    )
    followup = store.begin_turn(lease.conversation_id)

    result = ContextReferenceResolver().resolve("Explain that change", followup.snapshot)

    assert result.status is ContextResolutionStatus.AMBIGUOUS
    store.abort_turn(followup)


def test_typed_change_references_resolve_by_field_entity_and_result_order() -> None:
    deadline = _change_entity("10-11-1", entity_id=20)
    document = _change_entity(
        "10-11-2",
        entity_id=30,
        entity_type=EntityType.DOCUMENT,
        changed_field=ChangeField.DOCUMENT_VERSION,
    )
    store = ConversationContextStore()
    lease = store.begin_turn()
    store.finish_turn(
        lease,
        ConversationContextUpdate(
            entities=(deadline, document),
            last_collection=(deadline, document),
        ),
    )
    followup = store.begin_turn(lease.conversation_id)
    resolver = ContextReferenceResolver()

    assert resolver.resolve("Explain the first change", followup.snapshot).entity == deadline
    assert resolver.resolve("Explain the second change", followup.snapshot).entity == document
    assert resolver.resolve("Explain the deadline change", followup.snapshot).entity == deadline
    assert resolver.resolve("What was the old deadline?", followup.snapshot).entity == deadline
    assert resolver.resolve("When is it due now?", followup.snapshot).entity == deadline
    assert resolver.resolve("Explain the new document", followup.snapshot).entity == document
    assert resolver.resolve("Explain that assignment", followup.snapshot).entity == deadline
    assert (
        resolver.resolve("Explain the assignment that changed", followup.snapshot).entity
        == deadline
    )
    store.abort_turn(followup)


def test_change_field_reference_does_not_fall_back_to_an_unrelated_change() -> None:
    document = _change_entity(
        "10-11-2",
        entity_id=30,
        entity_type=EntityType.DOCUMENT,
        changed_field=ChangeField.DOCUMENT_VERSION,
    )
    store = ConversationContextStore()
    lease = store.begin_turn()
    store.finish_turn(
        lease,
        ConversationContextUpdate(entities=(document,), last_collection=(document,)),
    )
    followup = store.begin_turn(lease.conversation_id)

    result = ContextReferenceResolver().resolve("When is the new deadline?", followup.snapshot)

    assert result.status is ContextResolutionStatus.MISSING
    store.abort_turn(followup)


def test_equal_snapshot_ids_are_only_valid_for_document_version_changes() -> None:
    document_change = _change_entity(
        "10-10-1",
        entity_id=30,
        entity_type=EntityType.DOCUMENT,
        changed_field=ChangeField.DOCUMENT_VERSION,
        from_snapshot_id=10,
        to_snapshot_id=10,
    )

    assert document_change.change is not None
    assert document_change.change.from_snapshot_id == document_change.change.to_snapshot_id
    with pytest.raises(ValueError, match="change snapshots must be ordered"):
        _change_entity("10-10-2", from_snapshot_id=10, to_snapshot_id=10)


def test_change_context_remains_session_isolated() -> None:
    store = ConversationContextStore()
    first = store.begin_turn()
    change = _change_entity("10-11-1")
    store.finish_turn(
        first,
        ConversationContextUpdate(
            entities=(change,),
            last_collection=(change,),
            active_entity=change,
        ),
    )
    other = store.begin_turn()

    assert (
        ContextReferenceResolver().resolve("Explain that change", other.snapshot).status
        is ContextResolutionStatus.MISSING
    )
    store.abort_turn(other)


def test_expired_change_context_cannot_be_resolved() -> None:
    clock = FakeClock()
    store = ConversationContextStore(clock=clock)
    lease = store.begin_turn()
    change = _change_entity("10-11-1")
    store.finish_turn(
        lease,
        ConversationContextUpdate(
            entities=(change,),
            last_collection=(change,),
            active_entity=change,
        ),
    )
    clock.advance(store.session_ttl_seconds + 1)

    with pytest.raises(ConversationContextStoreError):
        store.begin_turn(lease.conversation_id)
