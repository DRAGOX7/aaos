# Phase 9 domain-specific evaluation protocol

The Phase 9 evaluation is deterministic and synthetic-only. Its frozen
calibration and heldout memberships are hashed before execution; ordinary
pytest can execute calibration fixtures only. Real `hold-001` through
`hold-024` identities are rejected by the normal harness and remain a
separately authorized release operation.

Metrics are reported independently:

- retrieval recall@k;
- unsupported-claim rate and NLI confusion;
- citation accuracy;
- agent/tool authority-negative behavior;
- multimodal selection and provenance kind;
- computation correctness/security.

No blended accuracy or security-quality aggregate is produced. BGE-M3
relevance remains distinct from NLI entailment, and `EMBEDDED_PDF` remains
distinct from `DERIVED_VISUAL_EVIDENCE`. The default adapters make zero model
calls, zero RAG calls, and zero external writes. Any future local-model probe
must be a separately labelled capability report and cannot change membership,
thresholds, or heldout authorization.
