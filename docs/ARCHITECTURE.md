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

This document describes intended architecture. The current implementation status remains Bootstrap / pre-Phase 0.
