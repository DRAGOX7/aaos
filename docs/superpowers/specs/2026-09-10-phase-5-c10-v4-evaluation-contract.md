# Phase 5 C10 — V4 Routing Acceptance Contract

## Status and scope

This document records the human-approved C10 numeric routing policy and its
versioned V4 evaluation contract. It is governance and evaluation scope only;
it does not authorize model inference, held-out execution, prompt tuning,
production orchestrator changes, or Phase 6 work.

The frozen V1, V2 and V3 schemas, manifests, reports, fingerprints and held-out
artifacts remain immutable. V4 is a new contract with schema version
`phase5-agent-benchmark-v4`.

## C10 numeric policy

The model-routing domain is a capability/quality domain, separate from trusted
system and security gates. The policy is:

| Parameter | Value |
| --- | --- |
| Calibration minimum routing N | 64 |
| Held-out minimum routing N | 64 |
| Global routing accuracy target | >= 0.95 |
| Minimum passing numerator at N=64 | 61/64 |
| Maximum misses at N=64 | 3 |
| English / Retrieve coverage | >= 16 |
| English / Terminal coverage | >= 16 |
| Arabic / Retrieve coverage | >= 16 |
| Arabic / Terminal coverage | >= 16 |

Coverage minima are hard structural requirements. Global routing accuracy is
the acceptance gate; subgroup accuracy is reported diagnostically and is not a
second hard gate. There is no asymmetric false-terminal gate. Security and
trusted-system gates remain independent, zero-tolerance requirements, and
proposal-format efficiency remains a separate diagnostic domain.

C10 is an engineering evaluation policy for this local/portfolio system. It is
not population-reliability certification, medical or aviation assurance, or a
statistical confidence guarantee. The policy was selected from architecture,
risk and sample-size semantics, not from current model results:
`C10_POLICY_SELECTED_FROM_CURRENT_MODEL_RESULTS=NO`.

## Outcome-independent routing authoring

The frozen authoring specification is `c10-routing-matrix-v1`. Each routing
case tests only the C8 semantic responsibility:

* `RETRIEVE` when a clear answerable information need requires trusted local
  knowledge; or
* `TERMINAL_TEXT` when bounded non-grounded behavior is sufficient without
  consulting trusted local knowledge.

Calibration and held-out routing cases are authored from a deterministic,
balanced matrix (language, action, directness and request form), with 16 cases
per required stratum in each split. They are independently instantiated and
must have disjoint IDs and zero normalized-content duplicates under the
predeclared `NFKC+casefold+collapse-whitespace exact equality` rule. No model
embeddings, model outputs, known failures, prompt experiments, case IDs,
language heuristics, or result filtering may influence authoring.

Every case has exactly one architecture-derived owner. Trusted-system cases are
migrated from the C9 manifests without changing semantic intent; they are not
added to inflate the routing denominator.

## V4 score domains

V4 reports these separate domains:

1. `MODEL_ROUTING` — retrieve/terminal action and applicable terminal kind;
2. `TRUSTED_SYSTEM` — the deterministic condition owned by AAOS;
3. `SECURITY_HARD_GATES` — zero tolerance;
4. `RUNTIME_ENVIRONMENT`;
5. `PROPOSAL_FORMAT_EFFICIENCY` — validity and bounded repair diagnostics.

There is no blended agent-accuracy primary metric. A routing score cannot
compensate for a trusted-system, security, runtime, lineage, permission or
sanitization failure.

At N=64, acceptance uses exact integer comparison: 61/64 passes and 60/64
fails; values are never rounded upward. A split below 64 routing cases or
missing any required coverage stratum is not acceptance-eligible.

## V4 split and held-out protection

V4 creates exactly 64 calibration and 64 held-out model-routing cases, with 32
English / 32 Arabic routing cases in each split and 16 cases in every
action/language stratum. The complete manifests additionally carry migrated
trusted-system cases, so their total language counts are reported separately.
Existing C9 trusted-system material is migrated deterministically and remains
owned by its original trusted condition. V4 held-out is created and fingerprinted
before any V4 calibration execution, but remains unconsumed and invisible until
a separate authorization. A reservation alone does not consume the split.

### One-time held-out consumption and recovery

The one-time V4 split is consumed at the start of protected semantic
evaluation, not when a reservation is acquired. Protected semantic evaluation
begins at the earliest of:

1. the first protected held-out case being materialized or read for evaluation;
2. the first held-out case being provided to model or tool execution;
3. scoring beginning with held-out semantic case data; or
4. production of any protected case-specific result.

The durable ledger uses exactly these states:

- `AVAILABLE` — no held-out semantic evaluation is in progress or consumed.
- `RESERVED` — one runner holds the atomic permit; no protected held-out case
  has been read, materialized, executed, or scored.
- `CONSUMED` — protected semantic evaluation has begun; this state is
  permanent.

`AVAILABLE -> RESERVED` must be atomic so concurrent runners cannot both
start. Before or atomically with the first protected semantic evaluation
operation, the runner must persist `RESERVED -> CONSUMED`. It must never read
or materialize the first protected case and only afterward mark the split
consumed.

A pre-semantic infrastructure failure does not consume the split only when
trusted non-semantic audit metadata proves that no protected held-out case was
read or materialized, no held-out model/tool execution or scoring occurred,
and no protected case-specific result was produced. With that proof, the
runner may release `RESERVED -> AVAILABLE`. The ledger records only safe
metadata and contains no case content.

A failure after the split reaches `CONSUMED` never permits a rerun, regardless
of whether evaluation passes, fails, crashes, times out, or fails to write a
complete aggregate report. `CONSUMED` has no outgoing transition. A stale
`RESERVED` state must not be reset automatically: it may return to `AVAILABLE`
only if trusted non-semantic audit metadata proves no semantic evaluation
began; otherwise fail closed and treat the split as `CONSUMED`. Missing final
output alone is not proof. A corrupt or ambiguous ledger also fails closed;
it must never be interpreted as count zero.

The non-semantic execution count is `0` in `AVAILABLE` or a provably
pre-semantic `RESERVED` state, and `1` in `CONSUMED`. An ambiguous or corrupt
state is unknown and blocks execution. These rules govern only the V4
held-out split and do not change calibration iteration policy.

V3 held-out remains unconsumed and invisible. No V4 model or RAG execution is
part of this contract-creation change.

## Required invariants

`V1_IMMUTABLE=YES`, `V2_IMMUTABLE=YES`, `V3_IMMUTABLE=YES`;
`ONE_CASE_ONE_PRIMARY_OWNER=YES`; `PERFORMANCE_DEPENDENT_MAPPING=NO`;
`SECURITY_HARD_GATES_REMAIN_ZERO_TOLERANCE=YES`;
`MODEL_CALL_COUNT=0`; `RAG_EXECUTION_COUNT=0` during authoring; and
`V4_HELDOUT_EXECUTION_COUNT=0` until separately authorized.

This contract does not freeze a model candidate or change Phase 5 status. Human
review is required before the first V4 calibration.
