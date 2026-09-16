# Phase 7B — Frontend Shell and AAOS Design System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase 7A contract-only frontend workspace into the real AAOS Next.js application shell with the approved visual system, same-origin FastAPI proxy, TanStack Query foundation, responsive navigation, and one real course-data proof without implementing the full Phase 7C academic experience.

**Architecture:** The browser talks only to Next.js same-origin routes. Next.js rewrites `/api/*` to FastAPI using a server-only `AAOS_BACKEND_ORIGIN`; it owns no AAOS business authority. Generated OpenAPI types remain the browser contract source. TanStack Query owns server state; React state remains UI-only. The shell follows the approved AAOS design: light background, calm academic SaaS feel, full desktop sidebar, compact tablet behavior, mobile drawer, restrained pastel accents, rounded surfaces, subtle borders/shadows, and Lucide icons.

**Tech Stack:** Next.js + React + TypeScript, Tailwind CSS, shadcn/ui primitives, Lucide, TanStack Query, Vitest + Testing Library, generated OpenAPI TypeScript contracts from Phase 7A.

**Spec:** `docs/superpowers/specs/2026-09-16-phase-7-dashboard-operational-visibility-design.md`

## Global Constraints

- Baseline: Phase 7A human-accepted at `61313d0`.
- Preserve every Phase 7A backend/API acceptance gate.
- `frontend/openapi/aaos-v1.json` and `frontend/types/api.generated.ts` remain generated artifacts; never hand-edit them.
- Keep `openapi-typescript` exact-pinned at `7.13.0`.
- No browser credentials, Moodle cookies, OAuth tokens, database URLs, filesystem paths, provider secrets, or `NEXT_PUBLIC_*` backend secrets.
- Next.js is transport/presentation only; no approval/RAG/task/integration authority in React or Next.js.
- 7B does not implement full Courses, Assignments, Documents, RAG, Tasks, Approvals, Activity, SSE, System Health, or Settings behavior.
- The only real-data proof in 7B is a compact active-course surface using `GET /api/v1/courses`.
- Desktop-first responsive: full sidebar desktop, compact/collapsed tablet, drawer mobile.
- One icon family: Lucide.
- No fake analytics, fake notifications, fake health, fake task/approval counts, or fabricated academic metrics.
- Follow TDD and commit after each independently reviewable task.

---

### Verified Task 1 toolchain snapshot (2026-09-16)

The first RED test was observed successfully before production code:
`Cannot find module '../next.config'`.

Exact resolved versions for Task 1:

```text
next=16.3.5
react=19.3.0
react-dom=19.3.0
typescript=5.9.3
tailwindcss=4.3.3
@tailwindcss/postcss=4.3.3
@tanstack/react-query=5.103.1
lucide-react=1.46.0
vitest=5.0.1
@testing-library/react=16.3.3
@testing-library/jest-dom=7.0.1
jsdom=30.0.1
eslint=9.39.5
eslint-config-next=16.3.5
@types/react=19.3.0
@types/react-dom=19.3.0
openapi-typescript=7.13.0
```

`typescript@7.0.2` was rejected after npm exposed the
`openapi-typescript@7.13.0 -> typescript@^5.x` peer requirement.
`next lint` was verified absent in Next.js 16.3.5, so the lint gate is
the direct ESLint CLI (`eslint .`). ESLint 10.10.0 was also rejected
after `npm ls eslint --all` proved peer incompatibility with the current
Next.js ESLint plugin stack; Phase 7B therefore exact-pins the newest
compatible `eslint@9.39.5` release resolved from the registry.

Do not approve or bypass npm install scripts merely to silence the
`allow-scripts` warning. If a later lint/build command demonstrates that a
blocked dependency install script is actually required, diagnose that
specific failure before changing npm script permissions.


