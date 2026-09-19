# Phase 9 sanitized evidence package

Package status: `READY_FOR_HUMAN_ACCEPTANCE` (not a Phase 9 pass).

## Package index

| Item | Path / value |
| --- | --- |
| release checklist | `docs/release/phase-9-release-checklist.md` |
| evidence package description | `docs/release/phase-9-evidence-package.md` |
| manifest schema | `phase9-release-v1` |
| manifest SHA-256 | `cd01ae21f06ca60ebfff83cbd417aad7c569b6cadad1e97e4c936f9e3eae999f` |
| sanitized index SHA-256 | `f0f49768bf928e43d02bcd4a2fa2daded31ff88d2c53c11612f2a227a531d67c` |
| frozen Phase 8 target | `5dea7f19bbe26e85b968afc63a21c10f0dd4e389` |
| Alembic head | `0007_phase8_visual_provenance` |

The package is an index of sanitized evidence, not a copy of private raw
artifacts. Evidence report files produced by `build_evidence_package()` are
canonical UTF-8 JSON with sorted keys, bounded typed fields, and individual
SHA-256 hashes. The index itself records the manifest hash and report hashes.
The output directory must be new or empty; existing artifacts are rejected,
and writes use temporary files plus flush/fsync and atomic rename where the
platform supports it.

## Included evidence classes

- backend/frontend verification and static gates;
- migration and clean-setup qualification;
- Docker isolation/computation and failure/recovery qualification;
- deterministic RAG/grounding and multimodal summaries;
- deterministic fake external-action lifecycle summaries;
- Windows/resource qualification;
- tracked public-release and claim audits.

All model/provider calls and real external writes during Phase 9 are zero.
Any deterministic fake, synthetic fixture, historical external event, or
optional/unloaded model capability is labelled explicitly in its report.

The canonical sanitized report-file hashes are:

| Report | SHA-256 |
| --- | --- |
| action_lifecycle | `e59f90a535bd8dd2c2cc1e78df72d7bb61c12bb220fc1fd1fb522ad45a6f0040` |
| api_drift | `65e1a51342ddbef9007802f09147bbe281ffa4c38baef494679331b375352715` |
| backend_tests | `58b711b13cf68378dad09d05dde563f0e2b1c31fd40070fe7a0eda6c59d3d1a0` |
| browser_acceptance | `c097f6aeb179e72d607deedb68f0ad727f5678cd1d3e9db3e81ce927753a3e06` |
| clean_setup | `4270deb88b268c7bf927911868728a7644c9a4831e25de6f0eb2c0204ced0e28` |
| cleanup | `7abae31376e2680f16d2fa2d603c09a6c54eeb9f2ac3fec4152ac8e8fcb3c61f` |
| docker_qualification | `9e7de72a44693dec8ae44c49637b9f9f6dd422122789d8dcd9e7384278f721d1` |
| documentation_claims | `eae6895564c8493e3ce7e3d67286ca13ccad28389b974851c746dcfc019b95b9` |
| evaluation | `4dc8d9d14f716640bfd779dc642a9455702b3bebbdd52d8d36f6e3f6910678fe` |
| failure_recovery | `27a0c4c88084534152fd17d879ee09f6a955dbe0ab41ddb422092e5fc13304f5` |
| frontend_tests | `07b2b16cbcd48beda96ad655d85621daf4400d3a6aeaa306b84b27dec7c10574` |
| independent_review | `288bc8e16ae6f4e1ea8fb59ee8ec7c1546976f622a07db94b09a4a12dfb769b6` |
| migration_verification | `a35537516511007db06512869c08bc0b7121101a06775eb1d4912610ab096915` |
| multimodal_evaluation | `2325c8b5347366fca31b0038a423934614e0d844e7218ed99824edfd51cf00b1` |
| performance_resources | `0a0076e42be295825f1158da55b4ef8064f6936a85a8e9b9ce0a84c6be18b712` |
| public_release | `64e68eb936521e141605f111e6199e2e04e04ce90e4650bf12bfd6d4d8a60334` |
| security_adversarial | `cc29071b56217b8482c9e0747a34f0c75a91cb55baa3c7509910102879fe56f3` |
| static_gates | `5d41ca9f76a203ea6b81ae0fb6eb313b739f5ee7f3a5ca4ee4b6bf8a9ebe1691` |
| windows_qualification | `3720816777fdfceab3b03251f65a5c1870861937a4912d93da1ef81ef9ce8531` |

## Excluded data

No report may contain credentials, API/OAuth tokens, cookies, browser auth
state, private university documents, normal application databases, model
weights, raw provider payloads, raw prompts, Docker host details, tracebacks,
or temporary runtime/browser artifacts. This package does not alter or copy
the normal AAOS database.

## Review disposition

Independent review result: `PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`.
Known minor limitations are listed in the release checklist. Human review is
still required before any Phase 9 freeze decision; Task 9 verification is
complete but Phase 9 is not accepted or frozen.
