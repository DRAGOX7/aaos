# Agentic AAOS — Read-Only Conversational Orchestration

**Milestone:** AGENTIC_AAOS_PHASE_A_READ_ONLY_ORCHESTRATION
**Status:** Implemented; deterministic and static verification passed
**Baseline:** ad0a913df171b398f6dfce2e10061de33fbfbe67

## Product scope

A caller submits one self-contained academic question. AAOS asks the existing local model gateway for a closed decision, validates any selected tool call against a static read-only registry, runs a bounded operation through existing domain services, and returns a response grounded in trusted academic results or the existing Phase 4 RAG response.

The milestone does not add conversation memory, follow-up resolution, task actions, calendar or Moodle writes, approval execution, filesystem access, arbitrary SQL/HTTP/shell/browser execution, or a frontend.

Supported operations are derived from current read APIs:

- list and resolve active courses;
- list assignments globally or for one exactly resolved course;
- retrieve an assignment by exact title and optional course name;
- list documents, optionally by exact course or document title;
- delegate document-grounded questions to Phase 4 RAG.

Task reads are excluded from the first registry because they are not needed for these academic questions and their action summaries are outside this milestone.

## Architecture and ownership

Extend the current Phase 5 AgentOrchestrator, ToolRegistry, PermissionPolicy, runtime policy, observation sanitizer, event/lifecycle contracts, and RagQueryAdapter. Keep the Phase 5 C8 retrieve-or-terminal provider contract unchanged. The new academic decision provider is a distinct, closed schema because C8 deliberately prevents model-selected tool identity and arguments; that existing contract cannot express this milestone.

The model may choose only CALL_TOOL, ANSWER, or REFUSE. A call names one of the six registered capabilities and carries JSON arguments. ToolRegistry validates the exact input schema and permission before invoking a handler. Trusted services resolve names to canonical database identifiers. The model never supplies a trusted course, assignment, or document ID.

ANSWER contains one or more request-local evidence references previously exposed in sanitized observations. AAOS verifies each reference against validated tool results, then renders the response deterministically from those results. The model supplies no final answer text or citation text for structured academic reads.

REFUSE contains one closed reason code. User-facing wording is selected by trusted code.

The registry contains exactly:

- academic.list_courses
- academic.get_course
- academic.list_assignments
- academic.get_assignment
- academic.list_documents
- rag.answer

Every definition is READ_ONLY, uses a typed strict input schema, validates the exact bounded output shape, and has a tool-specific sanitized observation projection. No task tools or Phase 5 local-write tools are included.

## Entity resolution

Course names/codes and assignment/document titles are matched using deterministic Unicode NFKC, case-folding, and whitespace normalization. Resolution must produce exactly one active canonical record. Zero matches return a specific not-found result; multiple matches return an ambiguity result. There is no fuzzy or conversational reference resolution.

The rag.answer handler derives a trusted course scope only from a uniquely resolved course name. Without a course name it uses the request's application-granted global read scope. It constructs the RagQuery in trusted Python and delegates once through RagQueryAdapter and LocalRagService.

## Decision and observation contracts

The model receives a strict structured output schema. CALL_TOOL uses a registry-enumerated tool name and tool-specific argument shape. ANSWER carries bounded evidence-reference strings. REFUSE carries a closed reason enum. Duplicate JSON keys, unknown fields, unknown tools, invalid arguments, and invalid evidence references fail closed. The schema and registry are fixed by infrastructure and never incorporate user or observation values.

Each successful structured read returns a request-local opaque result reference plus a typed bounded record projection. The output validator checks the tool's exact key set, primitive types, row count, text lengths, identifiers, and reference format before the value can be sanitized or used as final evidence. Observation projections include only fields required for routing and label all academic content as untrusted data. The existing C4 sanitizer remains the path for path filtering, text bounds, and safe errors.

A RAG result uses the existing Phase 4 output validator and citation projection. A successful or insufficient-evidence RAG response is terminal and returned unchanged by the agent loop; no later model decision can rewrite it or bypass a refusal.

## Execution limits

Use the existing Phase 5 limits wherever available:

| Bound | Value |
| --- | ---: |
| Maximum proposal steps | 8 |
| Maximum tool calls | 4 |
| Maximum identical normalized calls | 2 |
| Maximum proposal repairs | 1 |
| Model proposal timeout | 60 seconds |
| rag.answer timeout | 120 seconds |
| Structured academic read timeout | 5 seconds |
| Maximum observations | 8 |
| Maximum records returned by one academic read | 50 |

Add a 300-second request deadline. The service signals cancellation at the deadline and waits for the orchestrator's truthful lifecycle result; it does not claim an in-flight synchronous worker was terminated. The API also forwards client disconnect cancellation. Repeat protection fingerprints the canonical registry name and schema-validated arguments using canonical JSON. Entity matching separately normalizes Unicode, case, and whitespace; repeat fingerprints do not currently merge those text variants.

## Provenance and final response

For structured reads, the model selects only references visible in prior observations. Trusted code verifies each reference and renders the response from the associated validated data. The response includes source tool name, entity kind, canonical entity ID, title, and course ID where applicable. Empty collection results include query provenance (tool and resolved filter) without inventing an entity ID.

For RAG, preserve the Phase 4 answer status, answer text, language, citation locations, document-version identities, generation ID, and content revision. The orchestrator cannot replace an insufficient-evidence response with terminal model text.

Model-produced citations are never accepted.

## Failure semantics

Keep distinct safe codes for unknown/ambiguous courses, missing/ambiguous assignments, no matching document, empty assignment/document collections, RAG insufficient evidence, invalid model decisions, invalid arguments, unknown or unauthorized tools, tool failure, model/tool timeout, request deadline, cancellation, step exhaustion, repeated calls, invalid evidence references, and output-schema failure.

Empty assignment or document lists are successful factual results. A requested exact entity that cannot be resolved is a typed not-found/ambiguity result. RAG insufficient evidence is a grounded terminal outcome, not a generic tool failure.

## Trust boundaries

Natural-language requests, academic titles/descriptions, model output, tool observations, and RAG evidence are untrusted data. They cannot change tool registration, permission, schemas, limits, state, system policy, or approval requirements. The tool catalog is generated only from executable static definitions. Tool handlers call read-only domain services and the existing RAG service; they do not access databases directly.

The request is single-turn and self-contained. No persistent or semantic memory is created.

## Verification and extension points

Use fake model/provider and academic/RAG services for deterministic end-to-end scenarios. Cover direct reads, multi-step tool use, RAG delegation/refusal, invalid decisions and arguments, unknown shell/HTTP/SQL names, repeated calls, limits, cancellation/deadline, malicious observations, provenance validation, and output contract rejection. No real external writes, V4 execution, or real provider calls are part of acceptance.

Future work may add bounded conversational context only as a separately authorized milestone. Task actions, approval workflows, calendar actions, change intelligence, study planning, UI, evaluation against protected V4 material, and long-term memory remain excluded.

## Recommended Follow-Up

- Add a small concurrent-request admission cap for `/ask` before exposing the endpoint to broader traffic, so simultaneous local model requests cannot accumulate without a process-level bound.
- Consider applying trusted entity-name normalization to repeat-call fingerprints so equivalent course-name spellings consume the same repeat allowance.
