# Ask Latency — Investigation (2026-10-02)

No production code was changed. Measurements used evaluation-only probes against the real runtime, with nothing else on the GPU (RTX 4050 Laptop, 6 GB VRAM; 15.6 GB RAM).

## Where the time goes

Each stage was timed on 5 questions × 2 rounds (`scripts`-style probe in the session scratchpad). Loading qwen happens inside the first model call, so it is listed separately below.

| Stage (steady state) | Time per question |
|---|---|
| Unload qwen before embedding (`lms ps` / `unload` / `ps`) | ~2 s |
| Load BGE-M3 on the GPU and embed the question | ~4 s |
| Release BGE-M3 | ~1 s |
| Reload qwen (`lms load`) | ~5–6 s |
| **GPU swap subtotal** | **~12 s** |
| Model calls: decompose ~1.5 s, span selection ~2 s, coverage ~3 s, generation ~3 s | ~9 s |
| Claim NLI on CPU | ~1 s per short check, 2–3 s per long window |

**Totals:**
- Simple questions: 18–26 s.
- Multi-claim answers: 44–46 s. K-fold spent 14–16 s in claim NLI.
- A claim that fails verification: 51–55 s. The final-exam-weight claim spent 28–31 s in claim NLI.

## Causes

1. **The GPU model swap is about half of a typical question.** Phase 4 unloads qwen before every query embedding to respect the 6 GB VRAM ceiling, then reloads it. This is deliberate, but per question it costs ~12 s for a ~0.1 s embedding.
2. **The claim-NLI fallback is costly when it can't succeed.** A neutral claim is re-checked against each full cited chunk in windows, at about 1–3 s per CPU NLI call. Claims that never verify pay the full fallback every time. The fallback was added by the claim-verification fix (`bc4d5583`).
3. **Cold start.** After a backend restart, the first question loads BGE-M3 (4.5 s on the GPU, 26 s on the CPU) and the NLI model (~19 s), on top of the agent layer. This explains the 1 m 53 s answer seen earlier.
4. **Model calls (~9 s)** are inherent to a four-call pipeline on a local 4B model.

## Measured alternative for cause 1

Query embedding with BGE-M3 on the CPU:

| Device | First query (cold) | Each later query | After a release |
|---|---|---|---|
| CPU | 26 s | **0.15 s** | 2.6 s |
| GPU (today) | 4.5 s | 0.03–0.08 s | 3.3 s |

- The CPU and GPU query vectors match (cosine 0.999999 on 3 questions), so retrieval is unaffected.
- A CPU-resident query embedder would remove the ~12 s swap from every question. qwen would stay on the GPU, and bulk indexing would keep the existing GPU swap path.
- **Cost:** ~2.3 GB of RAM held by the backend (5.4 GB was free during measurement), and a one-time ~26 s warm-up.

## Recommendations (each needs approval)

1. **CPU-resident query embedding** for Ask, keeping the GPU swap for indexing. Expected: about 12 s less per question.
2. **Warm up at backend start:** load the query embedder and NLI in the background so the first question doesn't pay ~45 s.
3. **Bound the claim-NLI fallback.** For example, re-check only chunks whose span verdict was neutral, and stop at the first entailed window. Measure how often the fallback actually rescues a claim before tightening it.

## Results after the changes

Implemented and merged with owner approval:
- CPU-resident query embedding and startup warm-up (`4bb4964a`);
- the claim re-check bounded to two windows (`4fc8e5af`).

| Measure | Before | After |
|---|---|---|
| Typical Ask answer (live API) | 25–45 s | 10–19 s |
| Failing claim's re-check (same claim, real NLI) | 25.1 s (5 inferences + 3 overflow probes) | 5.4 s (2 inferences) |
| 3-run trace: median / slowest query | 30.0 s / 66.1 s | 18.4 s / 44.0 s |
| 3-run trace: expected page cited (EN / AR, mean of 16) | 13.3 / 12.0 | 14.3 / 12.3 |
| 3-run trace: unanswerable refused, cited answers | 8/8, 0 | 8/8, 0 |

The answer-quality differences are within run-to-run noise; the bound did not cost correct answers. The backend holds about 2.5 GB of extra RAM for the CPU query embedder.
