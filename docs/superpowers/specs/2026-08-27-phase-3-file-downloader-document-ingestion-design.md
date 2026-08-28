# Phase 3 — File Downloader and Document Ingestion Design

Status: human-approved Phase 3 architecture

## 1. Purpose and scope

Phase 3 safely acquires newly discovered academic PDF resources, validates the
source bytes, preserves immutable content, deduplicates identical payloads,
versions changed payloads, extracts embedded page text, and persists provenance
and page metadata.

Phase 3 is an explicit operator-invoked capability. A normal Phase 2 Moodle
sync remains metadata-only and must never trigger a download or ingestion.
Phase 1 and Phase 2 remain frozen at their accepted boundaries.

Phase 3 v1 is synchronous, sequential, single-writer, PDF-only, and local-first.
It does not perform OCR, RAG chunking, embeddings, visual retrieval, LLM
processing, document summarization, or any Moodle write.

## 2. Frozen foundation and phase boundary

Phase 1 owns the canonical academic entities, including `RemoteFile`, stable
source identity, normalization, and deterministic synchronization. Phase 2
discovers Moodle metadata and may produce normalized `RemoteFile` rows, but it
does not fetch `pluginfile` content or download files.

Phase 3 starts only from an existing normalized Moodle `RemoteFile`. It adds a
separate, narrowly authorized fetch and ingestion pipeline:

```text
existing normalized Moodle RemoteFile
        -> MoodleFileAuthorizer
        -> BrowserAuthenticatedFetcher
        -> temporary PDF bytes
        -> size, content, and PDF validation
        -> SHA-256 and page extraction
        -> ContentBlobStore atomic install
        -> per-document SQLite transaction
        -> Document / DocumentVersion / DocumentPage metadata
```

The Phase 2 browser request policy remains unchanged. Phase 3 authorization is
not a new permission available to Phase 2 collection, and Phase 3 does not add
an API/browser fallback.

## 3. Design goals and non-goals

Phase 3 must:

- download only a known, eligible Moodle `RemoteFile` through the established
  authenticated Edge context;
- reject arbitrary URLs and independently validate every redirect;
- enforce a hard accepted/stored PDF limit of 50 MiB;
- validate bytes rather than trusting filename or server media type;
- preserve immutable original PDF bytes in content-addressed storage;
- globally deduplicate identical content while preserving source provenance;
- create a new version only when source bytes change;
- extract deterministic page-level embedded text and geometry;
- keep filesystem and SQLite state coherent through atomic installation and
  explicit compensation;
- isolate failures per document while returning a nonzero batch result when
  any selected document fails; and
- use disposable database and content-store paths for tests and acceptance.

Phase 3 v1 does not:

- change Phase 1 normalization or Phase 2 collection behavior;
- download files during normal academic synchronization;
- accept a caller-supplied URL;
- export cookies or browser storage to another HTTP client;
- automate SSO, passwords, or MFA;
- support formats other than PDF;
- implement OCR, translation, summarization, chunking, embeddings, RAG, or
  PixelRAG;
- create background schedules or destructive retention commands;
- use ETag or Last-Modified conditional refresh; or
- write to the normal AAOS database or content store during development or
  acceptance.

## 4. Chosen architecture

The pipeline uses small synchronous components with one responsibility each.

### Phase3IngestionService

Orchestrates selection, locking, authorization, fetch, validation, extraction,
storage, persistence, retry decisions, compensation, and batch reporting. It
does not parse Moodle URLs, PDF bytes, or SQL rows itself.

### IngestionLock

Provides one application-owned Phase 3 mutation lock. It prevents concurrent
version allocation, blob installation, and document mutation jobs. A second
job fails before any fetch, database write, or filesystem mutation.

### MoodleFileAuthorizer

Converts one known normalized Moodle `RemoteFile` into a narrow authorization
for its resource route and validates each subsequent redirect. It never accepts
an arbitrary URL as authority.

### BrowserAuthenticatedFetcher

Uses the existing dedicated Edge/Playwright `BrowserContext` to perform the
approved authenticated request. It does not export cookies, storage state,
headers, or credentials, and it does not expose a general browser request
surface to callers.

