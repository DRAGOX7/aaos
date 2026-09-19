# Phase 9 clean setup and reproducibility qualification

This qualification is limited to installation, configuration diagnostics, and
disposable migration probes. It does not change the application startup path,
create the normal database, download models, contact providers, or perform
external writes.

## Required pinned inputs

- Python: `>=3.11,<3.12`, selected through the committed `uv.lock`.
- Backend lock: `uv lock --check`.
- Frontend: the committed `frontend/package-lock.json`; setup uses `npm ci`
  with scripts disabled. No dependency upgrade is performed.
- Alembic head: `0007_phase8_visual_provenance`, reached from the committed
  migration chain. Qualification databases are temporary files only.
- Configured local model identifier: `qwen/qwen3.5-4b` (from the frozen
  settings default). Phase 9 records this identifier but does not load or
  download it; no immutable LM Studio artifact revision is claimed here.
- Phase 8 pinned model revisions remain the owning Phase 8 evidence. This task
  does not re-download or reinterpret those artifacts.

## Qualification method

`scripts/phase9/qualify_clean_setup.py` accepts an injected `SetupProbe` and
executes only fixed argument tuples. It never uses a shell string, prints
configuration values, contacts a service, or returns command stderr. The
configuration validator classifies values as `VALID`, `MISSING`, or
`MISCONFIGURED` without reading a provider or treating a secret as report data.

Migration qualification invokes the repository's normal Alembic environment
against a newly-created temporary SQLite path. It verifies the resulting
revision and table inventory, reports no external calls/writes, and rejects an
existing path. The normal `data/database/aaos.db` is never selected.

## Dependency and repository disposition

Range-pinned backend dependencies are disclosed rather than upgraded. Exact
frontend dependencies are governed by `package-lock.json`. No CI workflow is
invented by Phase 9; the repository's existing local commands are the release
qualification surface. README status wording is not changed in Task 4 because
the approved plan assigns no README edit to this task.

## Windows/PowerShell notes

Use PowerShell-native path handling and explicit argument arrays. Subprocess
output must be decoded with UTF-8 for the acceptance runtime when the local
Windows code page is CP1252 (`PYTHONUTF8=1` is an environment qualification
note, not an application setting). Temporary databases and probe roots must be
under a controlled temporary directory and removed by the caller after
evidence is retained.

## Safety invariants

- `create_all()` is not part of clean setup; only Alembic migration is used.
- Imports remain inert: no implicit database creation, migration, model load,
  Docker startup, network call, or provider call.
- Credentials, tokens, cookies, model weights, normal databases, and raw
  command diagnostics are never written to the report.
- Phase 8 remains frozen at `phase-08-passed`; RLM/Deep Ask AAOS remains out of
  scope.
