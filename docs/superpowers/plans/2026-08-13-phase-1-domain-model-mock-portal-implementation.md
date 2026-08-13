# Phase 1 Domain Model and Mock Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic Phase 1 academic domain, complete-snapshot normalizer, in-memory mock source, and atomic SQLite synchronization substrate without entering Phase 2.

**Architecture:** Provider-neutral source records are accepted only as a proven complete result, normalized into immutable canonical snapshots, and persisted by one transactional sync service. Four SQLAlchemy tables use exact source namespaces plus opaque source IDs for identity; missing records are retained with `is_active=false`.

**Tech Stack:** Python 3.11, frozen dataclasses, SQLAlchemy, Alembic, SQLite, pytest, Ruff, mypy, and the existing uv lockfile. No new dependency, HTTP server, Moodle client, browser, or network access.

## Global Constraints

- Work only after human approval of `docs/superpowers/specs/2026-08-13-phase-1-domain-model-mock-portal-design.md`.
- Preserve accepted Phase 0 behavior and `phase-00-passed`; do not modify Phase 0 inference or doctor contracts.
- `source_system` is a stable, non-secret source namespace/instance key (for example `mock:primary` or `moodle:just`), never merely a generic adapter name.
- Every lookup, parent resolution, deactivation, reactivation, and snapshot operation is scoped to the exact namespace.
- Only a successful complete enumeration may become `CanonicalSnapshot`; partial, failed, interrupted, intermediate, or unknown-completeness results fail before synchronization.
- Duplicate `(source_system, source_id)` identities within one entity type fail before writes, even when values are identical.
- Assignment, Announcement, and RemoteFile parent-course identity is immutable; re-parenting is a typed conflict.
- `RemoteFile.remote_url` is optional non-secret HTTP(S) metadata. Phase 1 mechanically enforces scheme, structure, no embedded userinfo, and no control characters; provider-specific adapters must omit credentials, tokens, cookies, session state, signatures, or other secret-bearing locator material before the canonical boundary.
- A complete empty snapshot is valid only when the source proves the exact namespace is completely empty; it may deactivate only that namespace.
- Canonical datetimes are aware UTC values. Offset or `Z` input is accepted; naive input is rejected; no local/Jordan timezone is inferred.
- Validate the entire snapshot before opening a write transaction. Deactivation and all upserts commit atomically or roll back together.
- Do not add hard deletion, retries, statistics/`SyncResult`, repositories, event buses, plugin frameworks, or generic abstractions without a failing test proving need.
- Do not implement Moodle authentication, sessions, Playwright, HTTP mock servers, downloads, ingestion, RAG, LLM/agent behavior, MCP, external writes, dashboards, REPL, vision, or Phase 2 scaffolding.
- Use sanitized deterministic fixtures only; never add credentials, private academic records, cookies, tokens, or secrets.
- Before the first production edit, run `uv lock --check` and `uv sync --locked --all-groups`.
- Use Terra for normal implementation/integration review; escalate to Sol only for a genuine architecture or security contradiction or the final security-sensitive whole-phase review.

## File map

- `src/aaos/academic/models.py` — immutable canonical records, source identity, and UTC value contract.
- `src/aaos/academic/source.py` — provider-neutral raw records, complete/failure collection result, and source protocol.
- `src/aaos/academic/normalization.py` — pure validation, duplicate/parent checks, timestamp and URL normalization.
- `src/aaos/academic/mock.py` — deterministic named in-memory source snapshots.
- `src/aaos/academic/sync.py` — one-transaction namespace-scoped upsert/deactivation service.
- `src/aaos/persistence/academic_models.py` — SQLAlchemy metadata and four ORM mappings.
- `src/aaos/persistence/database.py` — only the SQLite foreign-key connection hook, if required.
- `migrations/versions/0002_phase1_academic_domain.py` — explicit schema migration.
- `tests/test_academic_*.py` — focused contract, migration, mock, sync, and integration tests.

No other application or dependency files are planned.

## Preflight

- [ ] Confirm `git status --short --branch` is clean, `phase-00-passed` exists and resolves exactly to `55d2293c49f3f7ab73e4920a5e97c346efb362cd`, and the current starting HEAD contains that commit in its ancestry. The starting HEAD is the approved Phase 1 design/plan history, not the Phase 0 tag.
- [ ] Read the approved design and this plan; verify no Phase 1 files exist.
- [ ] Run the locked-environment preflight and accepted persistence/import baseline:

```powershell
uv lock --check
uv sync --locked --all-groups
uv run pytest tests/test_database.py tests/test_inference_imports.py tests/test_doctor_imports.py -v
```

## Task 1: Canonical/source contracts and pure normalization

**Files:** Create `src/aaos/academic/{__init__.py,models.py,source.py,normalization.py}`; create `tests/test_academic_models.py` and `tests/test_academic_normalization.py`.

**Interfaces:** `SourceCollection = CompleteSourceSnapshot | SourceCollectionFailure`; `normalize_collection(collection: SourceCollection) -> CanonicalSnapshot`; frozen canonical Course/Assignment/Announcement/RemoteFile inputs; child inputs carry `course_source_id`, never an internal database ID.