### PdfValidator

Validates the actual byte length, PDF signature/content consistency, selected
PDF-engine open/parse behavior, and password requirement. Filename and
`Content-Type` are advisory observations only.

### PdfExtractor

Uses the selected PDF engine to produce page count, embedded Unicode text, and
page dimensions for every page in order. The engine is hidden behind the
application-owned `PdfProcessor.process(body: bytes) -> ParsedPdf` boundary;
no engine-specific type escapes it.

### PDF engine selection

Phase 3 selects `pypdfium2==5.13.0`, backed by PDFium. The dependency is
permissively licensed according to its official package metadata (Apache-2.0,
BSD-3-Clause, and dependency licenses). Task 1 must verify the pinned package
metadata and preserve the PDFium and third-party license/notice material
required for any future binary distribution. This is dependency governance,
not a redesign of AAOS repository licensing.

### ContentBlobStore

Owns temporary artifacts, SHA-256 storage keys, root confinement, atomic blob
installation, idempotent existing-blob handling, and rollback compensation.

### SQLite persistence layer

Owns `Document`, `ContentBlob`, `DocumentVersion`, and `DocumentPage` rows and
per-document transactions. It uses the existing SQLAlchemy/Alembic
architecture; PDF bytes are never stored as SQLite BLOBs.

No generic repository inheritance hierarchy, event bus, background worker, or
plugin system is introduced.

## 5. Authentication and browser boundary

Authenticated Phase 3 fetches remain inside the existing dedicated persistent
Edge profile and Playwright `BrowserContext`:

```text
human SSO/MFA
        -> dedicated AAOS Edge profile
        -> authenticated BrowserContext
        -> one authorized Phase 3 resource fetch
```

The established profile ownership and profile lock remain separate from the
Phase 3 ingestion lock. The ingestion job acquires the ingestion lock before it
begins selection or fetch work, then uses the existing browser-profile lock
contract when opening the context. The browser context closes before the
profile lock is released; the ingestion lock is released only after all
document work and cleanup finish.

Forbidden authentication behavior includes:

- cookie or storage-state export;
- reconstructing authenticated Moodle requests in `httpx`, `requests`, or
  another client;
- password or MFA automation;
- browser database inspection; and
- credential persistence outside the established browser boundary.

## 6. Selection and download authorization

### Eligible source

An ingestion selection is a database identity for an existing active,
normalized Moodle `RemoteFile`. The selected row must retain its valid parent
`Course` relationship and known Moodle Resource identity. An inactive or
missing `RemoteFile` is not fetched; existing document history remains intact.

The selector accepts only database identity. It never accepts a URL, path,
hostname, Moodle function name, or arbitrary browser operation.

### Authorized resource chain

Authorization begins at the known Moodle Resource route:

```text
/mod/resource/view.php?id=<known-source-id>
```

The configured canonical Moodle origin and canonical application base path are
authoritative. The resource ID must match the selected `RemoteFile` source
identity. Base-path stripping occurs exactly once, and prefix-confusion paths
such as `/currentevil/...` are rejected.

A permitted fetch has exactly one of these three controlled shapes:

1. the exact authorized Resource URL returns a direct, non-HTML file body;
2. the authorized Resource URL returns an HTTP redirect chain ending at the
   corresponding same-origin
   `pluginfile.php/.../mod_resource/content/...` continuation; or
3. the exact authorized Resource URL returns an HTTP 2XX HTML Resource wrapper
   containing exactly one href that passes the existing permit-specific
   `MoodleFileAuthorizer` `pluginfile.php/.../mod_resource/content/...`
   continuation policy, followed by one request to that continuation.

The first shape is an exact equality check against the permit's internally
constructed URL. A direct HTTP 2XX response is not accepted as a file merely
because of its status or metadata: an HTML response at that URL is handled
only as the controlled Resource wrapper in shape three. None of these shapes
authorizes another Resource cmid, another Resource route, an arbitrary
same-origin 2XX response, or any caller-supplied URL.

