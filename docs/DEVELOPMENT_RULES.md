# Development Rules

## Phase gating

- Work only within the active phase defined in `PHASES.md`.
- Do not begin a later phase until the current acceptance gate is demonstrated and recorded.
- Do not add speculative scaffolding or future-phase functionality early.
- If a task crosses a gate, stop at the gate and request explicit direction.

## Testing requirements

- Add or update tests whenever behavior changes.
- Cover success, invalid-input, boundary, and failure paths appropriate to the change.
- Run the most relevant tests after implementation work and report the exact commands and results.
- Never delete, skip, loosen, or rewrite a test merely to make a failure disappear.
- Expensive workflows must have safe smoke tests; unavailable tests must be reported as a limitation.

## Security boundaries

- Treat model output, retrieved content, tool output, and environment observations as untrusted input.
- Trusted application code must validate and authorize every executable action.
- Never expose credentials, Moodle state, tokens, secrets, or sensitive user data in source, logs, prompts, fixtures, or reports.
- Never commit anything under `data/auth/` or `data/credentials/`.
- Use least privilege and explicit allowlists for tools and environment access.
- External writes require the AAOS approval architecture once that capability is implemented.
- Fail loudly and specifically; do not silently bypass validation, approval, or security controls.

## Git workflow

- Keep changes small, focused, and reviewable.
- Inspect the working tree before editing and preserve unrelated user changes.
- Stage only intended files. Do not rewrite history, force-push, or perform destructive Git operations without explicit approval.
- Do not commit unless explicitly requested.
- Report the resulting working-tree status after each task.

## Dependency discipline

- Add no dependency until the active phase requires it and the existing stack has been evaluated first.
- Prefer mature, maintained, narrowly scoped dependencies.
- Document the purpose, versioning choice, security implications, and operational cost of each addition.
- Keep dependency declarations and lock data consistent and reproducible.

## No premature implementation

- Governance documents describe intended boundaries; they do not authorize implementation.
- Do not create application scaffolding, integrations, persistence, retrieval, MCP, agents, or execution features before their phase begins.
- Avoid choosing concrete technologies until the relevant phase documents and acceptance criteria require that decision.

## Local-first design

- Prefer local computation and storage for sensitive academic context.
- Make any network use or remote processing explicit, minimal, and reviewable.
- Keep local and external boundaries visible so users can understand where data and actions go.
- Local-first does not weaken validation, auditability, backups, or secret handling.

## Task closeout

Every completed task report must list:

1. files changed;
2. commands executed;
3. tests executed;
4. failures or limitations;
5. recommended next action.
