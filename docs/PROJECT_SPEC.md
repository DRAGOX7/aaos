# AAOS Project Specification

## Product goal

AAOS (Autonomous Academic Operating System) is a local-first academic workflow assistant primarily targeting Moodle-based academic portals. It is intended to help a user observe, organize, retrieve, reason about, and safely act on academic work without giving a language model unrestricted access to accounts, data, or execution environments.

Credentials and private academic data remain local. Sensitive external writes require explicit human approval.

## Intended architecture

AAOS is initially built around one primary harness/orchestrator, not a multi-agent swarm. A local LLM served through LM Studio interprets context and proposes structured actions. The LLM does not execute those actions: trusted application code validates, authorizes, executes, and records them.

Controlled application operations use typed tools. MCP is used at appropriate external interoperability boundaries, while internal AAOS operations use direct typed interfaces. A restricted IPython/Python REPL is planned for a later phase and is not a general execution authority.

Academic knowledge is built through retrieval-augmented generation over locally stored academic documents. Retrieval and generation must preserve source provenance and produce citation-grounded answers.

## Major intended capabilities

- Read-only Moodle synchronization.
- Assignment, announcement, and deadline extraction.
- Course-file downloading and ingestion.
- Local, citation-grounded RAG over academic documents.
- Study-material generation.
- Controlled project scaffolding.
- Google Calendar synchronization behind explicit human approval.
- GitHub integration through MCP where appropriate.
- A dashboard and structured telemetry.
- A later restricted IPython/Python REPL.
- Later multimodal and vision-language-model processing of visual academic material.

These are planned capabilities assigned to gated phases. They are not claims about the current implementation.

## Architectural principles

1. **Trusted code controls execution.** The LLM proposes actions; trusted application code validates, authorizes, executes, and records them.
2. **One primary orchestrator initially.** The initial system has a single, inspectable control loop rather than a multi-agent swarm.
3. **Least privilege.** Each tool receives only the access and inputs required for its defined operation.
4. **Typed boundaries.** Tool inputs, outputs, observations, and approval requests use explicit schemas and validation.
5. **Human approval before sensitive external writes.** Reads and proposals are distinct from consequential actions that modify an external environment.
6. **Local first.** Credentials, private academic data, model inference, and document knowledge remain local unless an explicitly reviewed boundary requires otherwise.
7. **Observable behavior.** Important decisions, tool calls, failures, and state transitions are inspectable without leaking secrets.
8. **Phase-gated delivery.** Each phase must satisfy its acceptance gate before work begins on the next phase.
9. **No hidden fallback.** Security, validation, and execution failures are reported explicitly rather than silently bypassed.
10. **Replaceable components.** The orchestrator, model, tools, retrieval layer, and environment adapters remain separated by stable contracts.

## Current implementation status

The current evidence-qualified status is:

- **Phase 0 — ACCEPTANCE UNRESOLVED.** An existing `phase-00-passed` tag points to `55d2293` (`docs: plan Phase 0 final acceptance`), but repository evidence does not establish completed Phase 0G/0H acceptance. The historical tag is preserved pending explicit governance action.
- **Phase 1 — FROZEN** at `phase-01-passed` / `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`.
- **Phase 2 — FROZEN** at `phase-02-passed` / `736c8498ab1fe2c1d4d425ba4625774eaf791e93`; freeze tag added at project-owner request on 2026-09-30.
- **Phase 3 — FROZEN** at `phase-03-passed` / `12a27f7a77ed79a3e638d9fc6bf4509840a9b326`.
- **Phase 4 — FROZEN** at `phase-04-passed` / `e99c494628952e0a1ebcafa2450c87611b4dc6c8`.
- **Phase 5 — PARTIALLY IMPLEMENTED / CORRECTIVE REVIEW REQUIRED.** Slices 1–5 are human approved; committed Slice 6 is not yet human approved. Slice 7 and Phase 6 are not authorized.

The authoritative status/decision source is [GOVERNANCE_LEDGER.md](GOVERNANCE_LEDGER.md).
Historical Phase 0/2 qualifications above remain unchanged. The approved
roadmap revision requires C1–C7 before further Slice 7 / Slice 8 acceptance
work; it does not reopen frozen phases. Future gates are defined in
[PHASES.md](PHASES.md): durable approval core before Calendar, optional GitHub
read-only, backend-led operational visibility, independent optional vision
and isolated computation, then release qualification. Product capabilities
listed elsewhere are goals, not early implementation authorization.

Phase status vocabulary and the remaining roadmap are defined in
[`docs/PHASES.md`](PHASES.md). These status notes do not alter the product
requirements or future scope above.
