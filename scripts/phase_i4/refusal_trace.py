"""Trace which RAG stage refuses each question in the frozen I4 manifest.

Run from the runtime data root (the directory that contains ``data/``):

    python -m scripts.phase_i4.refusal_trace OUT.json [RUNS]

Recording proxies replace the production service's components (sufficiency
gate, atomic decomposer, span extractor, coverage classifier, generator and
claim NLI verifier) inside this process only; production code and behaviour are unchanged. Answerable cases
run ``RUNS`` times (default 3), unanswerable cases once. Question text,
requirement text and short span excerpts stay in the local results file.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from aaos.rag.contracts import GlobalScope, RagOperationalError, RagQuery
from aaos.rag.factory import build_configured_phase4_runtime

from .arabic_retrieval_eval import MANIFEST, _matches

EXCERPT_CHARACTERS = 160


class _Proxy:
    """Forward everything to ``inner`` except the one recorded method."""

    def __init__(self, inner: Any, name: str, method: Any) -> None:
        self._inner = inner
        setattr(self, name, method)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _install(service: Any, record: dict[str, Any]) -> None:
    gate = service._sufficiency_gate

    def evaluate(*args: Any, **kwargs: Any) -> Any:
        decision = gate.evaluate(*args, **kwargs)
        record["sufficiency"] = decision.status.value
        record["evidence_count"] = decision.evidence_count
        return decision

    service._sufficiency_gate = _Proxy(gate, "evaluate", evaluate)

    decomposer = service._atomic_requirement_decomposer

    async def decompose(*args: Any, **kwargs: Any) -> Any:
        result = await decomposer.decompose(*args, **kwargs)
        record["decomposition"] = result.status.value
        record["requirements"] = {
            item.requirement_id: {"text": item.requirement_text} for item in result.requirements
        }
        return result

    service._atomic_requirement_decomposer = _Proxy(decomposer, "decompose", decompose)

    extractor = service._evidence_span_extractor

    async def extract(*args: Any, **kwargs: Any) -> Any:
        spans = await extractor.extract(*args, **kwargs)
        for item in spans:
            entry = record.setdefault("requirements", {}).setdefault(item.requirement_id, {})
            entry["spans"] = [
                {
                    "evidence_id": span.evidence_id,
                    "characters": len(span.quote),
                    "excerpt": " ".join(span.quote.split())[:EXCERPT_CHARACTERS],
                }
                for span in item.spans
            ]
        return spans

    service._evidence_span_extractor = _Proxy(extractor, "extract", extract)

    coverage = service._requirement_coverage_classifier

    async def judge(*args: Any, **kwargs: Any) -> Any:
        decision = await coverage.classify(*args, **kwargs)
        record["coverage"] = decision.status.value
        for judgment in decision.judgments:
            entry = record.setdefault("requirements", {}).setdefault(judgment.requirement_id, {})
            entry["support"] = judgment.support.value
        return decision

    service._requirement_coverage_classifier = _Proxy(coverage, "classify", judge)

    claim_verifier = service._claim_entailment_verifier
    classifier = claim_verifier._classifier
    pending: list[dict[str, Any]] = []

    def classify(premise: str, hypothesis: str) -> Any:
        result = classifier.classify(premise, hypothesis)
        pending.append(
            {
                "premise_characters": len(premise),
                "label": result.native_label,
                "entailment": round(result.entailment_probability, 3),
                "neutral": round(result.neutral_probability, 3),
                "contradiction": round(result.contradiction_probability, 3),
            }
        )
        return result

    claim_verifier._classifier = _Proxy(classifier, "classify", classify)

    async def verify_claim(claim: Any, **kwargs: Any) -> Any:
        pending.clear()
        result = await claim_verifier.verify(claim, **kwargs)
        record.setdefault("claims", []).append(
            {
                "excerpt": " ".join(claim.text.split())[:EXCERPT_CHARACTERS],
                "result": result.value,
                "nli": pending[0] if pending else None,
            }
        )
        return result

    service._claim_entailment_verifier = _Proxy(claim_verifier, "verify", verify_claim)

    generator = service._answer_generator

    async def generate(*args: Any, **kwargs: Any) -> Any:
        output = await generator.generate(*args, **kwargs)
        record["generator_status"] = output.status.value
        record["generator_claims"] = len(output.claims)
        return output

    service._answer_generator = _Proxy(generator, "generate", generate)


def _stage(record: dict[str, Any]) -> str:
    """Name the first stage that decided a refusal, from the recorded facts."""
    if record["status"] != "INSUFFICIENT_EVIDENCE":
        return "ANSWERED" if not record["status"].startswith("ERROR") else "ERROR"
    if record.get("sufficiency") == "UNSUPPORTED":
        return "S1_SUFFICIENCY"
    if record.get("decomposition") == "TOO_MANY_REQUIREMENTS":
        return "S2_DECOMPOSITION"
    if "generator_status" not in record:
        requirements = record.get("requirements", {}).values()
        if requirements and all(not item.get("spans") for item in requirements):
            return "S2_NO_SPANS"
        return "S2_COVERAGE"
    if record["generator_status"] == "INSUFFICIENT_EVIDENCE":
        return "S3_GENERATOR"
    claims = record.get("claims", [])
    if claims and all(item["result"] == "NOT_ENTAILED" for item in claims):
        return "S4_CLAIM_NLI"
    return "S5_VALIDATION"


async def _trace(runtime: Any, cases: list[dict[str, Any]], runs: int) -> list[Any]:
    service = runtime.service
    record: dict[str, Any] = {}
    _install(service, record)
    rows = []
    for run in range(1, runs + 1):
        for case in cases:
            if run > 1 and not case["answerable"]:
                continue
            for language in ("en", "ar"):
                record.clear()
                started = time.monotonic()
                try:
                    response = await service.ask(RagQuery(case[language], GlobalScope()))
                    record["status"] = response.answer.status.value
                    record["answer_hit"] = any(
                        _matches(
                            (c.document_version_id, c.page_start, c.page_end), case["expected"]
                        )
                        for c in response.answer.citations
                    )
                except RagOperationalError as error:
                    record["status"] = f"ERROR:{error.code.value}"
                    record["answer_hit"] = False
                row = {
                    "run": run,
                    "id": case["id"],
                    "language": language,
                    "answerable": case["answerable"],
                    **record,
                    "seconds": round(time.monotonic() - started, 1),
                }
                row["stage"] = _stage(row)
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


async def _trace_then_close(runtime: Any, cases: list[dict[str, Any]], runs: int) -> list[Any]:
    try:
        return await _trace(runtime, cases, runs)
    finally:
        await runtime.aclose()


def main(argv: list[str]) -> int:
    if len(argv) not in {1, 2} or (len(argv) == 2 and not argv[1].isdigit()):
        print(__doc__)
        return 2
    runs = int(argv[1]) if len(argv) == 2 else 3
    cases = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
    runtime = build_configured_phase4_runtime(data_root=Path("data"))
    rows = asyncio.run(_trace_then_close(runtime, cases, runs))
    Path(argv[0]).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