For the Resource wrapper shape, HTML is parsed structurally to discover anchor
href candidates; anchor text, CSS classes, filenames, and response metadata do
not authorize a target. Every candidate is checked through the existing
permit-specific `MoodleFileAuthorizer` policy. Unauthorized or unrelated links
are rejected/ignored, and exactly one authorized continuation is required:
zero candidates fail closed and multiple candidates fail closed as ambiguous.
Arbitrary Moodle links, external URLs, `mod_folder` pluginfiles, and
favicon/core/theme pluginfiles remain unauthorized. Callers still cannot supply
an arbitrary `pluginfile` URL. The wrapper path performs one controlled
continuation fetch only; it never crawls or recursively follows another HTML
wrapper.

When Moodle returns a redirect, it may resolve the resource to a same-origin
`pluginfile` target. A `pluginfile` request is authorized only when all of
these conditions hold:

1. the chain began from the selected known resource route;
2. the resource identity matches the selected `RemoteFile`;
3. the destination uses HTTPS and the exact configured Moodle origin;
4. the destination remains beneath the canonical Moodle application base;
5. the target matches the fixed Phase 3 `pluginfile` allowlist; and
6. every redirect hop is examined and authorized before it is followed.

Automatic unrestricted redirect following is disabled. Redirect cycles,
unexpected intermediate routes, HTTP downgrade, and any hop that cannot be
positively classified fail closed.

Rejected destinations include:

- a direct or caller-supplied `pluginfile` URL;
- an unknown resource ID;
- another Resource cmid or Resource route;
- external or cross-origin URLs;
- unsupported Moodle routes or unrelated `pluginfile` targets;
- HTTP downgrade;
- `file:`, `data:`, or `javascript:` schemes; and
- localhost, loopback, link-local, or private-network pivots.

This is a Phase-3-only capability. Phase 2 request allowlists continue to reject
downloads and `pluginfile` access.

## 7. Fetch and size contract

The fixed Phase 3 v1 maximum accepted/stored PDF size is:

```text
MAX_PDF_BYTES = 50 MiB = 52,428,800 bytes
```

Playwright's authenticated request API does not provide the same streaming
control as a dedicated streaming HTTP client. The size contract is therefore:

1. if a valid `Content-Length` is available and exceeds `MAX_PDF_BYTES`, reject
   before body processing;
2. otherwise receive the body through the authenticated browser-context
   request path;
3. measure the actual byte length; and
4. reject any body larger than `MAX_PDF_BYTES` before hashing, storage, or
   persistence.

The limit is a hard accepted/stored-file limit, not a guaranteed network-stream
cutoff. A missing, malformed, or smaller `Content-Length` never replaces actual
byte-length verification.

Response bodies are temporary sensitive academic data. They are not logged,
placed in evidence, or retained outside the temporary artifact and final
content-addressed blob paths.

## 8. PDF validation and filename handling

Phase 3 v1 accepts PDF only. A candidate succeeds only when:

- actual size is within the hard limit;
- bytes are consistent with PDF content;
- the selected PDF engine opens and parses the document successfully; and
- the document does not require a password.

HTTP 2XX from the initial Resource URL is transport success only, not proof
that the body is a PDF. The fetched bytes must still pass the size and
PDFium-backed validation below; filename, icon, and `Content-Type` metadata
remain advisory.

HTML login pages, portal errors, renamed ZIPs or images, malformed payloads,
and other disguised content are rejected. Server `Content-Type`, URL suffix,
and filename extension are advisory and cannot authorize content.

A password-required PDF fails with `PDF_ENCRYPTED`. Phase 3 never guesses,
requests, stores, or bypasses a PDF password. A technically encrypted PDF that
the selected PDF engine can open normally without a password may proceed.

The source filename is display metadata only. Sanitization:

- reduces input to a basename;
- removes control characters and path separators;
- replaces Windows-reserved filename characters;
- rejects `.` and `..`, drive-qualified, UNC, absolute, and empty results;
- trims trailing dots and spaces and applies a bounded metadata length; and
- never influences the content-store path.

If response metadata does not yield a safe source filename, the already
validated `RemoteFile` display title may provide a sanitized metadata name.
The physical blob location always derives solely from SHA-256.

## 9. Content-addressed storage

PDF bytes live in filesystem storage, conceptually rooted at:

```text
data/documents/
```

