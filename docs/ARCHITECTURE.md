# Intended Architecture

## Primary control flow

```text
User
  ->
Harness / Orchestrator
  ->
Local LLM
  ->
Action proposal
  ->
Policy + schema validation
  ->
Typed Tool / MCP / Restricted REPL
  ->
Environment
  ->
Structured observation
  ->
Harness / Orchestrator
  ->
Local LLM
  ->
repeat until completion / failure / approval boundary
```

Initially, AAOS has one main harness/orchestrator. Subagents are not part of the initial architecture. The orchestrator owns state transitions, validation, authorization, execution dispatch, traceability, approval boundaries, and loop termination.

The local LLM is a reasoning and proposal component, not an execution authority. Every model-proposed action passes through trusted application code. Tool arguments are schema validated, and permissions and policies are checked before execution. External or sensitive writes go through the approval engine once that capability is introduced.

## Execution boundaries

- **Typed tools:** provide narrowly scoped, schema-validated application operations.
- **Direct typed Python interfaces:** are the normal boundary between internal AAOS modules.
- **MCP:** is used only where standard external interoperability is useful.
- **Restricted REPL:** is introduced only in Phase 8. It is not a universal root capability and remains constrained by policy, isolation, resources, and task-specific access.

The model never receives direct, unrestricted access to credentials, Moodle authentication state, account data, the host shell, external systems, or storage. Model output, retrieved content, tool results, and environment observations remain untrusted inputs.

## State and knowledge boundaries

- SQLite stores canonical workflow and domain state.
- Chroma and lexical retrieval store searchable academic knowledge derived from authorized local documents.
- Credentials and private academic data remain local and outside model-visible context except through narrowly scoped, trusted mechanisms.
- Structured observations return to the orchestrator for validation before another bounded model interaction.

## Component responsibilities

- **User:** provides intent, context, and explicit human approval where required.
- **Harness/orchestrator:** controls policy, state, validation, authorization, execution, traceability, and safe termination.
- **Local LLM via LM Studio:** interprets bounded context and proposes structured plans, tool requests, or responses.
- **Typed tools/MCP/restricted REPL:** expose only approved capabilities through controlled boundaries.
- **Environment:** contains the local or external academic systems and data being observed or changed.
- **Structured observation:** reports a tool or environment outcome without granting it authority over the next action.

## Current implementation status

The authoritative status/decision source is [GOVERNANCE_LEDGER.md](GOVERNANCE_LEDGER.md).
This summary is not independent implementation authorization.

This document describes the intended architecture and the following
evidence-qualified status: Phase 0 acceptance is unresolved; Phase 1 is
**FROZEN** at `phase-01-passed` / `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`;
Phase 2 is **ACCEPTED, NOT FROZEN** at
`736c8498ab1fe2c1d4d425ba4625774eaf791e93` with no historical
`phase-02-passed` tag; Phase 3 is **FROZEN** at `phase-03-passed` /
`12a27f7a77ed79a3e638d9fc6bf4509840a9b326`; Phase 4 is **FROZEN** at
`phase-04-passed` / `e99c494628952e0a1ebcafa2450c87611b4dc6c8`; and Phase 5 is
**PARTIALLY IMPLEMENTED / CORRECTIVE REVIEW REQUIRED** (Slices 1–5 human
approved; committed Slice 6 not yet approved).
Phases 6 onward remain **PLANNED / NOT STARTED**. The existing `phase-00-passed` tag points to
`55d2293` (`docs: plan Phase 0 final acceptance`); repository evidence does not
establish completed Phase 0G/0H acceptance, so that historical provenance
remains unresolved pending explicit governance action.

## Reviewed consumer and future-phase boundaries

Phase 5 corrective gate C1–C7 is specified in the
[Phase 5 plan](superpowers/plans/2026-09-07-phase-5-controlled-agent-loop-implementation.md).
It requires an application-owned outer task grant, one trusted completion
authority preserving Phase 4 rendered-response lineage, explicit operation
termination/cleanup distinct from request deadlines, tool-specific observation
projections, one effective runtime policy, actual tool output validators and
safe versioned backend events. These are pending corrections, not claims
about current behavior. Slice 6 is not human approved.

Historical Phase 4 design ownership wording (sections 7 and 38) is superseded
for future Phase 5 work: conversation history and follow-up rewriting remain
excluded, approval belongs to Phase 6. The frozen Phase 4 document and code
are preserved; integration stays `LocalRagService.ask(RagQuery)` returning
`RenderedRagResponse`, not a new answer generator.

Phase 6 approval is orthogonal to capability permission, with durable atomic
action consumption and unknown-outcome reconciliation before external writes.
MCP is a transport, not a prerequisite for internal tools. Phase 7 consumes
backend events/policy rather than owning execution policy in frontend state.
Phase 8 vision and isolated computation are independently optional; vision
does not grant code authority and a Python namespace is not containment.
Phase 9 consolidates release evidence; security/recovery/evaluation remain
continuous. Exact future gates live in [PHASES.md](PHASES.md).
