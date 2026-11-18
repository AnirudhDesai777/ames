# AGENTS.md

These conventions apply to anyone working in this repo.

## Workflow

The maintainer chooses the coding agent and any optional build or review harness per task. Work directly from the maintainer's request or the assigned issue in a fresh worktree. The maintainer owns design decisions and merges PRs. Regressions, safety problems, and crashes remain grounds to block a merge.

## Repo map

Read this before searching. Paths marked (planned) do not exist yet; the first issue that needs one creates it at that path.

| Path | What lives there | Read it when |
|---|---|---|
| `AGENTS.md` | These rules | Always, first |
| `docs/design.md` | v1 design: pipeline, budgets, memory schema, persona, guardrails | Before any feature work |
| `docs/roadmap.md` | Work after v1: NVIDIA, web search, fine-tuning, duplex, cloning | Never build from it without an issue |
| `docs/reviews/` | EGM review reports on the design and on PRs | Before changing a design decision |
| `docs/architecture/` (planned) | C4 diagrams (Mermaid) | Before changing how components connect |
| `server/` | Python server: Pipecat SmallWebRTC echo, model-free stages and tests | Backend issues |
| `web/` | Browser client: Vite and TypeScript scaffold, local WebRTC config and tests | UI issues |
| `persona/` | Ames character sheet and system prompt, versioned | Persona, tone, or guardrail issues |
| `evals/` | Frozen eval sets and runners (persona, crisis, memory, latency) | Any change that can move an eval score |
| `.github/` | PR template and CI workflows | Opening a PR or changing CI |
| `.githooks/` | Local git hooks (blocks pushes to main) | Never edit without an issue |

Rules for the map:
- Start from the task, then the files it names, then this map. Do not scan the whole repo.
- A PR that adds or removes a top-level directory updates this table in the same PR.
- Do not edit `docs/reviews/` or frozen eval sets unless the issue says to.

## Scope

- Build only what the task asks. If you find something else worth doing, write it in the PR under "Follow-ups". Do not build it.
- If the brief is unclear or wrong, stop and say so in the PR instead of guessing.

## Code style

- The smallest change that fully solves the issue. No speculative options, wrappers, or "for later" code.
- Reuse what the repo and its dependencies already provide before writing new code. Do not add a dependency for a few lines.
- Comment only the why the code cannot show.
- A known shortcut gets a comment: `shortcut: <the limit>, <when to upgrade>`.
- New logic gets a small test. Never cut input validation, error handling that prevents data loss, or security.
- No leading underscores on names. No emojis or check-mark characters anywhere in the repo.

## Code smells

Use these prompts when reading a diff. Flag a smell when you can name the affected caller, failure case, or cost of the extra code. Suggest the smallest change that addresses it.

- Speculative flexibility. A flag, dependency, wrapper, or extension point has no current caller that needs it. Check the repo and standard library for an existing solution.
- A fix in one caller for a shared defect. Trace the other callers before patching. Fix the behavior where it originates and keep each caller's validation and error handling.
- A silent failure followed by a success action. For example, if a closed data channel skips a send, clearing the text box loses the draft. Let the caller observe whether submission succeeded.
- A test starts after the changed handoff. Injecting a typed frame proves the downstream processor handles it. To cover submission, enter through the session handler and observe the transcript or stub reply.
- A contract is hidden in wiring. Attaching `submit` to a returned function hides a capability its caller needs. Make the operations visible at the return site.
- A pipeline is built and then rewritten by searching for stage classes. When changing the composition, express the intended stage order where it is assembled.

## Git

- Never push to main. Work on a branch named `<type>/<short-topic>`, for example `feat/echo-transport`.
- Conventional Commits, small and atomic: one logical change per commit.
- No `Co-Authored-By` trailers and no tool attribution in commits or PRs.
- Open a PR using `.github/pull_request_template.md`. The PR title is a Conventional Commit; it becomes the squash commit on main.
- Run `git config core.hooksPath .githooks` once per clone.

## Tests

- Run the narrow tests for what you changed on each commit, and the full suite once before opening the PR.
- Tests must not need a GPU, microphone, network, or downloaded models. Stub models and use WAV fixtures. Checks that need real hardware go in the PR under "Manual checks" for the maintainer to run.

## Writing

- Plain prose in docs and PRs. No slogans, no dramatic one-liners, no bolded sentences for effect.
