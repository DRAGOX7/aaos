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

**Bootstrap / pre-Phase 0.**

This repository currently contains governance documentation only. No application, model, Moodle, RAG, MCP, database, container, REPL, dashboard, or agent functionality has been implemented.
