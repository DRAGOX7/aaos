# Phase 9 threat model

This document records the release-qualification threat model for the frozen
AAOS system. It is a review and evidence map, not a claim that any risk has
been eliminated. Phase 9 adds deterministic verification harnesses only; it
does not add authority, providers, browser routes, or external writes.

| Asset or entry point | Trust boundary and existing mitigation | Phase 9 verification | Residual risk |
| --- | --- | --- | --- |
| Moodle cookies, auth state, and redirects | Local authentication/runtime and read-only adapter; secrets excluded from model and logs | Auth/error/log redaction and expired-session fixtures | Connected-account setup remains user-specific. |
| Google OAuth credentials and tokens | Phase 6 adapter boundary; never model- or browser-visible | Tree, log, browser scans and fake adapter tests | One historical real CREATE is evidence, not ongoing provider availability. |
| Environment secrets | Process/configuration boundary | Safe configuration diagnostics and secret-sentinel scans | Operators can still misconfigure their environment. |
| Document, retrieved, and visual prompt injection | Content is untrusted data; closed proposals and validators | Frozen text/visual/tool-observation corpus | Semantic novelty beyond the corpus remains residual risk. |
| Malicious PDFs and images | Bounded parser, renderer, and vision inputs | Malformed, corrupt, and oversize fixtures with cleanup checks | Parser and library vulnerabilities remain dependency risk. |
| Arbitrary computation | Docker-qualified, network-none, read-only-root, cap-drop, non-root sandbox | Real Docker qualification plus fake quota/crash suite | Target hardware/runtime availability limits execution. |
| Filesystem traversal, symlink, and junction attacks | Trusted path containment and bounded task roots | Windows path/symlink/junction corpus | Platform-specific filesystem behavior requires local qualification. |
| Browser-to-backend and SSE leakage | Same-origin proxy, typed API errors, passive Activity | Route inventory, header/error scans, and SSE reconnect tests | Deployment proxy configuration remains environmental. |
| Approval and action authority | Phase 6 immutable records, approver context, atomic claim and reconciliation | Fake-only prepare/approve/claim/execute/reconcile matrix | Real provider semantics are covered only by the accepted narrow integration. |
| Malformed provider and model outputs | Closed contracts, sanitizers, and bounded errors | Malformed response and late-result tests | Unobserved provider variants require future adapter work. |
| Replay, race, and ambiguous outcome | One-shot operation identity, CAS, and UNKNOWN_OUTCOME | Concurrent, late, and crash matrix | Reconciliation still requires trusted operator action. |
| SQL, path, traceback, and raw-payload leakage | Error boundary and typed event/detail contracts | Response, log, and artifact scans | Internal diagnostics remain bounded rather than exhaustive. |
| Dependencies and model revisions | Lockfiles and explicit model identity/revision | Dependency inventory and hash manifest | Unbounded ranges and externally managed model artifacts are disclosed. |

## Corpus rule

The Task 1 corpus contains synthetic inert sentinels only. Every fixture is
expected to remain `DATA_ONLY`: it cannot grant tool identity, scope,
permission, approval, citation ownership, computation privilege, provider
arguments, or external-write authority. Corpus evaluation performs no model,
RAG, provider, filesystem, network, or subprocess execution.

## Review rule

Each row must be backed by the exact executed evidence in the Phase 9 release
report. A written test is not, by itself, evidence that a threat is secure;
the report must name what ran and disclose the remaining limitation.
