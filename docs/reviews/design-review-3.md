# Goldfish Review Report 3

**Target:** `docs/design.md` version 3 and `docs/roadmap.md`
**Date:** 2026-11-07
**Mode:** REVIEW, narrow verification pass (round 3)
**Prior review:** `docs/reviews/design-review-2.md` (NB1-NB9, MJ1-MJ19)
**Fixed and not reviewed:** the owner decisions in section 3. The eval thresholds are proposals for the maintainer to confirm, so they are not reviewed either.

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `implementation-readiness` | completed |
| `design` | completed |

**Prior items:** 28 of 28 resolved. Four have small leftovers that do not block (MJ2, MJ8, MJ13, MJ16).
**New blockers:** 1 confirmed (NB10). 1 conditional (NB11), which is a blocker only if issue #12 does not list the candidates.

**Overall verdict:** the doc is ready to build once NB10 is decided. That needs one line or one small change in S1b. Every slice in 13.3 except B can start now. B can start once the candidate list is confirmed (NB11).

---

## Part 1: Verdicts on review 2

Line numbers refer to design.md unless another file is named.

| Item | Verdict |
| --- | --- |
| NB1 | Resolved. Slice P writes `persona/ames.md` (L257, L442). S3b depends on S3a and P, B depends on E, S2a and S3b (L449-450), and S3d is removed. |
| NB2 | Resolved. 8.4 (L286-294) defines how crisis mode works. The locale is sent on `/api/offer`. The late block adds the crisis instruction and the resources. A UI card appears on `state.crisis`. A malformed token keeps the current state. The `turn.crisis` rows and the `resources.json` schema are defined, slice R supplies the data, and S6 depends on S3b, R and W. |
| NB3 | Resolved. mlx-lm runs in-process, so there is a single listener (L77, L302). The alternatives table records the choice (L409). |
| NB4 | Resolved. The doc adds `turn.extracted_at`, at-most-once extraction, and a `delete_epoch` check inside the write transaction (L164, L192, L220, L226). History covers the current session only (L99, L235), and "reappears" is defined (L236). |
| NB5 | Resolved. S2b is a standalone script (L446). The concurrent-load test moved to S8 (L462). S5d depends on S5b (L458). S1b owns the HTTP app and the security checks (L441). |
| NB6 | Resolved. D is a conditional v1 slice (L45, L452, L470), and roadmap.md L23 agrees. |
| NB7 | Resolved. There is no reduced profile (L54). On out-of-memory, the server stops and states the RAM requirement (L394). |
| NB8 | Resolved. CI runs on `ubuntu-latest`. MLX is a platform-marked `mac` extra, and `AMES_STUB=1` selects stubs (L429-432). |
| NB9 | Resolved. `GET /` is exempt from the token. The header is `X-Ames-Token`, with the offer body as a fallback (L123, L305). |
| MJ1 | Resolved. Latency runs from when Smart Turn fires to the first audio frame sent (L83), and 10.4 measures the same interval (L371). |
| MJ2 | Resolved. Release gates (10.6), the refusal definition (L353), the crisis split (L358), M0 (L331) and the record schemas (L339-347) are all in. Small leftover: L331 says every set has a split, but the memory and latency schemas have no `split` field. E can settle this. |
| MJ3 | Resolved. There are separate conversation and extraction caches, and cancellation is checked between tokens (L77). |
| MJ4 | Resolved. History is capped at 3000 tokens and covers the current session only (L99). |
| MJ5 | Resolved. The larger extraction model is removed. The fallback is a narrower predicate set, then D (L227). |
| MJ6 | Resolved. Licensing is split: L1 checks STT, TTS and VAD after S2b, and L2 checks the LLM and judge after B. S3c depends on L1. |
| MJ7 | Resolved. A functional fact replaces its row (L209). |
| MJ8 | Resolved. A per-fact delete removes orphaned entities and aliases (L232). Turn text is deleted after extraction once the session has ended (L235). Small leftover: see judgment call J2. |
| MJ9 | Resolved. The memory and health response shapes, the notice codes, the localStorage advisory flag, client-only mute and end, and the crisis card are specified (L118, L129-140). |
| MJ10 | Resolved. The data directory is 0700 with umask 077, both pragmas are set (L152), and logs hold error codes only (L247, L315). |
| MJ11 | Resolved. Failure rows exist for an LLM hang, a port in use and a second tab, and hashes are checked at setup only (L322-323, L395-401). The Orpheus self-test still runs on an idle GPU, but S8 covers behaviour under load. Judgment call. |
| MJ12 | Resolved. `iceServers` is empty on both sides (L62, L325). `HF_HOME` and `HF_HUB_OFFLINE` are set before imports (L79, L324). |
| MJ13 | Resolved. Commands and the entry point are in 13.1, and the install gate is 10.6 item 3. Small leftover: nothing says how models reach the Ames models directory for the mac-tier runs of S2a, S2b and B before S7a exists. One line closes it, for example "until S7a, download with `HF_HOME` set to the models directory". |
| MJ14 | Resolved. Slice W wires the pipeline (L459). |
| MJ15 | Resolved. The confidence field and the 0.7 threshold are removed (L224). |
| MJ16 | Resolved. S1 pins Python 3.12, Node 22, Pipecat, mlx-lm and Vite. Small leftover (judgment call): B runs on unpinned candidates, so `llm-bakeoff.md` should record each candidate's revision. The judge is pinned in both #3 (L335) and L2 (L451). Pick one place. |
| MJ17 | Resolved. A session is one WebRTC connection, and a new offer replaces the old session (L140, L400-401). |
| MJ18 | Resolved. roadmap.md has a v1.x section (roadmap.md L5-7). |
| MJ19 | Resolved. S3a depends only on S1 (L448). |

