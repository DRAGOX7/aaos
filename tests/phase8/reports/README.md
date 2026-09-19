# Phase 8 evidence retention

Phase 8 evaluation evidence is deterministic and synthetic-only. Retained
reports may include bounded counters, frozen manifest hashes, migration-head
identifiers, and security-gate results.

Do not retain model outputs, prompts, raw worker output, provider payloads,
credentials, authorization state, host paths, or other secrets in this
directory. Real heldout execution is not reachable from ordinary pytest.