- [ ] Write RED tests for frozen/nonblank namespace and IDs, complete versus failed collection results, duplicate identities (including identical duplicates), same source ID across tables, missing/ambiguous/cross-namespace parents, aware offset and `Z` to UTC, naive timestamp rejection, `None` optionals, and unsafe URLs.

```python
def test_duplicate_assignment_identity_is_rejected() -> None:
    source = complete_snapshot(assignments=(assignment("a1"), assignment("a1")))
    with pytest.raises(NormalizationError, match="duplicate"):
        normalize_collection(source)

def test_offset_timestamp_is_aware_utc() -> None:
    result = normalize_collection(complete_snapshot(due_at="2026-08-13T15:00:00+03:00"))
    assert result.assignments[0].due_at == datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
```

- [ ] Run `uv run pytest tests/test_academic_models.py tests/test_academic_normalization.py -v`; expect import/collection RED.
- [ ] Implement Python 3.11-compatible frozen dataclasses and fixed exception types. `CompleteSourceSnapshot` is the only successful collection result; a failure or incomplete result cannot produce `CanonicalSnapshot`. Validate all identities and parents before returning; reject duplicates, cross-source references, naive timestamps, unsafe URL structure, and control characters. Accept only structurally valid HTTP(S) URLs without userinfo; do not inspect arbitrary query parameters, infer provider tokens, or fetch URLs. The canonical contract remains non-secret, while later provider adapters own removal/omission of secret-bearing locator material.
- [ ] Run focused pytest, Ruff format/check, mypy, and `git diff --check`; commit as `feat: add Phase 1 academic normalization contracts`.

## Task 2: SQLAlchemy schema, UTC persistence, and Alembic migration

**Files:** Create `src/aaos/persistence/academic_models.py` and `migrations/versions/0002_phase1_academic_domain.py`; modify `src/aaos/persistence/database.py` only for SQLite FK enforcement; create `tests/test_academic_persistence.py` and `tests/test_academic_migration.py`.

**Interfaces:** `Base: DeclarativeBase`; `UTCDateTime(TypeDecorator[datetime])`; tables `courses`, `assignments`, `announcements`, `remote_files`; integer `id`, `(source_system, source_id)` unique per table, `is_active`, required child FKs, and approved nullable metadata.

- [ ] Write RED tests for migration upgrade/downgrade, exactly four Phase 1 domain tables (`courses`, `assignments`, `announcements`, `remote_files`), unique identity per table, same source ID across tables, FK enforcement, active defaults, and the `UTCDateTime` boundary: naive values rejected, aware values accepted and normalized to UTC, canonical ISO-8601 `Z` text written, and aware `timezone.utc` values returned. The Alembic `alembic_version` table is migration metadata and must not fail the domain-table assertion.
- [ ] Run `uv run pytest tests/test_academic_persistence.py tests/test_academic_migration.py -v`; expect missing-module/schema RED.
- [ ] Implement explicit ORM mappings, ISO-8601 `Z` storage that returns aware UTC values, indexes on child `course_id`, `(course_id, is_active)`, and `(source_system, is_active)`, and a SQLite `PRAGMA foreign_keys=ON` connection hook. `UTCDateTime.process_bind_param` must reject naive datetimes, normalize aware values with `astimezone(timezone.utc)`, and serialize only canonical `Z` text; `process_result_value` must return an aware UTC value. Use explicit Alembic operations; create no auth/session/document tables.

```python
class CourseORM(Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
```

Use the same identity/active columns on every table, add the approved optional metadata, and make child `course_id` non-nullable.
- [ ] Run focused pytest, Ruff format/check, mypy, and `git diff --check`; commit as `feat: add Phase 1 academic schema`.

## Task 3: Deterministic in-memory mock source

**Files:** Create `src/aaos/academic/mock.py` and `tests/test_academic_mock.py`.

**Interfaces:** `MockAcademicSource(snapshots: Mapping[str, SourceCollection])`; `collect(name: str) -> SourceCollection` returns an immutable selected fixture without I/O or implicit sequencing.

- [ ] Write RED tests for multiple courses, assignment deadlines, announcements, RemoteFile metadata, changed/missing records, complete empty snapshots, unknown names, and import side-effect safety.
- [ ] Run `uv run pytest tests/test_academic_mock.py -v`; expect missing-module RED.
- [ ] Implement immutable named fixtures only. Do not start an HTTP server or read files.

```python
class MockAcademicSource:
    def __init__(self, snapshots: Mapping[str, SourceCollection]) -> None: ...
    def collect(self, name: str) -> SourceCollection: ...
```
- [ ] Run focused pytest, Ruff format/check, mypy, and `git diff --check`; commit as `feat: add deterministic Phase 1 mock source`.

## Task 4: Core namespace-scoped transactional synchronization

**Files:** Create `src/aaos/academic/sync.py` and `tests/test_academic_sync.py`.

**Interfaces:** `AcademicSyncService(session_factory: sessionmaker[Session])`; `sync(snapshot: CanonicalSnapshot) -> None`; `SynchronizationConflict`; `SynchronizationPersistenceError`.

