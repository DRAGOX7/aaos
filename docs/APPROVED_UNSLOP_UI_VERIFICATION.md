# Approved Unslop UI implementation

Date: 2026-10-30. Branch: `codex/approved-unslop-ui`.
Baseline: `b06d3c82495bd14dca5700f8d962a71d709ec4be`, including the
`20bdcff3` Ask integration merge, `4bbe0b8e` Evidence Inspector V2,
and the subsequent trusted-reference fixes. This is a frontend change;
it does not establish a new backend phase acceptance gate.

## Implemented scope

- **Dashboard:** only Up Next and Workspace received visual redesigns.
  Up Next groups course, assignment and due timestamp, allows wrapping on
  narrow screens, and uses the existing urgency classifier. Workspace uses
  divided rows and aligned value badges, and keeps its content height.
- **Ask:** more space between messages, a full-width answer card without the
  side avatar, 16px/28px answer text, quieter source headings, larger bordered
  source controls, a separate conversation toolbar, and a consistent composer.
- **Evidence:** a roughly 58/42 desktop split, more passage space, a bordered
  selected-claim quote, grouped document/version/location metadata, clearer
  highlighting caveat, and accessible surrounding context/original navigation.
  Narrow-screen modal behavior and desktop interactivity remain intact.

The hero's dimensions, gradient, padding, text, suggestions, emojis, placement,
and surrounding course-card composition are preserved. Course-card component,
colors, icons, typography classes, spacing and interaction code are unchanged.
Sidebar/navigation styling is unchanged. Existing counts are unchanged.
The permitted exceptions are hero input focus and primary-button contrast,
the minimal grid sizing correction, and Arabic font selection. Correct Arabic
glyph metrics can change text wrapping; this is not a card redesign.

## Confirmed P1 causes and corrections

| Issue | Root cause | Correction and verification |
| --- | --- | --- |
| Incomplete Calendar preview | Only summary/integration/approval expiry were displayed | Shared read-only preview shows actual start, end, timezone, location and description in Approvals and Calendar actions; boundary-date regression |
| Capability flags ignored | Approvals used `status === PENDING` alone | Each decision also requires its corresponding server capability; false/false and asymmetric regression cases |
| Deadline disagreement | AssignmentRow forced UTC and omitted time; Overview used viewer time | Shared formatter uses viewer timezone and includes time/zone; English/Arabic cross-midnight and cross-view regressions |
| Mobile Overview overflow | Implicit grid track retained the deadline row's minimum content width | Explicit `grid-cols-1`, `min-w-0`, and wrapping deadline rows; actual 390px English/Arabic checks |
| Primary contrast | White on the existing light/dark blue failed normal-text AA | Light primary-strong with white; dark primary with background-colored ink; measured 5.66:1 light and 5.72:1 dark |
| Hero input focus | Outline removed without a replacement | Focus-visible ring without dimensional changes; actual computed ring check |
| Arabic rendered in Arial | Next's Jakarta variable contains Arial before Noto | Same loaded Jakarta family first, Noto variable next, managed Jakarta fallbacks last; static regression plus actual CDP platform font inspection |
| Missing mobile model/privacy entry | Trigger had `hidden ... sm:flex` | Accessible 44px icon trigger on mobile, existing desktop presentation; disclosure and focus-return browser checks |
| Wrong mobile active item | Overview was hardcoded active | Reuse existing pathname resolver; nested-route regression and actual mobile Ask check |
| Fabricated Activity status | Null status defaulted to PENDING | Render only a supplied status; null regression, existing unknown-outcome tests retained |

For the font issue, `adjustFontFallback: false` did not remove the generated
fallback in the installed Turbopack output. That ineffective option was removed.
Both generated development and production CSS expose `Plus Jakarta Sans` as
the actual loaded family. The explicit first family preserves that face while
placing Noto before Arial. No color or layout tokens were changed.

## Preserved invariants

- Presentation consumes the same final verified Ask response and citation
  objects. No citation identifier is inferred from arbitrary bracketed answer
  text. The source rows remain the authoritative inspection controls.
- Source fetching, access checks, version/location validation, optional metadata
  fallback, alignment, original text and original-file URLs are unchanged.
- The highlight still identifies a **retrieved passage**, not a minimal
  independently verified claim span. Claim, passage and surrounding context
  retain separate sections.
- Streaming, stage updates, cancellation/retry, archived-course provenance,
  multiple answers and sources, and backend APIs are unchanged.
- Calendar preview is descriptive. Backend approval/execution remains
  authoritative; no optimistic success or capability escalation was added.

Independent review caught two regressions during development: moving the toolbar
outside the desktop Escape handler, and a proposed standalone 48-hour urgency
rule. The handler now covers header and grid; the existing classifier is reused.
Both corrections have targeted regressions and received independent follow-up.

## Verification

Commands executed from the appropriate repository/frontend directory:

