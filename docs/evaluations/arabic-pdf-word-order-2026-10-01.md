# Arabic Word Order in PDF Extraction — Investigation (2026-10-01)

No production code was changed. Prototypes ran read-only against the stored PDFs.

## Failure definition

Asked in Arabic who teaches AI 495C, Ask answered "الدكتور أحمد بني سليمان". The instructor is Sulieman Bani-Ahmad, which in Arabic reading order is "سليمان بني أحمد". The answer faithfully quotes the extracted text, but the extracted text has its Arabic words in reverse order. For example, the page header is stored as:

> Sulieman.net/c أحمد بني سليمان بالدكتور الخاصة المساقات موقع

when it should read:

> موقع المساقات الخاصة بالدكتور سليمان بني أحمد

## Root cause (facts)

- `src/aaos/documents/pdf.py` stores `text_page.get_text_bounded()` from pypdfium2 as-is, with no bidirectional handling.
- The character boxes show what the PDF contains:
  - Within each Arabic word, characters are emitted in reading order: their x-coordinates decrease.
  - Between words, they are emitted left to right by position on the page: "أحمد" at x≈115–130, then "بني" at x≈134–144, then "سليمان" at x≈149–166.
- pdfium returns that stream order verbatim, so every right-to-left phrase comes out with its word order reversed.
- Some tokens containing punctuation (e.g. "أ.د.") are stored with their characters in visual order as well.

**Violated invariant:** extracted page text should be in reading order, because chunking, retrieval, generation and citation quotes all treat it as such.

## Scope (measured on the stored PDFs)

| Document | Pages | Lines with Arabic | In visual order |
|---|---|---|---|
| AI 495C syllabus (versions 1 and 6, same file) | 4 | 12 | 12 |
| SE440 syllabus, ML notes, KNIME notes, mini-project | 140 | 0 | — |

The affected lines are the syllabus header, the instructor name, and the office, contact and policy headings. No stored PDF emits Arabic in reading order, so the change below never had a correct line to break.

## Candidate rules (prototypes)

| Rule | Result on the 12 lines |
|---|---|
| Reverse runs of Arabic words when their positions increase left to right | Fixes plain phrases; "أ.د." stays ".د.أ"; bracketed text is scrambled |
| Order every character right to left, keep Latin runs, mirror brackets | All Arabic correct; reverses multi-word English ("Policies Course Specific"), breaks the email address; mirroring was wrong |
| **Run-level geometric order** (below) | **11/12 exact**; 1 with cosmetic bracket spacing ("الاصطناعي( مبنى" for "الاصطناعي (مبنى") |

**Run-level geometric order**, applied only to lines containing Arabic:

1. Split the line into words, each with its character positions.
2. Order the characters of each Arabic word right to left by position.
3. Group consecutive words into Arabic and non-Arabic runs.
4. If the line's rightmost word is Arabic, order runs right to left; otherwise order runs left to right.
5. Keep non-Arabic runs left to right internally, and Arabic runs right to left.

The rule depends only on geometry, so it gives the same result whether a PDF stores text in reading or visual order.

`get_text_bounded()` and per-character text are index-aligned on all 4 pages of the affected file. They differ only by the encoding of one soft-hyphen marker (`U+FFFE` vs `\x02`). An implementation can therefore keep the existing page text and fall back to it unchanged whenever the lengths differ.

## Proposed fix (needs approval)

1. Apply the run-level rule in `_extract_page` to lines containing Arabic, leaving every other line byte-identical.
2. Regression tests: a synthetic text page reproducing the visual-order line, a reading-order line that must stay unchanged, and mixed Latin and Arabic.
3. Re-ingest and re-index the AI 495C syllabus. Its stored pages and chunks were extracted with the old text.
   - Because the content revision changes, its cached summaries are invalidated and need re-preparing.
   - Versions 1 and 6 are the same file; removing the duplicate is a separate cleanup.

## Limitations

The rule is validated on one Word-generated PDF. Lines whose rightmost word is Arabic are treated as right-to-left paragraphs, which fits these headers but is a heuristic for unusual mixed layouts.