The root is typed configuration and must be validated before use. Tests and
acceptance inject fresh disposable roots; they never use the production
default.

The storage key format is:

```text
sha256/<first-two-hex>/<full-lowercase-sha256>.pdf
```

For example:

```text
sha256/ab/abcdef0123456789...pdf
```

SQLite stores only this safe relative key. Absolute paths, `..`, alternate data
streams, symlinks/reparse-point escapes, and resolved paths outside the
configured root fail closed.

Blob installation writes a temporary file beneath an owned temporary area,
flushes and closes it, then atomically installs it at the final hash path. An
existing final path is accepted only when it is the expected regular owned file
and its bytes match the expected hash and size. Temporary names never become
database storage keys.

## 10. Persistence model

The approved ownership model is:

```text
Course
  -> RemoteFile
       -> Document (1:1)
            -> DocumentVersion (1:N)
                 -> ContentBlob (N:1)
                 -> DocumentPage (1:N)
```

### Document

`Document` represents the logical ingested document for one `RemoteFile`.

Conceptual fields:

- `id`;
- `remote_file_id`, required and unique;
- `ingestion_status`;
- `last_attempted_at`, optional aware UTC;
- `last_error_code`, optional fixed safe code;
- `created_at`, aware UTC; and
- `updated_at`, aware UTC.

Statuses are `NEVER_ATTEMPTED`, `READY`, and `FAILED`.

- `NEVER_ATTEMPTED` means no fetch attempt has completed.
- `READY` means at least one successful `DocumentVersion` exists.
- `FAILED` means the document has no successful version and its latest attempt
  failed.

A failed refresh of an already-ready document does not erase historical
readiness or versions: status remains `READY`, while `last_attempted_at` and
`last_error_code` record the latest failed operation. A later success clears
`last_error_code`.

`Document` retains only the latest operational attempt state. Phase 3 v1 does
not add a separate ingestion-attempt history table.

If the source `RemoteFile` becomes inactive or disappears, the `Document` and
all historical versions remain. Phase 3 performs no automatic purge or hard
deletion.

### ContentBlob

`ContentBlob` represents one immutable globally deduplicated PDF payload.

Conceptual fields:

- `id`;
- `sha256`, required and unique, lowercase 64-character hexadecimal;
- `size_bytes`, required and within `1..MAX_PDF_BYTES`;
- `media_type`, required and fixed to `application/pdf` in v1;
- `storage_key`, required and unique;
- `created_at`, aware UTC.

SHA-256 is canonical content identity. Blob rows are immutable after creation.
Different documents may share one blob.

### DocumentVersion

`DocumentVersion` represents one distinct observed PDF revision for one
`Document`.

Conceptual fields:

- `id`;
- `document_id`, required foreign key;
- `version_number`, required positive integer;
- `content_blob_id`, required foreign key;
- `source_filename`, required sanitized display metadata;
- `ingested_at`, required aware UTC;
- `extraction_status`, required;
- `page_count`, required positive integer.

Constraints:

- `UNIQUE(document_id, version_number)`;
- `UNIQUE(document_id, content_blob_id)`; and
- version numbers are contiguous and monotonically allocated as `1, 2, 3, ...`
  while the single-writer lock is held.

A changed filename alone does not create a version. Only a changed PDF SHA-256
does.

### DocumentPage

`DocumentPage` is the derived page representation for one version.

Conceptual fields:

- `id`;
- `document_version_id`, required foreign key;
- `page_number`, required positive integer;
- `text`, required Unicode text, possibly empty;
- `text_source`, required and fixed to `EMBEDDED_PDF` in v1;
- `width_points`, required positive finite number;
- `height_points`, required positive finite number; and
- `created_at`, required aware UTC.

Constraint:

- `UNIQUE(document_version_id, page_number)`.

Rows use page numbers `1..N`. Every PDF page gets exactly one row, including a
page with no embedded text.

## 11. Deduplication and versioning semantics

SHA-256 globally deduplicates content while `Document` and `DocumentVersion`
preserve source provenance.