---

## Part 2: New blockers

### NB10. Other local accounts can fetch the session token - Severity: Blocker (security, and a contradiction within 9.1)
* **Reviewer Lens:** `implementation-readiness`, `design` (found independently by both)
* **Affected Scope:** 9.1 L300 and L305; 5.4 L123 and L127
* **Discrepancy Description:** L300 puts "other user accounts on the same Mac" in scope. L305 has `GET /` embed the token, and `GET /` needs only a valid `Host` header. Another local account can send a request without an `Origin` header (for example with curl) and the `Host` set to `127.0.0.1:8765`. That returns the token, and the token lets them read every fact through `GET /api/memory`, wipe memory through `DELETE`, or take over the session with a new offer. The 0700 data directory protects the file but not the API in front of it. An agent building S1b cannot write security tests that satisfy both L300 and L305.
* **Rationale & Recommendation:** Pick one:
  * (a) Stop serving the token from `GET /`. `ames` opens Chrome at `http://127.0.0.1:8765/#t=<token>`. The browser never sends the fragment to the server, so `GET /` serves only the static client, and the client keeps the token in memory or sessionStorage. Whether other macOS users can read the process arguments is unverified, so check it in S1b.
  * (b) Narrow L300 to say that other local accounts are protected only at the file level, and accept that risk in section 14.

  Either option is a small change and makes S1b's tests well defined.

### NB11. The LLM candidate list is not in the doc - Severity: Blocker only if issue #12 does not list it (criterion a)
* **Reviewer Lens:** `design`
* **Affected Scope:** L45, L335, L444, L450
* **Discrepancy Description:** B must "run every candidate", and E's judge must be "of a different family than every LLM candidate". The doc names no candidates and no rule for choosing them, and refers only to issue #12 (L45). This review did not check whether #12 lists them.
* **Rationale & Recommendation:** If #12 lists the candidates, this is not a blocker. Otherwise, add the list, or the rule for choosing it, to B's row or to section 10, so that E can choose a judge that satisfies L335.

---

## Judgment calls (not blocking)

* **J1:** B's acceptance names only the persona gate (L450). The risk table (L470) implies that the bake-off also screens for crisis handling, so a model that fails crisis would only show up at S6. The 10.2 crisis-flag and guardrail gates are text-only and need only P and S3a, so they could be added to B. Release is still safe either way, because 10.6 blocks it.
* **J2:** Only user turns are extracted (L220), so her turns (role `ames`) never get `extracted_at` and, read literally, L235 keeps them forever. This conflicts with the 9.2 retention row. A one-line fix: delete her turns at the end of a session once all of that session's user turns have been extracted.
* **J3:** 8.4 does not say what happens when the user's mapped country has no key in `resources.json`. Falling back to `international` is the obvious choice.

## Not verified

* Upstream behaviour: whether the Pipecat client can send custom headers, mlx-lm prompt-cache handling, Orpheus tag support, and whether macOS lets one user read another user's process arguments. The doc defers these to S1b and S2b.
* The contents of issue #12, which decide NB11.
