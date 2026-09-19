# Phase 9 failure and recovery matrix

This matrix is a release-qualification artifact. Every row is exercised by
the deterministic synthetic runner in `tests/phase9/failure_scenarios.py`;
the runner performs no provider calls, network calls, model calls, RAG calls,
or external writes. It reports bounded states rather than raw exceptions,
paths, SQL, or provider payloads.

| Scenario | Subsystem | Detection / injected condition | Internal state | User-visible state | Retry policy | Data-loss risk | Trusted recovery |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Moodle unavailable | Moodle | connection unavailable | READ_ONLY_FAILURE | ACADEMIC_UNAVAILABLE | NONE | BOUNDED | trusted operator action |
| Moodle session expired | Moodle | session expired | SESSION_EXPIRED | REAUTH_REQUIRED | TRUSTED_RETRY | NONE | trusted re-authentication |
| Moodle malformed response | Moodle | schema mismatch | INVALID_RESPONSE | ACADEMIC_UNAVAILABLE | NONE | BOUNDED | discard and review |
| Document parse failure | Document | parser rejected input | PARSE_FAILED | DOCUMENT_UNAVAILABLE | NONE | BOUNDED | trusted ingestion |
| Corrupt PDF | Document | corrupt bytes | PARSE_FAILED | DOCUMENT_UNAVAILABLE | NONE | BOUNDED | replace source through ingestion |
| Index unavailable | RAG | index unavailable | INDEX_UNAVAILABLE | INSUFFICIENT_EVIDENCE | NONE | BOUNDED | trusted maintenance |
| Embedding failure | RAG | embedding adapter failure | EMBEDDING_FAILED | INSUFFICIENT_EVIDENCE | NONE | BOUNDED | trusted reindex |
| LLM unavailable | RAG | generation unavailable | GENERATION_UNAVAILABLE | INSUFFICIENT_EVIDENCE | NONE | BOUNDED | separately bounded request |
| NLI unavailable | RAG | NLI unavailable | NLI_UNAVAILABLE | INSUFFICIENT_EVIDENCE | NONE | BOUNDED | no unsupported answer |
| VLM unavailable | Vision | adapter unavailable | UNAVAILABLE | VISUAL_UNAVAILABLE | NONE | BOUNDED | continue without visual evidence |
| Docker unavailable | Computation | Docker unavailable | SANDBOX_UNAVAILABLE | COMPUTATION_UNAVAILABLE | NONE | BOUNDED | report unavailable sandbox |
| Worker crash | Computation | worker exits | UNKNOWN_OUTCOME | COMPUTATION_UNKNOWN | RECONCILIATION | REVIEW_REQUIRED | reconcile; never auto-retry |
| Computation timeout | Computation | deadline exceeded | TIMED_OUT | COMPUTATION_TIMEOUT | NONE | BOUNDED | cleanup worker |
| Output limit | Computation | quota exceeded | OUTPUT_LIMIT | COMPUTATION_FAILED | NONE | BOUNDED | discard beyond bound |
| Database lock | Persistence | SQLite lock | PERSISTENCE_CONFLICT | TEMPORARY_FAILURE | NONE | REVIEW_REQUIRED | trusted transaction retry |
| Migration mismatch | Persistence | Alembic head mismatch | SCHEMA_MISMATCH | SERVICE_UNAVAILABLE | NONE | REVIEW_REQUIRED | reconcile environment |
| FastAPI unavailable | Web | backend unavailable | BACKEND_UNAVAILABLE | SERVICE_UNAVAILABLE | NONE | BOUNDED | restart trusted service |
| Next unavailable | Web | frontend unavailable | FRONTEND_UNAVAILABLE | SERVICE_UNAVAILABLE | NONE | BOUNDED | restart trusted service |
| SSE disconnect | Web | stream disconnect | STREAM_DISCONNECTED | ACTIVITY_RECONNECT | NONE | NONE | bounded cursor reconnect |
| Calendar ambiguity | Approval | transport ambiguous | UNKNOWN_OUTCOME | RECONCILIATION_REQUIRED | RECONCILIATION | REVIEW_REQUIRED | reconcile; no auto execution retry |
| Approval revoked | Approval | revoked before claim | REVOKED | ACTION_REJECTED | NONE | NONE | new action and approval |
| Approval expired | Approval | expired before claim | EXPIRED | ACTION_EXPIRED | NONE | NONE | new action and approval |
| Filesystem full | Host | controlled path full | STORAGE_FAILURE | SERVICE_UNAVAILABLE | NONE | REVIEW_REQUIRED | trusted storage repair |
| Permission denied | Host | controlled path denied | PERMISSION_DENIED | SERVICE_UNAVAILABLE | NONE | REVIEW_REQUIRED | trusted permission repair |
| Malformed provider output | Provider | closed schema mismatch | MALFORMED_RESPONSE | SAFE_FAILURE | NONE | BOUNDED | discard and sanitized error |

## Safety conclusions

- `UNKNOWN_OUTCOME` is distinct from confirmed failure and always requires
  trusted reconciliation; no scenario creates an automatic external-write
  retry.
- Approval, capability, claim, dispatch, and reconciliation ownership remains
  in Phase 6. The matrix does not add a write path or a new authority.
- The synthetic evidence records zero real provider calls and zero real
  external writes. Production-provider availability is not inferred from this
  fake qualification.
- Import-inert qualification imports only closed, side-effect-free contracts
  and verifies that a controlled temporary root remains unchanged.