| Observation | Result |
| --- | --- |
| Same `RemoteFile`, same SHA-256 | No new version and no duplicate blob. |
| Same `RemoteFile`, different SHA-256 | Create the next immutable version. |
| Different `RemoteFile`s, same SHA-256 | Separate documents and versions share one blob. |
| Filename changes, bytes unchanged | No new version. |

An unchanged refresh may update `Document.last_attempted_at` and clear a prior
safe error, but it does not rewrite immutable version, page, or blob rows.

## 12. Page extraction and extraction status

For each accepted version, the application-owned PDF adapter uses PDFium to
produce:

```text
immutable PDF
        -> page count
        -> page 1..N
        -> embedded Unicode text
        -> width/height in PDF points
        -> DocumentPage rows
```

Phase 3 preserves Unicode and does not translate, summarize, aggressively
normalize Arabic, silently repair text, chunk for retrieval, or call an LLM.
Each page's full embedded Unicode text is extracted with
`PdfTextPage.get_text_bounded()`; the UCS-2-limited range convenience API is
not used.

Extraction status is deterministic:

- `SUCCESS`: every page has nonblank text after a whitespace-only usability
  check;
- `PARTIAL`: at least one page has usable text and at least one page does not;
- `NO_TEXT`: no page has usable embedded text.

All three statuses are successful ingestions. Empty pages retain empty text.
A PDF that cannot be parsed creates no successful `DocumentVersion`; the
failure is recorded on `Document` instead.

PDF bytes and SHA-256 are source truth. Page text is derived and regenerable.
A future approved re-extraction may replace derived pages without creating a
new version. No generic reprocessing framework is introduced in v1.

The original bytes, stable version and page numbering, page geometry, embedded
text, and source/course provenance intentionally preserve the substrate needed
for later OCR or visual retrieval. Those later operations do not belong to
Phase 3 and cannot create a new version unless the PDF bytes change. If a later
phase approves page rendering, it must use the same bounded PDFium adapter or
an explicitly reviewed replacement; Phase 3 creates no rendered images,
indexes, or visual-derived artifacts. The preserved future boundary is:

```text
DocumentPage
        -> render corresponding immutable PDFium page
        -> visual embedding
        -> PixelRAG-style retrieval
```

## 13. Trigger and command semantics

The conceptual operator interface is:

```text
aaos documents ingest-new
aaos documents ingest-new --remote-file-id <database-id>
aaos documents refresh
aaos documents refresh --remote-file-id <database-id>
```

`--remote-file-id` selects an existing database row. No URL selector exists.

### ingest-new

- selects eligible active Moodle `RemoteFile`s without a successful version;
- fetches and ingests those selected resources;
- skips already-successful documents without downloading them; and
- does not refresh unchanged documents by default.

### refresh

- selects eligible active Moodle `RemoteFile`s, including ready documents;
- intentionally re-fetches each selection;
- reports identical SHA-256 as unchanged; and
- creates the next version for changed SHA-256.

Both modes create one bounded sequential job. There is no retry loop at the
command level, automatic mode fallback, background schedule, arbitrary URL
input, or destructive delete/purge command.

## 14. Atomic ingestion and compensation

The success path for one document is:

```text
authorize source and chain
        -> fetch temporary bytes
        -> verify actual size
        -> compute SHA-256
        -> validate PDF and encryption state
        -> extract all page records
        -> atomically install/reuse ContentBlob bytes
        -> one SQLite transaction for blob metadata, version, pages, and Document state
        -> successful version becomes visible
```

Until the final transaction commits, ingestion is not successful.

Filesystem and SQLite cannot share one ACID transaction, so the service uses
explicit compensation:

- failures before blob installation remove temporary artifacts and create no
  version;
- a newly installed blob whose database transaction fails is removed only if
  this ingestion created it and it remains unreferenced;
- a pre-existing or shared blob is never deleted during compensation;
- an existing successful version is never removed by a failed refresh; and
- the document's latest attempt metadata is updated in a separate safe
  transaction after cleanup when the primary transaction did not commit.

If safe failure-state persistence itself fails, the command reports
`PERSISTENCE_FAILED`; it must not invent a successful version. Cleanup is
idempotent and must not hide the primary fixed failure category.

## 15. Failure codes and retry policy

