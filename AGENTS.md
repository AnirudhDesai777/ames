# AGENTS.md

Rules for any coding agent (Codex, Claude Code) working in this repo.

## Roles

- Claude Code orchestrates: it plans, writes each issue with acceptance criteria, and runs the pipeline below.
- Builder: Codex. It builds one issue at a time in a fresh worktree, with the ponytail skill, commits, and writes the PR description. Claude Code only launches it and opens the PR from Codex's text.
- Reviewer: the EGM plugin (`egm:egm-synthesizer` and its reviewers) reviews the PR diff. Only regressions, safety problems, and crashes block a merge.
- The maintainer owns design decisions and merges PRs.

Pipeline per issue: issue -> builder (Codex) -> PR -> EGM review -> fixes -> maintainer merges.

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
| `evals/` (planned) | Frozen eval sets and runners (persona, crisis, memory, latency) | Any change that can move an eval score |
| `.github/` | PR template and CI workflows | Opening a PR or changing CI |
| `.githooks/` | Local git hooks (blocks pushes to main) | Never edit without an issue |

Rules for the map:
- Start from the issue, then the files it names, then this map. Do not scan the whole repo.
- A PR that adds or removes a top-level directory updates this table in the same PR.
- Do not edit `docs/reviews/` or frozen eval sets unless the issue says to.

## Scope

- Build only what the issue asks. If you find something else worth doing, write it in the PR under "Follow-ups". Do not build it.
- If the brief is unclear or wrong, stop and say so in the PR instead of guessing.

## Code style (ponytail)

- The smallest change that fully solves the issue. No speculative options, wrappers, or "for later" code.
- Reuse what the repo and its dependencies already provide before writing new code. Do not add a dependency for a few lines.
- Comment only the why the code cannot show.
- A known shortcut gets a comment: `shortcut: <the limit>, <when to upgrade>`.
- New logic gets a small test. Never cut input validation, error handling that prevents data loss, or security.
- No leading underscores on names. No emojis or check-mark characters anywhere in the repo.

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
