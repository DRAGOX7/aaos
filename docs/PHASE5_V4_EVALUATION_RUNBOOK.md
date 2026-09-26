# Phase 5 V4 sealed evaluation runbook

This runbook is the operator surface for the sealed V4 manifest, calibration,
and one-time held-out commands. It exposes only aggregate metrics and
non-semantic ledger metadata. Do not open or print V4 case definitions to use
this runbook.

## Candidate prerequisite

The implementation lineage starts at `0966b361def4a5e640cfd59fd3ea82567c3beecb`.
Before any evaluation, obtain the separately approved candidate SHA and verify
the checkout is exactly that commit and clean:

```powershell
$approvedSha = "<paste the separately approved full SHA>"
$actualSha = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actualSha -cne $approvedSha) { throw "Candidate SHA mismatch" }
$changes = git status --porcelain
if ($LASTEXITCODE -ne 0 -or $changes) { throw "Candidate checkout is not clean" }
$actualSha
```

Replace the placeholder from the approval record and retain the printed full
SHA. `git status --porcelain` must return no lines. Manifest, calibration,
and held-out modes also refuse to start unless the checkout is clean and HEAD
is a valid 40-digit Git SHA; that SHA is written into the safe aggregate and
bound to a held-out reservation. A runbook or docs-only commit changes the
candidate SHA, so
re-verify the candidate after any checkout change.

## Environment

The project requires Python `>=3.11,<3.12`; package versions are selected by
the committed `uv.lock`. The repository does not pin a Python patch release or
an exact `uv` release. Use a compatible installed `uv`, install its Python 3.11
runtime if needed, then sync without changing the lock:

```powershell
uv --version
uv python install 3.11
uv sync --locked
uv run --locked python --version
```

The last command must report Python 3.11.x. Run the commands below from the
repository root. The evaluator uses the configured local model for calibration
and held-out, but substitutes a fixed fake RAG response and exposes only
`RAG_QUERY` and `COMPLETE_TASK` capabilities. It does not execute Phase 4 RAG
or write-capable tools.

## Protected-data rules

Do not inspect, print, copy, upload, or commit protected request text, prompts,
expected routes, terminal kinds, oracle values, raw model responses, per-case
results, or temporary worker output. Do not invoke evaluation internals or the
protected V4 test module directly. Use only these CLI commands and their
sanitized aggregate output. The parent CLI drains and discards worker stdout
and stderr; the worker returns only the validated closed aggregate schema.

## Safe pre-calibration review

Before seeking authorization for the first calibration:

1. Verify the candidate SHA and clean status as described above.
2. Read the safe ledger and implementation identities:

   ```powershell
   uv run --locked aaos phase5 v4 ledger-status
   ```

3. Confirm `fake_only_configuration.external_writes_enabled` and
   `fake_only_configuration.live_rag_enabled` are both `false`. Review the
   returned runner, scorer, manifest-verifier, aggregate-schema, candidate,
   and ledger identities. `candidate_sha` inside `ledger` is the candidate
   bound to a reservation; while AVAILABLE it is `null`, so use the verified
   Git SHA above as the current candidate identity.
4. Inspect only the safe source identities and aggregate schema. Do not read
   the V4 case-definition module or data to complete this review.
5. Obtain the separately required human authorization before calibration.
   Held-out also requires separate authorization and is one-time.

`ledger-status` is read-only. If the ledger does not exist it reports AVAILABLE
without creating a file or directory.

## Held-out state

The durable ledger is stored at:

```text
%LOCALAPPDATA%\AAOS\evaluation\v4\heldout-ledger.json
```

Read it only with:

```powershell
uv run --locked aaos phase5 v4 ledger-status
```

The only valid states are `AVAILABLE`, `RESERVED`, and `CONSUMED`. `execution_count`
is 0 while AVAILABLE or RESERVED and 1 after CONSUMED. RESERVED is ambiguous
if the process stops without trusted proof that failure happened before any
protected semantic access. Never reset or retry a stale/ambiguous reservation.
A corrupt or unavailable ledger blocks execution.

## Manifest

After verifying the candidate, run:

```powershell
uv run --locked aaos phase5 v4 manifest
```

Manifest mode checks both canonical manifests, fingerprints, required
structural coverage, duplicate status, and split disjointness. It does not call
the model and does not reserve or consume held-out. It emits only the safe
aggregate contract below.