## Task 1 — Convert the Contract Workspace into a Production Next.js App Foundation

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`, `frontend/next-env.d.ts`, `frontend/next.config.ts`, `frontend/postcss.config.mjs`, `frontend/eslint.config.mjs`
- Create: `frontend/app/layout.tsx`, `frontend/app/page.tsx`, `frontend/app/globals.css`
- Create: `frontend/vitest.config.ts`, `frontend/tests/setup.ts`, `frontend/tests/proxy-config.test.ts`

**Interfaces:**
- Produces `npm run dev`, `build`, `typecheck`, `test`, `lint`, `check:api-types`.
- Produces same-origin `/api/*` rewrite to server-only `AAOS_BACKEND_ORIGIN`.

- [ ] Resolve exact current package versions first; do not guess:

```powershell
$next=(npm view next version).Trim(); $react=(npm view react version).Trim(); $reactDom=(npm view react-dom version).Trim()
$typescript="5.9.3"; $tailwind=(npm view tailwindcss version).Trim(); $postcss=(npm view "@tailwindcss/postcss" version).Trim()
$tanstack=(npm view "@tanstack/react-query" version).Trim(); $lucide=(npm view lucide-react version).Trim()
$vitest=(npm view vitest version).Trim(); $testingReact=(npm view "@testing-library/react" version).Trim(); $testingDom=(npm view "@testing-library/jest-dom" version).Trim(); $jsdom=(npm view jsdom version).Trim()
```

- [ ] Write `frontend/tests/proxy-config.test.ts` before `next.config.ts`:

```ts
import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("AAOS transport boundary", () => {
  it("proxies /api through a server-only backend origin", async () => {
    process.env.AAOS_BACKEND_ORIGIN = "http://127.0.0.1:8765";
    const rules = typeof nextConfig.rewrites === "function" ? await nextConfig.rewrites() : [];
    expect(rules).toContainEqual({
      source: "/api/:path*",
      destination: "http://127.0.0.1:8765/api/:path*",
    });
  });
});
```

- [ ] Verify RED: `Push-Location frontend; npx vitest run tests/proxy-config.test.ts; Pop-Location`.
- [ ] Install exact-pinned dependencies using the resolved values, but pin `typescript@5.9.3` because `openapi-typescript@7.13.0` requires TypeScript `^5.x`; preserve `openapi-typescript@7.13.0`.
- [ ] Add scripts: `dev`, `build`, `start`, `lint`, `typecheck`, `test`, and preserve `generate:openapi`, `generate:api-types`, `check:api-types`. The lint script is exactly `eslint .`.
- [ ] Next.js `16.3.5` has no `next lint`; use the direct ESLint CLI. Create `eslint.config.mjs` from `eslint-config-next/core-web-vitals` plus `eslint-config-next/typescript`, with standard ignores for `.next/**`, `out/**`, `build/**`, and `next-env.d.ts`.
- [ ] Create strict `tsconfig.json` with `strict: true`, `noEmit: true`, bundler module resolution, and `@/* -> ./*` alias.
- [ ] Create `frontend/eslint.config.mjs`:

```js
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
```

- [ ] Create `next.config.ts`:

```ts
import type { NextConfig } from "next";
const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";
const nextConfig: NextConfig = {
  async rewrites() {
    const backendOrigin = process.env.AAOS_BACKEND_ORIGIN ?? DEFAULT_BACKEND_ORIGIN;
    return [{ source: "/api/:path*", destination: `${backendOrigin}/api/:path*` }];
  },
};
export default nextConfig;
```

- [ ] Create Tailwind/PostCSS foundation and `app/globals.css` tokens:

```css
@import "tailwindcss";
:root {
  color-scheme: light;
  --aaos-background: #f7f8fb;
  --aaos-surface: #ffffff;
  --aaos-surface-soft: #f3f5f8;
  --aaos-text: #182230;
  --aaos-text-muted: #667085;
  --aaos-border: #e6e9ef;
  --aaos-accent: #6f74df;
  --aaos-accent-soft: #eeefff;
  --aaos-success: #287a5f;
  --aaos-warning: #a46818;
  --aaos-danger: #b54747;
  --aaos-focus: #4f56c8;
  --aaos-radius-card: 1rem;
  --aaos-shadow-card: 0 10px 30px rgb(28 39 54 / 0.06);
}
```

- [ ] Create minimal `layout.tsx` and `page.tsx`; no fake dashboard data.
- [ ] Create Vitest config with jsdom and `@testing-library/jest-dom/vitest` setup.
- [ ] Verify GREEN: frontend test, `npm run lint`, typecheck, production build, API drift gate.
- [ ] Commit: `feat(phase7b): establish Next.js frontend foundation`.

---

## Task 2 — Initialize shadcn Primitives and Freeze the AAOS Design Tokens

**Files:** `frontend/components.json`, `frontend/lib/utils.ts`, `frontend/components/ui/{button,sheet,skeleton,tooltip}.tsx`, `frontend/app/globals.css`, `frontend/tests/design-system.test.tsx`.

- [ ] Resolve exact `shadcn` CLI version with `npm view shadcn version`.
- [ ] RED test: render `Button`, assert semantic button and `focus-visible` styling; expected FAIL before primitive exists.
- [ ] Run exact-pinned shadcn CLI to initialize and add only `button`, `sheet`, `skeleton`, `tooltip`.
- [ ] Review generated CSS immediately; preserve AAOS tokens and remove any unapproved gradients/theme invention.
- [ ] GREEN: component test, typecheck, production build.
- [ ] Commit: `feat(phase7b): add AAOS design primitives`.

---

## Task 3 — Build the Responsive AAOS Shell and Navigation

**Files:** `frontend/components/aaos/{app-sidebar,mobile-nav,page-header,app-shell}.tsx`, `frontend/app/layout.tsx`, `frontend/tests/{navigation,app-shell}.test.tsx`.

**Frozen navigation labels:** Overview, Courses, Assignments, Documents, Ask AAOS, Tasks, Approvals, Activity, System Health, Settings.

- [ ] RED navigation test verifies exactly those 10 links under `nav[aria-label="AAOS"]`.
- [ ] Implement one shared navigation array using Lucide icons only.
- [ ] RED shell test verifies `navigation`, `banner`, and `main` landmarks.
- [ ] Implement desktop sidebar + mobile Sheet drawer; no fake badges/counts/search/notifications.
- [ ] Header copy for 7B: `Overview` and `Your academic workspace` only.
- [ ] Compose shell in root layout.
- [ ] GREEN: tests, typecheck, build.
- [ ] Commit: `feat(phase7b): build responsive AAOS app shell`.

---

## Task 4 — Add TanStack Query Provider and Typed Courses API Client

**Files:** `frontend/components/providers/query-provider.tsx`, `frontend/lib/api/courses.ts`, `frontend/lib/query/{keys,courses}.ts`, `frontend/app/layout.tsx`, `frontend/tests/course-api.test.ts`.

**Interfaces:**
- `CourseSummary = components["schemas"]["CourseSummary"]`
- `CourseListResponse = components["schemas"]["CourseListResponse"]`
- `fetchCourses(signal?: AbortSignal): Promise<CourseListResponse>`
- `courseKeys.all = ["courses"]`
- `courseListQueryOptions()`

- [ ] RED test stubs `fetch` and proves the client calls relative `/api/v1/courses`, never a backend origin.
- [ ] Implement `fetchCourses` with `GET`, `Accept: application/json`, and a closed generic error `AAOS courses request failed (<status>)`; do not surface raw response body.
- [ ] Implement TanStack Query provider with query retry `1`, mutation retry `false`, and `refetchOnWindowFocus: false`.
- [ ] GREEN: tests, typecheck, build, API drift gate.
- [ ] Commit: `feat(phase7b): add typed frontend data foundation`.

---

## Task 5 — Add the First Real Course Data Proof to Overview

**Files:** `frontend/components/aaos/{section-card,course-proof-card}.tsx`, `frontend/app/page.tsx`, `frontend/tests/course-proof-card.test.tsx`.

- [ ] RED tests cover loading, empty, success, and local error states.
- [ ] Required copies:
  - `No active courses found.`
  - `Academic data is temporarily unavailable.`
  - `View courses`
- [ ] Success state shows at most first 3 real course titles/codes. Do not invent grade/progress/deadline data.
- [ ] Loading state has `aria-label="Loading courses"` plus skeleton rows.
- [ ] Error remains local to the course surface; shell must remain intact.
- [ ] Compose only this real surface on Overview; leave unimplemented feature regions absent rather than fake.
- [ ] GREEN: component tests, all frontend tests, typecheck, build, API drift gate.
- [ ] Commit: `feat(phase7b): show real course data in overview shell`.

---

## Task 6 — Responsive, Accessibility, Browser Fidelity, and 7B Acceptance

- [ ] Frontend gates: `npm run test`, `npm run typecheck`, `npm run build`, `npm run check:api-types`.
- [ ] Repository gates: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src`, `uv run mypy src/aaos scripts/export_openapi.py`, `uv lock --check`, `git diff --check`.
- [ ] Browser-artifact leak scan must find no `source_system`, `source_id`, auth tokens, credential paths, DB URLs, or local user paths.
- [ ] Start backend: `uv run uvicorn aaos.main:app --host 127.0.0.1 --port 8000`.
- [ ] Start frontend with server-only `AAOS_BACKEND_ORIGIN=http://127.0.0.1:8000`.
- [ ] Verify desktop (~1440px): full sidebar, exact labels, Overview header, real Course proof, no fake modules, no horizontal overflow.
- [ ] Verify tablet (~768px) and mobile (~390px): drawer works with keyboard, content stacks, no horizontal overflow.
- [ ] Capture browser screenshots and compare against the approved AAOS concept. Fidelity ledger must inspect at least: sidebar density/icon treatment, palette, card geometry, typography, responsive collapse.
- [ ] Above-the-fold copy audit: only AAOS shell copy, approved navigation labels, real course values, and the three frozen Course-proof strings above. No fake greeting, notification counts, learning analytics, health claims, pending approval/task counts.
- [ ] Re-run generated contract drift gate and require zero diff.
- [ ] Review `git diff 61313d0...HEAD -- frontend`; confirm no 7C workflows or authority logic.
- [ ] Final clean-tree acceptance run; `git status --short` must be empty.

## Phase 7B Acceptance Criteria

```text
NEXTJS_PRODUCTION_BUILD          = PASS
FRONTEND_TESTS                   = PASS
TYPESCRIPT_STRICT                = PASS
API_TYPES_DRIFT                  = PASS
BACKEND_FULL_PYTEST              = PASS
RUFF                             = PASS
MYPY                             = PASS
UV_LOCK_CHECK                    = PASS
DIFF_CHECK                       = PASS
SAME_ORIGIN_API_PROXY            = PASS
BACKEND_ORIGIN_BROWSER_SECRET    = ABSENT
TANSTACK_QUERY_FOUNDATION        = PASS
GENERATED_TYPES_CONSUMED         = PASS
DESKTOP_SHELL                    = PASS
TABLET_RESPONSIVE                = PASS
MOBILE_DRAWER                    = PASS
KEYBOARD_NAVIGATION              = PASS
NO_HORIZONTAL_OVERFLOW           = PASS
REAL_COURSE_DATA_PROOF           = PASS
FAKE_DASHBOARD_METRICS           = ZERO
UNAPPROVED_FEATURES              = ZERO
BROWSER_SECRET_EXPOSURE          = ZERO
NODE_MODULES_TRACKED             = ZERO
APPROVED_CONCEPT_FIDELITY        = HUMAN_REVIEWED
WORKTREE                         = CLEAN
```

## Self-Review

- Spec coverage: frontend runtime, same-origin proxy, generated contract use, TanStack Query, AAOS design system, responsive shell, approved navigation, one real Course proof, accessibility, production build.
- Out of scope: full Courses page, Assignments, Documents, RAG, Tasks, Approvals, Activity, SSE, System Health, Settings behavior.
- Placeholder scan: no TODO/TBD/“implement later”.
- Security: backend origin is server-only; browser code uses relative `/api/v1/courses`.
- Authority: Next.js/React remain presentation/transport only.

## Execution Handoff

Execute inline in the existing isolated `phase-07-dashboard` worktree using `superpowers:executing-plans`, task-by-task with review checkpoints. Do not begin 7C until 7B receives technical and human acceptance.