Persist only fixed non-sensitive failure codes. The closed v1 set includes:

- `NETWORK_TIMEOUT`;
- `NETWORK_FAILURE`;
- `DOWNLOAD_TOO_LARGE`;
- `UNAUTHORIZED_REDIRECT`;
- `UNAUTHORIZED_ROUTE`;
- `INVALID_PDF`;
- `PDF_ENCRYPTED`;
- `PDF_PARSE_FAILED`;
- `STORAGE_FAILED`; and
- `PERSISTENCE_FAILED`.

PDF processing maps failures at deterministic boundaries:

- `INVALID_PDF`: initial `PdfDocument(body)` load/content recognition fails for
  a reason other than password requirement;
- `PDF_ENCRYPTED`: initial load reports PDFium's password-required error code;
- `PDF_PARSE_FAILED`: after a successful document load, page count, page open,
  geometry, or embedded-text extraction fails.

Raw exception strings, response bodies, cookies, authorization headers,
sesskeys, complete sensitive `pluginfile` URLs, and extracted text are not
persisted or logged. Native PDFium/pypdfium2 exception text is never exposed.

Only transient network timeouts/failures receive automatic retries:

```text
initial attempt + at most 2 retries
```

Each retry starts from clean temporary state. Authorization, redirect-policy,
size, PDF, encryption, parsing, storage, and persistence failures are not
automatically retried. Retries remain inside the same selected document
operation and cannot allocate versions or blobs until successful validation.
Network operations, lock acquisition, browser startup, and cleanup all remain
bounded by implementation-owned typed timeouts; there is no unbounded wait.

## 16. Single-writer and batch behavior

The Phase 3 ingestion lock is acquired before source selection and is held
through browser closure, compensation, batch summary construction, and final
cleanup. A second process is rejected before fetch or mutation. Active locks
are never silently removed; stale-lock handling uses explicit repository-safe
ownership semantics.

Each document has its own persistence transaction. An invalid source does not
roll back successful independent documents:

```text
A -> READY
B -> FAILED (INVALID_PDF)
C -> READY
```

The command continues sequentially after a per-document failure when it is safe
to do so and exits nonzero if any requested document failed.

The deterministic summary contains counts only:

- `REMOTE_FILES_SELECTED`;
- `INGESTED`;
- `UNCHANGED`;
- `FAILED`;
- `SKIPPED`;
- `NEW_VERSIONS`; and
- `DEDUPLICATED_BLOBS`.

`ingest-new` reports existing successful documents as `SKIPPED` without a
download. `refresh` reports identical content as `UNCHANGED`.

## 17. Course provenance and observability

Course categorization is deterministic:

```text
DocumentPage
        -> DocumentVersion
        -> Document
        -> RemoteFile
        -> Course
```

No AI classification guesses course membership. Semantic labels such as
lecture, textbook, lab, paper, or exam review are future enrichment scope.

Safe operational logs may contain:

- internal `remote_file_id`;
- fixed status and error code;
- version number;
- size and page count;
- extraction status;
- deduplicated boolean; and
- aggregate batch counts.

Logs must not contain source bytes, page text, unsanitized filename data,
cookies, auth headers, sesskeys, storage state, complete `pluginfile` URLs,
response bodies, or arbitrary exception text.

## 18. Configuration

Security-sensitive policy is typed configuration, not a weakening CLI option.
Fixed Phase 3 v1 policy is:

```text
FILE_TYPE = PDF_ONLY
MAX_PDF_BYTES = 52,428,800
MAX_TRANSIENT_RETRIES = 2
```

There are no options such as `--allow-any-host`, `--allow-any-route`, or an
operator-supplied oversized limit. The content-store root is injectable through
typed AAOS configuration and must pass strict absolute-root, ownership,
confinement, and reparse/symlink validation. Database and store paths used by
tests and acceptance are explicitly disposable.

## 19. Persistence and migration requirements

Phase 3 uses one forward Alembic migration on the existing SQLAlchemy schema.
It adds the four Phase 3 tables, foreign keys, uniqueness constraints, enum or
check constraints, and indexes needed for:

- `Document.remote_file_id` lookup;
- global blob hash lookup;
- per-document version ordering;
- per-version page ordering; and
- provenance joins back to `RemoteFile` and `Course`.

