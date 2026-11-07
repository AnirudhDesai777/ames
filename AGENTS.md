# AGENTS.md

Rules for any coding agent (Codex, Claude Code) working in this repo.

## Roles

- Claude Code plans, writes the issue brief, and reviews the PR.
- Codex builds one issue at a time, exactly as briefed.
- Anirudh owns design decisions and merges PRs.

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
- Tests must not need a GPU, microphone, network, or downloaded models. Stub models and use WAV fixtures. Checks that need real hardware go in the PR under "Manual checks" for Anirudh to run.

## Writing

- Plain prose in docs and PRs. No slogans, no dramatic one-liners, no bolded sentences for effect.
