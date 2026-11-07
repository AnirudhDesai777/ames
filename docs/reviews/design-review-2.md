# Goldfish Review Report 2

**Target:** `docs/design.md` version 2 (lines 1-416) and `docs/roadmap.md` (lines 1-29)
**Date:** 2026-11-07
**Mode:** REVIEW (greenfield design, no code)
**Prior review:** `docs/reviews/design-review-1.md` (B1-B9, M1-M13)
**Fixed owner decisions (not reviewed):** Mac only, MLX, LLM chosen by bake-off, no age gate (this replaces the 18+ attestation suggested under M11), exactly five guardrails, Chrome only. The section 10 thresholds are treated as proposals. They are only flagged where they cannot be measured or contradict something else.

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `design` | completed |
| `zoom-out` | completed |
| `implementation-readiness` | completed |
| `comprehension` | completed |
| `structural` | completed |
| `style` | not run (skipped by request) |
| `codebase-details` | not run (skipped by request: no code exists) |

**Prior items:** 6 resolved, 16 partly resolved, 0 not resolved.
**New in version 2:** 9 blockers, 19 majors, 7 nits.

**Overall verdict:** version 2 is a large improvement. Every prior finding was addressed at least in part. The doc now has goals and non-goals, an interface section, full DDL, a control token, a security section, a retention table, network policy, numeric eval gates, failure modes, alternatives, risks and a dependency column. It is still not ready for slice-by-slice autonomous building. The remaining blockers cluster in four areas:
- the build-plan ordering in section 14
- how crisis mode actually works on the server
- a second unprotected local listener (`mlx_lm.server`)
- extraction bookkeeping that makes the deletion guarantee impossible

Most fixes are small and are decisions, not redesign.

Several reviewers recalled upstream behaviour from memory and did not verify it, and two findings depend on it:
- `mlx_lm.server` default port, CORS headers, per-request model loading, single prompt cache and handling of client disconnects (NB3, MJ3)
- Pipecat SmallWebRTC STUN defaults and whether the client SDK can send a custom header (MJ12, MJ9)
- Orpheus word timestamps
- whether MLX wheels install on non-Mac CI (NB8)

Check these before acting on the findings that depend on them.

---

## Part 1: Verdicts on the first review

