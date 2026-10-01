"""Phase I4: measure English vs Arabic retrieval and answers on the real index.

Run from the runtime data root (the directory that contains ``data/``):

    python -m scripts.phase_i4.arabic_retrieval_eval retrieval OUT.json
    python -m scripts.phase_i4.arabic_retrieval_eval answers OUT.json

``retrieval`` runs only embedding, dense, lexical and fusion (no language
model). ``answers`` runs the complete production RAG path. The manifest is
frozen; results are evidence for the I4 decision, not a test gate.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from aaos.rag.contracts import GlobalScope, RagOperationalError, RagQuery
from aaos.rag.factory import build_configured_phase4_runtime
from aaos.rag.retrieval import reciprocal_rank_fusion

MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "evaluation"
    / "phase_i4_arabic_retrieval_cases_v1.json"
)
EVIDENCE_CHUNKS = 6
MAX_FUSED_RANK = 30


def _chunk_pages(database: Path) -> dict[str, tuple[int, int, int]]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return {
            key: (version, start, end)
            for key, version, start, end in connection.execute(
                "select chunk_key, document_version_id, page_start, page_end from rag_chunks"
            )
        }
    finally:
        connection.close()


def _matches(location: tuple[int, int, int] | None, expected: list[list[int]]) -> bool:
    if location is None:
        return False
    version, start, end = location
    return any(version == v and start <= page <= end for v, page in expected)


def _best(ranked: list[tuple[str, int]], pages: dict[str, Any], expected: Any) -> int | None:
    ranks = [rank for key, rank in ranked if _matches(pages.get(key), expected)]
    return min(ranks) if ranks else None


def _retrieval(runtime: Any, pages: dict[str, Any], cases: list[dict[str, Any]]) -> list[Any]:
    retriever = runtime.runtime.service._retriever  # evaluation-only inspection
    rows = []
    for case in cases:
        for language in ("en", "ar"):
            candidates = retriever.retrieve_candidates(RagQuery(case[language], GlobalScope()))
            fused = reciprocal_rank_fusion(
                candidates.dense_hits, candidates.lexical_result.hits, rrf_k=60
            )
            lexical = [
                (hit.chunk_key, rank) for rank, hit in enumerate(candidates.lexical_result.hits, 1)
            ]
            rows.append(
                {
                    "id": case["id"],
                    "language": language,
                    "answerable": case["answerable"],
                    "lexical_hits": len(candidates.lexical_result.hits),
                    "dense_rank": _best(
                        [(hit.chunk_key, hit.rank) for hit in candidates.dense_hits],
                        pages,
                        case["expected"],
                    ),
                    "lexical_rank": _best(lexical, pages, case["expected"]),
                    "fused_rank": _best(
                        [(item.chunk_key, item.fused_rank) for item in fused],
                        pages,
                        case["expected"],
                    ),
                    "top_dense_distance": (
                        candidates.dense_hits[0].distance if candidates.dense_hits else None
                    ),
                }
            )
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    return rows


async def _answers(runtime: Any, cases: list[dict[str, Any]]) -> list[Any]:
    rows = []
    for case in cases:
        for language in ("en", "ar"):
            started = time.monotonic()
            row: dict[str, Any] = {
                "id": case["id"],
                "language": language,
                "answerable": case["answerable"],
            }
            try:
                response = await runtime.service.ask(RagQuery(case[language], GlobalScope()))
                citations = response.answer.citations
                row["status"] = response.answer.status.value
                row["citations"] = [
                    [c.document_version_id, c.page_start, c.page_end] for c in citations
                ]
                row["answer_hit"] = any(
                    _matches((c.document_version_id, c.page_start, c.page_end), case["expected"])
                    for c in citations
                )
            except RagOperationalError as error:
                row["status"] = f"ERROR:{error.code.value}"
                row["citations"] = []
                row["answer_hit"] = False
            row["seconds"] = round(time.monotonic() - started, 1)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


async def _answers_then_close(runtime: Any, cases: list[dict[str, Any]]) -> list[Any]:
    # The gateway's connections belong to this event loop, so close it here.
    try:
        return await _answers(runtime, cases)
    finally:
        await runtime.aclose()


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"retrieval", "answers"}:
        print(__doc__)
        return 2
    mode, output = argv
    cases = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
    runtime = build_configured_phase4_runtime(data_root=Path("data"))
    if mode == "retrieval":
        try:
            rows = _retrieval(runtime, _chunk_pages(Path("data/database/aaos.db")), cases)
        finally:
            runtime.close()
    else:
        rows = asyncio.run(_answers_then_close(runtime, cases))
    Path(output).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