- [ ] Write RED tests for first discovery, identical repeat without duplicates, changed mutable fields preserving internal IDs, new records, parent-first insertion, source namespace isolation with colliding provider IDs, and same source IDs across entity tables.

```python
def test_two_namespaces_with_same_provider_id_do_not_collide(db_session_factory) -> None:
    service = AcademicSyncService(db_session_factory)
    service.sync(snapshot("mock:one", course_id="42"))
    service.sync(snapshot("moodle:just", course_id="42"))
    assert count_courses(db_session_factory) == 2
```

- [ ] Run `uv run pytest tests/test_academic_sync.py -v`; expect missing-service RED.
- [ ] Implement one transaction: deactivate only rows with the exact snapshot namespace; upsert courses first, then children by `(source_system, source_id)`; preserve internal IDs and update mutable fields. Compare an existing child’s persisted parent source identity with incoming `course_source_id`; raise `SynchronizationConflict` on mismatch. Roll back expected SQLAlchemy failures and expose only fixed typed errors.
- [ ] Run focused pytest, Ruff format/check, mypy, and `git diff --check`; commit as `feat: add transactional academic synchronization`.

## Task 5: Completion, disappearance, and rollback hardening

**Files:** Modify normalization/sync only for failing tests; extend `tests/test_academic_normalization.py` and `tests/test_academic_sync.py`.

- [ ] Add RED tests proving partial/failed/unknown-completeness input, duplicate identities, missing/ambiguous/cross-source parents, generic unsafe URL structure, and child re-parenting leave the database untouched. Prove complete empty deactivation is namespace-scoped, normal disappearance/reactivation works, and injected persistence failure rolls back prior deactivation plus upserts. Do not claim generic tests can exhaustively detect provider-signed or tokenized URLs.

```python
def test_reparent_conflict_rolls_back(service) -> None:
    service.sync(normalize_collection(snapshot_with_child("course-a")))
    with pytest.raises(SynchronizationConflict):
        service.sync(normalize_collection(snapshot_with_child("course-b")))
    assert persisted_parent(service, "assignment-1") == "course-a"
```

- [ ] Run `uv run pytest tests/test_academic_normalization.py tests/test_academic_sync.py -v` and record RED.
- [ ] Make only the smallest fixes required; retain validation-before-write, exact namespace filters, immutable parent checks, and one rollback boundary. Do not add retries, deletion, network calls, or generic secret scanning.
- [ ] Run focused pytest, Ruff format/check, mypy, and `git diff --check`; commit as `test: harden Phase 1 snapshot safety`.

## Task 6: Integration, evidence, and human handoff

**Files:** Create `tests/test_academic_integration.py`; create ignored `.superpowers/sdd/2026-08-13-phase-1-domain-model-mock-portal-implementation/phase-1-evidence-report.md`.

- [ ] Add integration tests for the approved path `MockAcademicSource.collect()` → `normalize_collection()` → `AcademicSyncService.sync()`, including migration state, complete graph, namespace isolation, idempotence, update/new/disappearance/reactivation/empty behavior, rollback, and import safety.
- [ ] Run focused tests:

```powershell
uv run pytest tests/test_academic_*.py -v
```

- [ ] Review the integration-test diff, then commit it only after focused tests pass:

```powershell
git add tests/test_academic_integration.py
git commit -m "test: verify Phase 1 academic sync integration"
```

- [ ] Capture the resulting `FINAL_PHASE1_HEAD` and run the complete deterministic gate against that exact committed HEAD:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
uv lock --check
uv sync --locked --all-groups
git diff --check
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
```

Confirm locked sync caused no tracked mutation. Do not alter the default live
LM Studio smoke or make it a Phase 1 requirement.

- [ ] Dispatch one independent Terra-medium whole-Phase-1 review against `FINAL_PHASE1_HEAD`. The reviewed HEAD must equal the gate-tested and candidate human-accepted HEAD. If a review correction changes tracked code/tests, commit it and rerun the complete final gate against the new HEAD.
- [ ] Assemble the ignored evidence report from that exact HEAD with migration/test/static results, namespace and rollback evidence, limitations, locked-sync mutation status, independent review result, and explicit recommendation for human Phase 1 acceptance. State that no Moodle/network/auth/download/RAG/LLM/agent functionality was added.

## Final handoff and self-review

After Task 6, stop for explicit human Phase 1 acceptance. Do not modify the roadmap, create `phase-01-passed`, begin Phase 2, or add real Moodle code.

Coverage check: Tasks 1–2 cover source completion, exact namespace identity, duplicate/parent/URL/time validation, UTC persistence, FK/unique constraints, and migration. Tasks 3–5 cover deterministic fixtures, initial/repeat/change/new sync, namespace isolation, disappearance/reactivation/empty snapshots, immutable parents, and rollback. Task 6 covers integration, import safety, full gates, evidence, and handoff. The plan has no unresolved placeholders or later-phase functionality; all later interfaces consume `CanonicalSnapshot` only.
