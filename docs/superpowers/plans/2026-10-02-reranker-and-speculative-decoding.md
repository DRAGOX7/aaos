# Plan — Reranker and Speculative Decoding (2026-10-02)

**Status: PROPOSED. Not approved. No code written.**

There are two independent changes here, and each is approved and merged on its own:

- **A:** a reranker, for answer quality.
- **B:** speculative decoding, for speed.

Both touch the frozen Phase 4 RAG path, in the same way as the approved latency work (`4bb4964a`, `4fc8e5af`). Phase 4 is not reopened. Each change is a measured improvement that the owner accepts or rejects on evidence.

## Baseline (already measured)

From `docs/evaluations/ask-latency-2026-10-02.md` and the refusal traces:

| Measure | Today |
|---|---|
| Typical Ask answer (live API) | 10–19 s |
| 3-run trace: median / slowest query | 18.4 s / 44.0 s |
| Expected page cited (EN / AR, mean of 16) | 14.3 / 12.3 |
| Unanswerable questions refused; cited answers among them | 8/8; 0 |

Hardware: RTX 4050 Laptop with 6 GB VRAM and 15.6 GB RAM. qwen3.5-4b is the only model on the GPU. The query embedder (BGE-M3) and the claim NLI run on the CPU.

The evaluation corpus is the Summer course documents. They are still indexed, and the courses are now archived. The traces must scope questions to those courses by name, because unscoped questions now search current courses only.

## Shared evaluation gate

Both changes are judged with the same harness and the same bar:

1. Re-run the 3-run refusal/coverage trace (16 EN + 16 AR answerable, 8 unanswerable) and the Phase I4 Arabic retrieval set, before and after the change, on the same machine with nothing else on the GPU.
2. Record per-stage timings and answers in `docs/evaluations/`.
3. **Accept only if all hold:**
   - unanswerable questions are still refused (8/8, 0 cited answers);
   - expected-page citation does not drop beyond run-to-run noise;
   - the A–H selectors (95) and the full suite stay green.

## A. Cross-encoder reranker

### Why

Retrieval today works like this:

1. The question is searched two ways, by meaning (30 dense candidates) and by words (30 lexical candidates).
2. The two lists are merged by rank (RRF, k=60).
3. At most 6 chunks are kept as evidence (`retrieval.py`).

RRF only looks at ranks, never at how well a passage actually answers the question. The refusal investigation (`rag-generator-refusal-2026-10-01.md`) traced answerable refusals to the right evidence not reaching the generator.

A cross-encoder reads the question and each passage together and scores relevance directly. It is the standard fix for this.

### Model

- **`BAAI/bge-reranker-v2-m3`:** multilingual including Arabic, the same family as the BGE-M3 embedder, about 568M parameters.
- **Placement:** runs on the CPU next to the query embedder, so the GPU keeps qwen and no model swap is added.
- **Memory:** the weights are 2.27 GB (`model.safetensors`, Apache-2.0, revision `953dc6f6`). That is about 2.3 GB of extra RAM in fp32, on top of the ~2.5 GB the embedder holds. Measure whether int8/ONNX is needed.

### Design

- **Insertion point:** in `LocalRagService._retrieve`, after fusion and verification and before evidence selection. Rerank the top N fused candidates (start with N = 16) and pass them to the selector in reranked order.
- **Unchanged:** the selector, the sufficiency gate, the coverage checks and the claim verification. The reranker only reorders candidates. It never adds a chunk the retrieval did not return, so provenance invariants hold.
- **Sufficiency gate:** `sufficiency_max_fused_rank` keeps using the **fused** rank, so the refusal policy does not change silently. Reranking cannot rescue a candidate that the gate would reject.
- **Version tracking:** a new config field `reranker_model_identifier` and revision is recorded in query provenance, the same way the embedder is.
- **Kill switch:** disabled by a config flag, so it can be turned off without a revert.
- **Warm-up:** loaded at backend start like the embedder and NLI, so the first question does not pay the load time.

### Steps (RED → GREEN)