| Item | Verdict | What is still missing |
| --- | --- | --- |
| B1 Machine-checkable criteria | Partly resolved | No v1 exit bar. The roadmap (L3) ends v1 at "issue #10 closed", but the design never says which gates close it. Goal 1, one-command install (L18), has no gate: no scripted install into an empty cache ending with `/api/health` ready. "Refusal rate at most 2%" (L313) does not define a refusal, does not name a judge for it, and uses a prompt set not built to probe refusals. The crisis set (L317) has no dev/held-out split, although L303 requires one for every set, so it is unclear whether recall is judged on 20 positives or about 10. G4's "sincerely asked" (L319) has no pass rule. Agent-tier cells such as "Repository tests pass" and "Component tests pass" (L391-394) name no test cases. S3d and S6 (L390, L396) have no agent-tier test at all. |
| B2 Who runs which check | Partly resolved | The two tiers are defined (L374-377), but L374 says every row is built by Codex. S2b, B, S3d and S6 (L383, L385, L390, L396) can only be completed on the reference Mac, and their "agent-tier" cells hold maintainer gates. Mark them maintainer-run, or give Codex only the scripts. Per-slice target files, public signatures and a single test command are still absent. |
| B3 Eval slice and ordering | Partly resolved | Slice E, the depends-on column and the latency fixtures (L329-330) are in. Still missing: the JSONL record schema for each eval set, the owner of the datasets and of the 30 recorded WAV turns, and the owner of `persona/ames.md` (L241). "M0" (L303) is undefined. The dependency errors are NB1 and NB5. |
| B4 Crisis flag vs streaming | Partly resolved | The leading control token, inclusion criteria, stickiness and exclusion from extraction are in (L224-231, L268-271). How the server carries out crisis mode is not defined. That is new blocker NB2. |
| B5 RAM and GPU on 16 GB | Partly resolved | The reference machine, the conditional minimum, idle-only cancellable extraction, the concurrent-load gate and the Orpheus fallback are in. The RAM budget was removed rather than fixed. The "16 GB profile" (L344) is undefined (NB7). History and context length are uncapped (MJ4). The optional larger extraction model (L213) has no budget (MJ5). The Orpheus self-test runs on an idle GPU at startup (L345), not under the load it will actually face. |
| B6 LLM runtime | Resolved | `mlx_lm.server` as a child process (L76). Side effects are NB3 and MJ3. |
| B7 Server-to-UI contract | Partly resolved | Events and endpoints exist (L110-129). Missing: the token header name (L280), the response shape of `GET /api/memory` (L127), the `models` shape in health (L126), any client-to-server field for the locale (L269), where the advisory-seen flag is stored (G5, L264), what the UI does on `crisis: true`, and events for the section 11 user messages ("connection lost", profile loaded, expressive voice off). L120 contradicts L280 (NB9). |
| B8 Memory schema and forget | Partly resolved | The DDL, value column, User entity, alias table, cardinality and schema_version are in, and voice "forget that" is deferred. Missing: the contents of `extraction.schema.json` (L212), how alias rows are created, the "functional" vs "functional per subject" distinction (L188-190), and `PRAGMA foreign_keys=ON`. Without that pragma the ON DELETE CASCADE clauses do nothing. There is no extraction watermark (NB4). |
| B9 Localhost threat model | Partly resolved | Port 8765 is well covered (L277-280). The LLM subprocess listener is not (NB3). There is no statement of which attackers are in scope. The file is 0600, but the data directory, the log directory and the SQLite WAL and shm files are not covered (MJ10). |
| M1 Slice size | Partly resolved | Slices are split. Still missing: an owner for the HTTP app, the security middleware and the RTVI emitters (NB5), and an owner for pipeline wiring: writing `turn` rows, calling retrieval into the late memory slot, and triggering extraction on idle (MJ14). S3c still bundles barge-in with TTS. S7b adds the security checks only at the very end. |
| M2 Latency budget | Partly resolved | Prefill and retrieval rows, the late memory block, thinking off, and p50/p90 targets are in. The "Server to playback 70 ms" row (L89) lies outside the latency definition (L80) and outside the measured interval (L330), so the 1200 ms total contains an unmeasured stage. History length is uncapped (MJ4). Extraction may evict the warm cache (MJ3). |
| M3 Barge-in | Resolved | One ambiguity (nit N1): whether a sentence cut mid-way is kept under sentence alignment (L105). Sentence alignment is probably the main path, because Orpheus timestamps are unverified. |
| M4 Pipecat claim | Resolved | Whisper is built in, Parakeet is rejected, and Orpheus is benchmarked in S2b. The S2b-before-S3c ordering problem is part of NB5. |
| M5 False memories | Partly resolved | User-only extraction, prompt rules and a precision gate are in. The 0.7 confidence threshold (L203) applies to a number whose source is never stated, presumably uncalibrated self-report. Define it or drop it. |
| M6 Over-scoped v1 | Resolved | Mood comes from the control token, the UI is orb-only, Settings are deferred, the browser is Chrome only, and download progress is in the terminal. |
| M7 Missing sections | Partly resolved | All sections exist. The failure table lacks rows for: the `mlx_lm.server` child crashing or hanging, a port already in use, and a second tab or session. The 16 GB profile row is unbuildable (NB7). A malformed token fails open even during sticky crisis mode (part of NB2). The alternatives table omits crisis-by-token vs a separate classifier, and in-process vs subprocess LLM. |
| M8 Tags and sanitizer | Resolved | Small gap: the tag list is "fixed in S3a after checking Orpheus" (L232), but S3a does not depend on anything that runs Orpheus. State whether S3a edits design.md or opens an issue (L131). |
| M9 Privacy and retention | Partly resolved | "Dropped and logged" (L212) and "logs it" (L231) could write raw model output containing user content, which contradicts "no transcript text" in logs (L289). Crash reports are not addressed. "Hard delete" (L128-129) needs `secure_delete` or VACUUM plus a WAL checkpoint. A per-fact delete leaves the source turn text (MJ8). |
| M10 Network policy | Partly resolved | The WebRTC `iceServers` list is not pinned to empty, so Chrome may contact a public STUN server, and the blocked-network test (L299) could still pass. The `mlx_lm.server` child and Pipecat's Whisper must read models from the Ames models directory and inherit offline settings, for example `HF_HOME` and `HF_HUB_OFFLINE` set for the child. |
| M11 Open questions | Resolved | Q1 is closed, slice L checks licenses, and the age gate is settled by the owner. Deferred decisions (judge in #3, tag list in S3a, extraction model in S5b) could be listed in section 15. |
| M12 Pinning | Partly resolved | Model pins arrive only in slice L, after B (L386), so S1, S2a, S2b and B run on unpinned models. The judge model is not in L. Python, Pipecat, mlx-lm and Vite versions are pinned nowhere. L also waits on B for no reason (MJ6). |
| M13 Later work mixed in | Partly resolved | The move to roadmap.md, the new title, "no feedback UI" (L30) and the held-out persona prompts are done. The distillation fallback now contradicts the roadmap (NB6). The design defers Settings, Firefox and Safari, and voice forget to "v1.x" (L31, L32, L220), but roadmap.md has no v1.x section. |

---

## Part 2: New blockers in version 2

### NB1. The bake-off cannot run the gates it decides by - Severity: Blocker
* **Reviewer Lens:** `design`, `zoom-out`, `implementation-readiness`, `comprehension`
* **Affected Scope:** L241, L313, L318, L385, L387-390
* **Discrepancy Description:** B depends only on E and S2a, but its gates need three things that come later or have no owner:
  * The persona system prompt with the control-token instruction. That is `persona/ames.md` with prompt assembly in S3b, and no slice authors the file.
  * The control-token parser for the "malformed control tokens zero" gate. That is S3a.
  * The advice and spoken-length rules, which the persona eval scores against the S3b prompt.

  Codex would bake off models without the prompt they will be judged under, or invent a persona. S3d re-runs the persona gate on the chosen model but does not depend on B, and it depends on S3c (TTS) although the eval is text-only.
* **Rationale & Recommendation:** Add a maintainer-owned slice that writes `persona/ames.md`, including the crisis instructions, before B. Make B depend on S3a, S3b and that slice. Make S3d depend on B and not on S3c, or fold S3d into B.

### NB2. Crisis-mode server behaviour is undefined - Severity: Blocker (safety)
* **Reviewer Lens:** all five
* **Affected Scope:** L110-129, L115, L231, L269-270, L346, L396
* **Discrepancy Description:** The doc describes her behaviour in crisis mode but not the mechanism that produces it:
  * **Resources:** the flag arrives in the first token of the reply. Unless the server puts `resources.json` into the prompt, the model never sees the resources. Nothing says whether resources are spoken by the model, injected into the prompt, or shown in the UI.
  * **Stickiness:** when the model emits `crisis=0` but the server keeps crisis mode on, nothing says what changes her behaviour. A crisis instruction in the late prompt block is the natural answer, but it is not stated.
  * **Locale:** no message or endpoint in 5.4 carries `navigator.language` to the server. Values like "en" often have no region.
  * **Data:** `resources.json` has no format and no source of verified hotline data. Codex must not invent phone numbers.
  * **Fail-open during crisis:** a malformed token defaults to `crisis=0` (L231, L346). That then counts towards the three-turn exit (L270) and lets the sass back in mid-crisis.
  * **Dependencies:** S6 depends only on S3a, but its gate needs S3b and B.
* **Rationale & Recommendation:** Pick one delivery path and write it down. A simple version:
  * The client sends its locale once, as a field on `/api/offer`.
  * While crisis mode is on, the server adds a fixed crisis instruction plus the locale's resources to the late prompt block.
  * The UI shows a resource card whenever `state.crisis` is true, so resources never depend on the model.
  * A malformed token keeps the current crisis state and does not count towards de-escalation.
  * Define the `resources.json` schema and have the maintainer supply its contents.
  * State which `turn` rows get `crisis=1`.
  * Give S6 an agent-tier test for the sticky logic and the resource lookup, and make it depend on S3b and B.

### NB3. The LLM subprocess is an unprotected second local listener - Severity: Blocker (security)
* **Reviewer Lens:** `design`, `structural`, `implementation-readiness`
* **Affected Scope:** L65, L76, L277-280, L297, L370, L398
* **Discrepancy Description:** Section 9.1 hardens port 8765 only. `mlx_lm.server` is a second HTTP listener with no stated port, bind address, Host or Origin check, or token. Reviewers recall, without having verified it, that it uses a fixed default port, sends `Access-Control-Allow-Origin: *`, and accepts model or adapter fields in the request. If that is right, any web page can drive the GPU during a session and might make the server load other model paths. The offline promise also depends on the child inheriting `HF_HUB_OFFLINE`.
* **Rationale & Recommendation:** Verify the upstream behaviour first. Then choose one of two options:
  * bind the child to a random free port on 127.0.0.1, start it with an explicit offline environment, block cross-origin access, and add it to the Host/Origin tests
  * run mlx-lm in-process and drop the subprocess

  Add the choice to 9.1 and to the risk at L370.

### NB4. Extraction has no watermark, so deleted facts can come back - Severity: Blocker (contradiction with the 10.3 gate)
* **Reviewer Lens:** `design`, `comprehension` (blocker); `zoom-out`, `implementation-readiness`, `structural` (major)
* **Affected Scope:** L146-153, L208-219, L325
* **Discrepancy Description:** User speech (L208) and any delete (L219) cancel extraction, so extraction must resume later. Nothing records which turns are done. A per-fact delete leaves the source turn text in `turn` (L218). So a resumed or re-run extraction can write the deleted fact back, which breaks "a deleted fact never reappears" (L325). That gate also does not say where "reappears" is checked: the panel, the store, or what she says. L96 does not say whether conversation history comes from stored turns across sessions.
* **Rationale & Recommendation:**
  * Add `turn.extracted_at`, set when a turn is processed, skipped or dropped.
  * Extract each turn at most once.
  * Check a delete counter inside the write transaction.
  * State that the history window is the current session only, or say how deleted facts are scrubbed from it.
  * Define "reappears" as the store, the panel and the next prompt.

### NB5. Section 14 dependencies are wrong and the server core has no owner - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness` (primary), `design`, `comprehension`, `structural`, `zoom-out`
* **Affected Scope:** L383, L391, L395-398
* **Discrepancy Description:**
  * **S2b** depends only on S2a, but it needs three later things: an Orpheus runner (built in S3c, which waits on L and B), forced extraction for the concurrent-load scenario (S5b, see 10.5), and the chosen LLM (B).
  * **S5d's** delete-during-extraction test needs S5b, which S5d does not list.
  * **No slice builds the HTTP app:** `GET /` with the token, `/api/health`, the token middleware, the Host and Origin checks, and emitters for the custom RTVI `transcript`, `speaking` and `state` events. S4 is built against events nobody sends. The memory delete endpoints run unprotected until S7b retrofits security.
* **Rationale & Recommendation:**
  * Split S2b into a standalone Orpheus real-time-factor script (after S2a) and a concurrent-load plus 16 GB run (after B, S3c and S5b).
  * Add S5b to S5d's dependencies.
  * Add an S1b slice covering the HTTP app, token, Host and Origin middleware, health, and RTVI emitters with stub values, with its own tests. Make S4 and S5d depend on it. S7b then only verifies.

### NB6. Distillation is both the v1 fallback and post-v1 work - Severity: Blocker (contradiction)
* **Reviewer Lens:** `design`, `comprehension`, `structural`, `zoom-out`
* **Affected Scope:** design L30, L44, L367; roadmap L3, L15-17
* **Discrepancy Description:** Section 3 and the risk table make distillation the v1 path if no model passes. L30 defers fine-tuning until "after the bake-off". The roadmap says nothing in it is built before v1 ships, and it lists distillation there. Section 14 has no distillation slice. If B fails, the build plan has no next step.
* **Rationale & Recommendation:** State in all three places either that distillation is a conditional v1 slice after B, or that a failed bake-off blocks v1 until #7 lands. This settles only where the work is planned. It does not reopen the owner decision.

### NB7. The "16 GB profile" is referenced but never defined - Severity: Blocker (contradiction)
* **Reviewer Lens:** all five
* **Affected Scope:** L53, L344
* **Discrepancy Description:** L344's out-of-memory behaviour is "try the 16 GB profile, then stop". No section defines that profile (which LLM, Kokoro only, a context cap) and no slice builds it. L53 says a failing 16 GB test raises the minimum to 24 GB, which implies a single profile.
* **Rationale & Recommendation:** The simplest fix is to delete "try the 16 GB profile": on a load error, stop with a message naming the RAM requirement. Add a profile only if S2b shows one is needed, and then define its models and context cap.

### NB8. CI platform and MLX dependency isolation are undefined - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** L305, L376, L381-382
* **Discrepancy Description:** Every agent-tier gate runs in CI with stubs, but the doc does not name the CI OS. It also does not say how MLX packages (believed to be macOS-arm64 only, not verified) stay out of a non-Mac install, or how pipeline code imports without them. On a Linux sandbox a plain `uv sync` or an import of the Whisper MLX service may fail. If so, no slice can pass its merge gate.
* **Rationale & Recommendation:**
  * Name the CI runner.
  * Put the MLX packages behind an extra with a `sys_platform == 'darwin' and platform_machine == 'arm64'` marker.
  * Require each service to have a stub, selected by a named environment variable or config flag.

### NB9. `GET /` cannot require the token it hands out - Severity: Blocker (one-line contradiction in the security spec)
* **Reviewer Lens:** all five
* **Affected Scope:** L120, L124, L280
* **Discrepancy Description:** L120 says every endpoint requires the session token. L280 says `GET /` embeds it and only the other endpoints require it. If L120 is implemented literally, the page cannot load. The header name is also not given.
* **Rationale & Recommendation:** Exempt `GET /` from the token check, keep the Host check on it, and name the header. Confirm that the Pipecat client SDK can send that header on `/api/offer`.

---

## Part 3: New majors

| # | Lens | Lines | Issue and fix |
| --- | --- | --- | --- |
| MJ1 | design, comprehension, structural | 80, 89, 330 | The 70 ms "Server to playback" row lies outside the latency definition and the measured interval. Drop the row or extend the definition. Also state that latency starts after Smart Turn fires and so excludes the end-of-speech wait. |
| MJ2 | design, comprehension, structural | 303, 307, 313, 317, 319 | No v1 exit bar. Refusal is undefined. The crisis set has no split. "M0" is undefined. "Confirmed when #3 merges" (issues do not merge). Define the release gates and the JSONL schemas. |
| MJ3 | design, zoom-out, implementation-readiness | 86, 208, 213 | Extraction on the same `mlx_lm.server` may evict the conversation prompt cache, if it keeps one entry (unverified), and break the 150 ms warm-prefill row. Re-warm the cache after extraction, or run extraction once per idle window, or budget a cold prefill. State how cancellation stops generation on the GPU. |
| MJ4 | design, comprehension | 86, 96 | No history window or context cap. Prefill and the KV cache grow without limit within a session. |
| MJ5 | all | 213, 386, 397 | The optional larger extraction model is not in the memory budget, the pinned table (L), the downloads (S7a), the serving design or the load test. If the precision gate fails, prefer a higher confidence threshold or fewer predicates first. |
| MJ6 | zoom-out | 386, 389 | Slice L waits on B, but the Orpheus license check has nothing to do with which LLM wins. Split the TTS license check out so S3c is not blocked. |
| MJ7 | zoom-out | 21, 179, 198, 203, 217 | Closed functional facts stay on disk, invisible in the panel and deletable only by "Delete everything", which undercuts Goal 4. Nothing in v1 reads them. Replace the row instead of closing it, or show closed facts. |
| MJ8 | design, structural, zoom-out | 218, 287 | A per-fact delete leaves the source turn text, the entity and its aliases. Transcripts are kept indefinitely with no stated v1 use after extraction. Say that only "Delete everything" fully erases, or redact the source turn and clean up orphans. State why transcripts are kept, or shorten their retention. |
| MJ9 | design, structural, zoom-out, comprehension | 110-129, 264, 342-348 | Interface gaps: the `GET /api/memory` response shape, the `models` shape in health, where the advisory-seen flag is stored, events for the section 11 user messages, and whether mute and end are client-only actions. |
| MJ10 | design, structural | 141, 212, 231, 289, 292 | File and log hygiene. Require the data directory to be 0700 and set a umask so the WAL and shm files are covered. Require `PRAGMA foreign_keys=ON` and `secure_delete`. Never log raw model output or failed extraction JSON. |
| MJ11 | design, structural | 340-349 | Missing failure rows: the LLM child crashes or hangs, a port is in use, a second tab or WebRTC session opens. The Orpheus self-test runs only on an idle GPU. Hashing every model with SHA-256 at each startup (L343) reads many GB; hash at setup and check size and mtime at startup. |
| MJ12 | design, structural, comprehension | 296-299 | Set `iceServers` to an empty list on client and server. Point `HF_HOME` (or local paths) at the Ames models directory for Pipecat and the LLM child, so `HF_HUB_OFFLINE` holds. |
| MJ13 | implementation-readiness, comprehension, structural | 18, 290, 296, 398 | The install and run flow is unnamed: the install command, `ames setup`, the start command, and the `ames` entry point next to the `ames-voice` package. Model location before S7a exists (HF cache or the Ames directory) is undefined. Goal 1 has no acceptance test. |
| MJ14 | implementation-readiness | 96, 146, 203, 208 | No slice wires the pipeline: writing spoken-only `turn` rows, calling retrieval into the late memory slot, and triggering extraction from the VAD/TTS idle signal. |
| MJ15 | design | 203 | The source of `confidence` is unstated. Define it or drop the 0.7 threshold and rely on the precision gate. |
| MJ16 | implementation-readiness, comprehension, structural | 381-386 | Python, Pipecat, mlx-lm and Vite versions are not pinned. S1, S2a, S2b and B run before model pins exist. The judge model is not in L. |
| MJ17 | design | 148, 348 | The session model is undefined: what `session_id` is, and what happens on reconnect or with two tabs sharing one LLM and one cache. |
| MJ18 | structural | design L31, L32, L220; roadmap | roadmap.md has no v1.x section for the items the design sends there. |
| MJ19 | implementation-readiness | 387 | S3a, a pure-function slice, depends on E for no reason. S3a, S4 and S6 logic could start right after S1. |

---

## Nits

| # | Lines | Lens | Issue and fix |
| --- | --- | --- | --- |
| N1 | 105 | design, zoom-out | Under sentence alignment, say whether a half-spoken sentence is kept. Word alignment relies on TTS timestamps that are not confirmed. |
| N2 | 190 | zoom-out, comprehension | "Functional per subject" vs "functional": use one term. |
| N3 | 311 | comprehension | "Three roles" refers back to L10, where they are not called roles. |
| N4 | 303, 383 | comprehension | `evals/` holds datasets and `docs/evals/` holds results. State the split once. |
| N5 | 38 | structural, comprehension | The decision log is in Notion, which Codex cannot reach, and the issue numbers are not linked. |
| N6 | 8-12 | structural | There is no explicit problem statement. The summary describes the product. |
| N7 | 290 | structural | The retention table relies on an uninstall command that is never defined. |

---

## Suggested order of fixes

1. One-line fixes: NB9 (`GET /` exemption and header name), NB7 (delete the 16 GB profile row), NB6 (state where distillation lives), and the malformed-token-keeps-state rule from NB2.
2. Decisions the maintainer must make: the crisis delivery path, the locale field and the `resources.json` source (NB2); in-process vs a locked-down subprocess (NB3); the CI runner (NB8).
3. Section 14 edits: the persona slice before B with B depending on S3a and S3b (NB1); the S2b split, S5d depending on S5b, and the S1b server-core slice (NB5); the `turn.extracted_at` column (NB4).