## Calibration

Run calibration only after the safe review and separate human authorization:

```powershell
uv run --locked aaos phase5 v4 calibration
```

Calibration runs the canonical V4 scorer and local-model executor. Its RAG
response is fake, and its tool registry has no write-capable tool. Calibration
does not read or modify the held-out ledger state.

### Calibration gate

Use the aggregate's `passed` field and review the reported values. The canonical
C10 requirements are:

- At least 64 routing cases in each split.
- At least 16 cases in each of the four required language/action strata in
  each split.
- Global routing accuracy at least 0.95, compared with exact integer counts;
  at N=64, 61/64 passes and 60/64 fails. Do not round a displayed ratio up.
- Subgroup accuracy is diagnostic; it is not a separate acceptance threshold.
- Trusted-system conditions must pass, security hard-gate failures must be
  zero, and runtime failures must be zero. These independent domains cannot be
  compensated for by routing accuracy.

Stop if the manifest is invalid, any required structural minimum is absent, or
the calibration gate fails. Do not proceed to held-out after a failed gate.

## Held-out

After calibration passes, verify the ledger still reports AVAILABLE, confirm
the exact candidate SHA and authorization, and run exactly once:

```powershell
uv run --locked aaos phase5 v4 heldout
```

The runner atomically reserves the permit, completes non-semantic model
preflight, persists RESERVED to CONSUMED before loading/materializing the
protected manifest, then executes, scores, writes the safe aggregate, and
records completion. A crash or error after consumption remains CONSUMED. The
only release is a narrowly proven pre-semantic infrastructure failure while
still RESERVED; no case may have been materialized and no model/tool execution,
scoring, or case result may have started. Never retry after CONSUMED.

## Safe aggregate output

The closed schema identity is `phase5-v4-safe-aggregate-v1`. Unknown top-level
fields and unknown nested fields are rejected. It permits only benchmark and
evaluation identities, candidate SHA, total/routing/trusted-system counts,
trusted-system passes, security and runtime failure counts, model availability
counts, stratum counts, global and per-stratum accuracy, approved failure
counters, manifest/split/fingerprint booleans, threshold, pass status, and
execution count. `invalid_output_count` equals its disjoint parse and schema
failure subtotals. Evaluation-only counts are `null` in manifest mode. It has
no case text, raw response, expected/actual route, oracle, or per-case result
fields.

Reports are atomically written under:

```text
%LOCALAPPDATA%\AAOS\evaluation\v4\reports\manifest-latest.json
%LOCALAPPDATA%\AAOS\evaluation\v4\reports\calibration-latest.json
%LOCALAPPDATA%\AAOS\evaluation\v4\reports\heldout-latest.json
```

The same bounded JSON aggregate is printed to stdout. Protected worker stdout,
stderr, and exception text are discarded; public errors use fixed safe codes.
Do not open temporary worker IPC files.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Command completed and its acceptance gate passed. |
| `1` | Safe failure: command setup, infrastructure/model error, or malformed worker result. For `ledger-status`, this also means the ledger is corrupt or unavailable. Stop and diagnose without exposing raw diagnostics. |
| `2` | Invalid command syntax. |
| `3` | Evaluation completed and produced a safe aggregate, but its acceptance gate failed. Stop; do not run held-out after failed calibration. |
| `4` | Held-out permit is unavailable or ambiguous because the state is not AVAILABLE, or the ledger is corrupt/unreadable. Do not retry or reset. |

For `ledger-status`, code 0 means a safe state was read; code 1 means the
ledger is corrupt or unavailable.

## Stop conditions

Stop evaluation and preserve the current state if any of these occurs:

- Any protected semantic content appears in stdout, stderr, an exception, or a
  report. Do not copy it elsewhere or continue investigating in that context.
- Manifest verification fails or reports invalid fingerprints/disjointness.
- Calibration fails its canonical acceptance gate.
- Held-out state is anything other than AVAILABLE before the one-time command.
- The ledger is corrupt, unreadable, or contains ambiguous/stale RESERVED state.
- A worker, output-containment, candidate-cleanliness, or persistence defect is
  suspected. Do not repair the ledger by hand.
- Held-out has reached CONSUMED, including after a crash or missing aggregate.

After any stop condition, preserve the safe ledger status and ask for a fresh
review/authorization. Never rerun held-out to obtain a replacement result.