1. **Spike (evaluation-only, no production code):**
   - Score the trace questions' fused candidates with the reranker.
   - Measure CPU time per question at N = 8, 16 and 24, and RAM.
   - Measure how often the expected page moves into the top 6.
   - **Stop here if the gain is negligible or the cost exceeds about 2 s per question.**
2. Tests:
   - reranking reorders but never adds or drops candidate identities;
   - fused rank still drives sufficiency;
   - the disabled flag gives today's order exactly;
   - the provenance records the reranker identity.
3. Implement the `Reranker` port, the CPU adapter, the factory wiring and the warm-up.
4. Run the shared evaluation gate. Record results.

### Risks

- **CPU time:** 16 pairs of up to about 512 tokens may take 2–5 s on the CPU. The spike decides N, or whether to stop.
- **RAM:** 15.6 GB total, with about 2.5 GB already held by the embedder.
- **Arabic:** the gain may differ by language, so report EN and AR separately.

## B. Speculative decoding in LM Studio

### Why

About 9 s of each answer is four sequential qwen calls:

- decompose: ~1.5 s;
- span selection: ~2 s;
- coverage: ~3 s;
- generation: ~3 s.

With speculative decoding, a small draft model proposes several tokens and qwen verifies them in one pass. The **output distribution is unchanged**, so answer quality should be identical. Only speed changes.

### Preconditions (verify first; any "no" stops B)

1. **Draft model:** it must share qwen3.5-4b's tokenizer, which means a small Qwen 3.5 model. None is installed (`lms ls` shows only gemma-3-4b, minicpm5-2b and qwen3.5-4b), so one must be downloaded. That needs the owner's approval of the exact file and size.
2. **LM Studio support:** LM Studio must offer a draft model for the `qwen35` architecture, and it must work together with the JSON-schema `response_format` that AAOS uses for every call.
3. **VRAM:** qwen 4B and the draft must fit in 6 GB together with the KV cache.

### Design

- **Requests:** pass the approved draft model on chat requests from `aaos.inference.lm_studio` (`draft_model` in the request payload), controlled by a config value that defaults to off.
- **Allow-list:** the draft model identity is allow-listed like `APPROVED_QWEN_MODEL`. An unknown or missing draft falls back to plain decoding and is reported, never failed silently.
- **Lifecycle:** `lm_studio_lifecycle.py` loads the draft alongside qwen and unloads it with qwen.

### Steps

1. **Spike (manual, evaluation-only):**
   - Download the approved draft.
   - Time the four pipeline calls with and without the draft on the trace questions.
   - Record the draft acceptance rate and VRAM use.
   - **Stop here if the speedup is under about 20% or VRAM overflows.**
2. Tests:
   - the request carries the draft only when enabled and allow-listed;
   - the fallback works when the draft is unavailable;
   - lifecycle loads and unloads both models.
3. Implement and run the shared evaluation gate.

### Risks

- **Small gains:** structured outputs are short, so speculative decoding helps most on long generations. AAOS outputs are mostly short JSON, which limits the gain. The spike measures it.
- **Arabic:** Arabic text may have a lower draft acceptance rate. Report it separately.

## Considered and not pursued

**Jev (TypeSafe AI, "System One" decision model).** Jev returns typed decisions (choice, score, yes/no) in one parallel pass instead of generating text, and its makers claim large speedups. It is not adopted, for three reasons:

- **Hosted API only:** using it would send course documents and Moodle data to a third-party cloud. That breaks AAOS's local-only design and its security rules.
- **Cannot write answers:** it doesn't generate text, so it cannot replace the generator, which is where answer quality is decided.
- **Little to gain where it would fit:** the decisions it could take over, like intent routing, already run instantly on deterministic rules. Claim verification is a yes/no question that Jev could answer, but that is exactly the document text that must not leave the machine.

Revisit only if a local, open-weight version appears.

## Order

Do **A first**. Answer quality is the weaker property today, and A needs no download beyond the reranker weights. That download also needs owner approval. **B** follows only if its preconditions pass.