```powershell
git status --short
git log -6 --oneline
git merge-base --is-ancestor 4bbe0b8e HEAD
git rev-parse eval/lfm3-1.7b
git diff --check
npx vitest run tests/approval-views.test.tsx tests/activity-views.test.tsx tests/navigation.test.tsx
npx vitest run tests/approval-views.test.tsx tests/activity-views.test.tsx tests/navigation.test.tsx tests/overview-academic-summary.test.tsx tests/assignment-view.test.tsx tests/task-views.test.tsx tests/citation-evidence.test.tsx tests/ask-view.test.tsx tests/model-badge.test.tsx
npx vitest run tests/deadline-display.test.tsx tests/evidence-inspector-v2.test.tsx
npx vitest run tests/design-tokens.test.ts tests/deadline-display.test.tsx
npm test
npm run lint
npm run typecheck
npm run build
.venv\Scripts\python.exe -m pytest tests/test_ask_stream.py tests/test_ask_local_topic_reference.py tests/test_phase7e_approval_reads.py tests/test_phase7e_approval_commands.py -q
```

- Initial regression run: five expected failures reproduced ignored capability
  flags, fabricated Pending and incorrect mobile route selection.
- Final frontend suite: **381 tests, 58 files passed**. Focused final font/date
  checks also passed (9 tests).
- Backend: **46 tests passed**, using disposable fixture data. This covers Ask
  streaming, same-message topics versus trusted references, and approval APIs.
- ESLint: zero errors; two existing unused-import warnings in
  `course-detail-view.tsx` and `document-detail-view.tsx`.
- TypeScript and production build passed. Build retains the pre-existing
  parent-lockfile and experimental proxyTimeout notices.
- Independent scoped evidence review and final branch/security review completed;
  identified issues were corrected and follow-up review found no blockers.

## Actual visual and interaction evidence

Local artifact directory (kept outside Git, contains synthetic data only):

`C:\Users\aljaf\.codex\visualizations\2026\10\30\01a18c7e-d27f-7e52-9363-d7644823b71d\aaos-approved-ui\`

Open `index.html` for side-by-side actual before/after screenshots and links to
additional interaction captures. This is implemented application evidence,
not a rerender of the proposed HTML concepts.

- 36 matched baseline and 36 implemented captures with the same synthetic
  data, 2026-10-30 clock, Asia/Amman, locale, theme and viewport.
- Main matrix: 1440px, 768px, 390px; English and Arabic; dark and light examples.
- Additional 31 captures and **95 passing browser assertions**, including
  1200px split view, Arabic long answers/context, short answers, insufficient
  evidence, multiple sources, correct highlights/version URLs, context
  expansion, primary contrast, input ring, provider disclosure and focus return.
- Six further screenshots check progress, cancellation and failure on desktop
  and Arabic mobile. Original-document navigation opened intercepted synthetic
  PDFs successfully in both cases.
- Zero horizontal overflow in final captured layouts. Baseline reproduced
  Overview overflow at 390px in both languages.
- English course sections at 1440px dark/light and 768px dark are pixel-identical
  in matched crops. English sidebar crops are also pixel-identical. Arabic text
  rasterization/wrapping changes with the permitted font correction. Hero
  screenshots retain its geometry/composition; pixel differences include the
  permitted button correction and text/gradient rasterization. Raw comparison
  results are in `preservation.json`; no claim of exact whole-hero pixel equality.

Reproduction scripts in that artifact directory:

```powershell
.venv\Scripts\python.exe <artifact-directory>\capture.py before
.venv\Scripts\python.exe <artifact-directory>\capture.py after
.venv\Scripts\python.exe <artifact-directory>\verify.py
.venv\Scripts\python.exe <artifact-directory>\states.py
.venv\Scripts\python.exe <artifact-directory>\compare.py
```

These scripts use the audit's existing fixture harness and installed Chrome via
Playwright. Every API request is intercepted; original-PDF checks intercept at
browser-context scope, including popups. They do not exercise real course data,
cloud models, protected evaluation data or external Calendar writes.

## Changed files

- `frontend/app/globals.css`: corrected font fallback order only.
- `frontend/components/ui/button.tsx`: primary contrast.
- `frontend/components/aaos/overview-{academic-summary,hero,up-next,workspace}.tsx`:
  permitted dashboard changes and narrow fixes.
- `frontend/components/aaos/ask-{view,conversation,response-renderer,result-cards}.tsx`:
  approved Ask presentation.
- `frontend/components/aaos/citation-evidence.tsx`: inspector presentation and
  toolbar-aware Escape handling.
- `frontend/components/aaos/{approval-list-view,task-list-view,calendar-action-preview}.tsx`:
  complete read-only action preview and capability handling.
- `frontend/components/aaos/{assignment-row,mobile-nav,model-badge,activity-list-view}.tsx`:
  targeted P1 corrections.
- `frontend/lib/time/deadline.ts`, `frontend/lib/i18n/messages-ar.ts`: shared
  timestamp presentation and required interface translations.
- Tests: `approval-views`, `activity-views`, `navigation`, `deadline-display`,
  `evidence-inspector-v2`, `design-tokens`.
- This verification report.

## Limitations and next action

Browser checks use representative synthetic data, not successful live-model
responses or real academic content. They are targeted accessibility checks,
not a complete WCAG audit. Other P2/P3 audit findings remain out of scope.
Global search and unrelated page styling were not redesigned.

No merge, push, deployment or PR is authorized or performed. The local feature
commit is the reviewable deliverable. Existing `frontend/next-env.d.ts` and
unrelated untracked files are preserved and excluded. The evaluation branch
remains `bdcd702196a3b29dfac2dd1979abe41e286d456c`.

Recommended next action: review the implemented screenshot gallery and local
feature commit before deciding whether to integrate it into main.