Upgrade and downgrade must be deterministic on a disposable database. The
migration does not modify frozen Phase 1 identity or synchronization
constraints and does not add Moodle-specific tables.

## 20. Testing strategy

### Deterministic PDF fixtures

Fixtures cover:

- valid one-page text PDF;
- valid multi-page text PDF;
- mixed usable-text and empty/image-like pages;
- valid completely no-text/scanned-style PDF;
- malformed PDF;
- non-PDF bytes renamed `.pdf`;
- password-required encrypted PDF;
- exact and over-limit size behavior;
- identical bytes under different names; and
- changed bytes for the same `RemoteFile`.

### Download authorization

Tests prove all three successful fetch shapes for a known `RemoteFile`:

- the exact authorized initial Resource URL with a direct non-HTML HTTP 2XX
  response;
- the approved Resource-to-`pluginfile` redirect continuation; and
- an HTTP 2XX Resource HTML wrapper with exactly one authorized
  `mod_resource/content` anchor followed by that continuation.

Wrapper tests also prove that favicon/unrelated links are ignored, zero or
multiple authorized candidates fail closed, and external, `mod_folder`, and
other-Resource links are rejected.

They reject:

- direct/arbitrary `pluginfile`;
- another Resource cmid or Resource route;
- unknown resource identity;
- cross-origin or external redirect;
- HTTP downgrade;
- `file:`, `data:`, and `javascript:`;
- localhost/private-network pivot;
- unsupported Moodle route; and
- redirect cycles or unclassifiable hops.

They also prove the frozen Phase 2 browser policy gains no download or
`pluginfile` capability.

### Storage security

Tests prove:

- traversal or hostile filenames cannot affect storage location;
- no absolute path is persisted;
- resolved writes cannot escape the configured root;
- Windows symlink/reparse and prefix-confusion attacks fail closed;
- temporary artifacts cannot become final accidentally; and
- atomic installation and idempotent existing-blob handling work.

### Deduplication and versioning

Tests prove:

- same document and bytes produce one version and one blob;
- changed bytes produce the next version;
- different documents with the same bytes retain separate provenance and one
  blob; and
- filename-only changes do not create versions.

### Failure injection and compensation

Inject download, validation, parsing, storage, and post-install database
failures. Prove:

- no fake successful version;
- no corrupt final blob;
- no temporary leftovers;
- a newly created unreferenced orphan is removed;
- a shared or pre-existing blob is preserved; and
- document readiness/history survives failed refresh.

### Retries

Tests prove timeout-then-success, all transient attempts failing, clean temp
state between attempts, and zero retries for deterministic policy/content
failures.

### Extraction and provenance

Tests prove page count, `1..N` numbering, dimensions, Unicode and mixed
Arabic/English persistence, empty page rows, `EMBEDDED_PDF`, and deterministic
`SUCCESS`/`PARTIAL`/`NO_TEXT`. Provenance must traverse from page to course.

### Single writer and batch behavior

Tests prove a second job is rejected before fetch/mutation, locks release after
owned resources close, independent documents commit separately, failures
produce a nonzero command result, and summary counts are deterministic.

### Migration and side-effect safety

Tests prove migration upgrade/downgrade on disposable SQLite, import safety,
no network/browser/database activity at import, and absence of writes to
`data/database/aaos.db` and `data/documents/`.

## 21. Live Phase 3 acceptance

After deterministic gates pass, exactly one human-authorized live acceptance
uses one currently available PDF `RemoteFile`, the existing authenticated Edge
context, a fresh disposable migrated SQLite database, and a fresh disposable
content-store root. It does not ingest the whole Moodle corpus.

The privacy-safe acceptance evidence is:

