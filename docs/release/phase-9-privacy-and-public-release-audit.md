# Phase 9 privacy and public-release audit

The release-facing tree is scanned for credentials, tokens, cookies, OAuth
state, private university documents, normal application databases, model
weights, browser state, temporary screenshots, host-specific artifacts, raw
provider payloads, and unbounded diagnostics. The scanner reports only bounded
counts and never returns matching content.

The public package must contain sanitized synthetic fixtures only. Files under
`data/auth/`, `data/credentials/`, normal database paths, model-weight suffixes,
and temporary browser-artifact suffixes are release blockers. Secrets remain
local and are never committed, logged, or exposed to the frontend/model.

The audit is deterministic and does not contact Moodle, Google, LM Studio, or
any other provider. A clean scan is evidence for this candidate only; it does
not make operator credentials safe to publish.