```text
LIVE_REMOTE_FILE_AUTHORIZATION=PASS
AUTH_CONTEXT=EXISTING_EDGE
COOKIE_EXPORT=NONE

DOWNLOAD=PASS
REDIRECT_POLICY=PASS
FINAL_ORIGIN=APPROVED_MOODLE
PDF_SIZE_LIMIT=PASS
PDF_VALIDATION=PASS

SHA256_COMPUTED=PASS
CONTENT_BLOB_INSTALL=PASS

PDF_EXTRACTION=PASS
PAGE_COUNT=<current actual>
EXTRACTION_STATUS=<SUCCESS|PARTIAL|NO_TEXT>

DOCUMENT_CREATED=1
DOCUMENT_VERSION_CREATED=1
CONTENT_BLOB_CREATED=1
PAGE_ROWS=<current actual>

REFRESH_SAME_BYTES=PASS
NEW_VERSION_CREATED=NO
DEDUPLICATION=PASS

DISPOSABLE_DATABASE_REMOVED=YES
DISPOSABLE_CONTENT_STORE_REMOVED=YES

NORMAL_DATABASE_TOUCHED=NO
NORMAL_CONTENT_STORE_TOUCHED=NO

MOODLE_WRITES=NONE
COOKIE_EXPORT=NONE

LIVE_PHASE_3_ACCEPTANCE=PASS
```

The acceptance does not hard-code filename, page count, byte size, or a
historical Moodle count. It proves correct ingestion of the selected current
approved source. Real ingestion into the normal AAOS database/content store
requires separate human authorization after Phase 3 acceptance.

## 22. Phase acceptance criteria

Phase 3 is ready to freeze only when all of these properties are proven:

1. approved current PDF resources are fetched only through the authenticated
   Phase 3 authorization chain;
2. new files ingest once and unchanged refreshes create no duplicate version;
3. identical bytes globally deduplicate while source provenance remains
   separate;
4. changed bytes create the next immutable version;
5. filenames cannot cause path traversal or determine storage location;
6. size, actual PDF content, encryption, and parseability are validated;
7. invalid, disguised, oversized, and password-required files are rejected;
8. interrupted or failed work leaves no corrupt final file or fake version;
9. filesystem/SQLite compensation removes only newly created unreferenced
   blobs and preserves shared blobs;
10. page number, text, geometry, source filename, source file identity, and
    course provenance are preserved;
11. `SUCCESS`, `PARTIAL`, and `NO_TEXT` are deterministic successful extraction
    outcomes;
12. one resource failure does not roll back independent successes;
13. the single-writer and browser-profile locks fail closed;
14. Phase 2 retains no download or `pluginfile` permission;
15. deterministic and one-file live acceptance gates pass; and
16. normal database and content-store paths remain untouched throughout tests
    and acceptance.

These criteria concretely satisfy the Phase 3 roadmap requirements for
approved-domain downloads, sanitization, MIME/size validation, atomic writes,
SHA-256 deduplication, PDF/page extraction, versioning, course categorization,
duplicate prevention, traversal rejection, invalid-file rejection, interrupted
download safety, and page/source metadata preservation.

## 23. Final deterministic gates

Before Phase 3 freeze, run:

```text
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv lock --check
git diff --check
```

The evidence must also prove that `data/database/aaos.db` and
`data/documents/` were absent or untouched as required. Phase 3 cannot freeze
while any documented acceptance requirement is unproven.

## 24. Explicit exclusions and future boundaries

Phase 3 v1 excludes:

- non-PDF formats;
- OCR and password handling;
- image or visual embeddings and PixelRAG;
- RAG chunking, vector databases, embeddings, retrieval, and LLM inference;
- summaries, translation, semantic document classification, or study-material
  generation;
- generic reprocessing infrastructure;
- ETag/Last-Modified optimization or partial download resume;
- background scheduling, distributed workers, or concurrent writers;
- deletion, purge, retention, or garbage-collection commands;
- arbitrary authenticated browsing/downloading;
- Moodle writes; and
- normal-database/content-store acceptance writes.

Future OCR, visual retrieval, and textual retrieval operate on the immutable
source bytes and stable page/version provenance established here. Derived-data
changes do not alter document version identity.

## 25. Governance housekeeping debt

`README.md`, `docs/PROJECT_SPEC.md`, and `docs/ARCHITECTURE.md` contain stale
project-phase status statements. They are not changed by this design commit.
Updating them is separate governance housekeeping and must not be combined with
Phase 3 implementation or used to reinterpret frozen Phase 1/Phase 2 behavior.
