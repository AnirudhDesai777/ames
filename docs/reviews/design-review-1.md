# Goldfish Review Report

**Target:** `docs/design.md` (lines 1-176)
**Date:** 2026-11-07
**Mode:** REVIEW (greenfield design, no code)
**Fixed owner decisions (not reviewed):** local-only inference; memory is a long-term user knowledge graph; uncensored but crisis-aware; public product named Ames.

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `design` | completed |
| `zoom-out` | completed |
| `implementation-readiness` | completed |
| `comprehension` | completed |
| `structural` | completed |
| `style` | completed |
| `codebase-details` | not run (skipped by request: no code exists) |

**Severity breakdown after deduplication:** 9 Blocker, 13 Major, 12 Nit.

**Overall verdict:** the product concept and cascade architecture are sound. As a build plan for an autonomous agent, the doc is not ready. Every reviewer independently flagged the same core gaps: the acceptance criteria cannot be checked, the crisis and streaming designs conflict, the LLM runtime is undecided, the RAM and GPU budget is optimistic for 16 GB machines, and there is no written server-to-UI contract.

Reviewers wrote some upstream facts from memory and did not verify them: Pipecat's built-in service list, the Orpheus tag set, the latest Smart Turn version, model licenses, and whether the `ames` name is free on PyPI. The bandwidth, KV-cache and prefill figures are first-principles estimates, not measurements.

---

## Prioritized Findings (Blockers -> Major -> Nits)

### B1. Slice and exit acceptance criteria cannot be checked by a machine - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`, `style`, `zoom-out`, `design`, `structural`, `comprehension` (all six)
* **Affected Scope:** Lines 33, 139-144, 160-168
* **Discrepancy Description:** The criteria below have no threshold or cannot be measured.
  * "Persona eval pass" (L33) and "Memory eval passes" (L166) have no threshold.
  * S3 only records a baseline, so nothing has to pass.
  * "Laughs/sighs audible" (L164) needs human judgement.
  * The "Manual QA checklist" (L165) does not exist.
  * "Fresh Mac" (L168) cannot be reproduced by an agent.
  * S1 "echoes back" (L162) is checked by ear.
  * S6 gates on recall only (L167). A flag that always fires passes, even though L143 says false positives are measured.
  * With about 15 positive prompts, recall of at least 0.9 allows one miss.
  * The judge for "not cringe" (L142) is an unnamed "local LLM". It may be Qwen3-8B grading itself.
  * The v1 exit criteria leave out the crisis eval, UI and packaging.
* **Rationale & Recommendation:** Give every eval in section 9 an exact prompt count, a named judge model that is not the model under test, a per-item scale and a numeric pass rule. Have sections 3 and 12 cite those rules.
  * Add a crisis false-positive ceiling and multi-turn and indirect crisis cases.
  * Add a refusal-rate metric, since "no refusals" with stock Qwen3 is unproven.
  * Measure memory precision as well as recall.
  * Replace subjective checks with concrete ones:
    * S1: a WAV fixture through the pipeline, asserting non-silent output.
    * S3: each tag reaches the Orpheus input and produces non-silent audio.
    * S7: a scripted install into an empty model cache, asserting `/health` reports ready.
  * Make the v1 exit bar four numbers: latency, persona score, memory recall and precision, and crisis recall and false-positive rate.

### B2. Codex probably cannot run most acceptance checks in its environment - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`
* **Affected Scope:** Section 12 (L156-168)
* **Discrepancy Description:** MLX, the Apple GPU, a microphone, WebRTC and Safari all need the owner's Apple Silicon machine. A Codex sandbox usually has none of them. The doc does not say who runs which check.
* **Rationale & Recommendation:** Split every acceptance criterion into two tiers:
  * (a) agent-runnable: unit and integration tests with stub STT, LLM and TTS services and WAV fixtures. This tier gates the merge.
  * (b) owner-run on the reference Mac: latency, audible output, browser QA.

  Also give each slice its target files, public signatures, named test cases and one command that must pass. Define the repo layout once.

### B3. Eval harnesses and datasets are needed but not scheduled, and slice ordering is broken - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`, `design`
* **Affected Scope:** Lines 139-144, 160-168
* **Discrepancy Description:** Only the latency harness has a slice (S2). No slice owns the following:
  * the persona, crisis and memory harnesses
  * the prompt datasets and who writes them
  * the storage format, e.g. `evals/*/*.jsonl`
  * the rubric

  Ordering problems:
  * S3 needs the persona eval.
  * S4 onboarding needs S7's download progress.
  * The S5 Memory panel needs the S4 shell and a UI contract.
  * S6 needs S3's output format fixed.
  * The latency harness fixtures are undefined: source, format, injection point, and the timestamp that marks each stage boundary.
* **Rationale & Recommendation:** Add an "Eval framework and datasets" slice before S3 with fixed schemas. Add a "depends on" column to the slice table, or an explicit order such as: S1 -> S2 -> Eval -> S3 -> S4a -> S7a (download) -> S4b -> S5 -> S6 -> S7b. Specify the latency fixtures: 16 kHz mono WAV, a manifest, and named Pipecat observer hooks for each stage.

### B4. The crisis flag mechanism conflicts with streaming TTS, and "crisis" is not defined - Severity: Blocker
* **Reviewer Lens:** `design`, `implementation-readiness`, `zoom-out`, `comprehension`, `style`
* **Affected Scope:** Lines 81, 124-126, 143, 167
* **Discrepancy Description:** TTS starts on the first sentence, but the crisis flag is "a structured side output" of the same streamed generation. That flag either arrives after a sassy sentence has already played or needs a second call. The doc does not say:
  * how one stream carries both speech and structured output
  * what the server does when the flag is set
  * whether the flag persists across turns
  * where the locale comes from
  * what format the resources file has

  "Real self-harm or suicide signals" (L124) cannot be measured, so the S6 eval has no ground truth for its labels.
* **Rationale & Recommendation:** Require a leading control token as the first span of every reply, for example `<state crisis=0|1 mood=...>`. The server parses and strips it before any text reaches TTS. This reuses the S3 tag sanitizer and also settles mood (see M8).
  * Make crisis mode sticky, with an explicit de-escalation rule.
  * Write inclusion criteria, for example "explicit or implied intent, plan or means for self-harm or suicide, or self-harm in progress". Label the eval set against them.
  * Ship resources as one static JSON keyed by country, taken from `navigator.language`, with an international fallback.
  * Decide whether crisis turns go into memory extraction and DPO data. Default: they do not.
  * Fix this format before S3 so that S6 does not have to rework S3's streaming.

### B5. The RAM and GPU budget does not hold on the 16 GB minimum, and concurrent load is not budgeted - Severity: Blocker
* **Reviewer Lens:** `design`, `zoom-out`, `comprehension`, `structural`, `style`
* **Affected Scope:** Lines 24, 33, 50, 58-66, 81, 102
* **Discrepancy Description:** The table is titled "24 GB machine", but the minimum is 16 GB. The roughly 11 GB counts model weights only. It leaves out:
  * the Qwen and Orpheus KV caches (about 1.2 GB at 8k context for Qwen)
  * Python, torch and Pipecat runtime overhead, possibly across two inference runtimes
  * the browser and macOS

  macOS limits GPU-wired memory to roughly 65-75% of RAM, about 10.5-12 GB on a 16 GB machine. The machine will swap.

  Three autoregressive workloads also share one GPU:
  * Orpheus, which needs about 85 SNAC tokens per second for real-time speech
  * Qwen, still streaming while she speaks (L81)
  * the async extraction call that runs after each turn (L102)

  On base M1-M3 memory bandwidth, Orpheus will likely fall below real-time. Extraction for turn N will run into turn N+1's first-token budget.
* **Rationale & Recommendation:** Do one of two things: publish a budget per tier (16 and 24 GB) with KV cache at a stated context cap, overhead and a browser allowance, plus a 16 GB profile (for example a 4B LLM and Kokoro); or raise the minimum to 24 GB.
  * Run extraction only when idle (no VAD activity, TTS finished), make it pre-emptible, and cancel it on user speech.
  * Add a concurrent-load scenario to the latency harness.
  * Set a minimum Orpheus real-time factor, with automatic fallback to Kokoro.
  * Name one reference machine with its RAM, and smoke-test on the slowest supported Mac in S2.

### B6. The LLM runtime is undecided (MLX or Ollama) - Severity: Blocker
* **Reviewer Lens:** all six
* **Affected Scope:** Lines 48, 64, 148, 163, 168
* **Discrepancy Description:** L48 says "MLX/Ollama", S2 says Ollama, and fine-tuning uses `mlx-lm` LoRA (L148), which Ollama cannot load without conversion. "Q4" is GGUF naming. Ollama is a separate app, so S7's "one command on a fresh Mac" cannot pass with it. The runtime also decides prompt caching (see M2).
* **Rationale & Recommendation:** Pick one runtime now. The reviewers lean towards in-process `mlx-lm`: one `uv` install, one family with the MLX STT, LoRA served natively, and prompt caching. Use its exact quantization label everywhere. If Ollama stays, rewrite S7's acceptance to "install Ollama, then one command".

### B7. The server-to-UI contract is undefined - Severity: Blocker
* **Reviewer Lens:** `implementation-readiness`, `comprehension`, `structural`, `zoom-out`
* **Affected Scope:** Lines 53, 86, 104, 130-132, 153
* **Discrepancy Description:** No message schema exists for any of the following:
  * transcripts, partial and final
  * speaking state
  * mood
  * crisis state
  * download progress
  * Memory create, read, update and delete
  * Settings

  L153 requires the v2 "UI contract" to stay identical, but the contract does not exist yet. S3 (server events) and S4 (UI) would be built separately against nothing.
* **Rationale & Recommendation:** Add an "Interfaces" subsection under section 4 with typed messages, for example `{type:"transcript", role, text, final, turn_id}`. Put streaming state on the data channel and Memory, download and Settings on HTTP endpoints. Build on Pipecat's RTVI client SDK and events (pipecat-client-js with the SmallWebRTC transport; package names unverified) rather than a hand-rolled protocol. Run S1-S3 against Pipecat's prebuilt test UI.

### B8. The memory schema cannot store the headline example, and "forget that" is undefined - Severity: Blocker
* **Reviewer Lens:** `design`, `implementation-readiness`, `comprehension`, `style`, `structural`, `zoom-out`
* **Affected Scope:** Lines 90-104
* **Discrepancy Description:** Schema gaps:
  * "Midterm is Thursday" needs a date, but `object_id` can only point to an entity. There is no literal or value column and no rule for normalising relative dates.
  * There is no reserved User entity, no alias table and no entity resolution ("Mom" vs "my mom" vs "Priya").
  * `source_turn_id` references a turn table that does not exist, so it is unclear whether transcripts are stored.
  * "Contradicting" has no rule for cardinality: `lives_in` is single-valued, while `likes`, `knows` and `studies` are multi-valued. There is no negation or "end" operation either.
  * `confidence` is never used.
  * Mention matching for retrieval is unspecified, and so are "recent" and the tokenizer for the 400-token budget.
  * `Feeling` and `Preference` as entity types overlap with the predicates `feels_about`, `likes` and `dislikes`.
  * `relation` has no primary key.

  "Forget that" (L104):
  * The referent of "that" is undefined.
  * Closing a fact with `valid_to` keeps history, which contradicts "delete".
  * In-flight extraction can re-write a fact after it was deleted.
  * The forgotten content stays in the live context and in DPO pairs.
* **Rationale & Recommendation:** Write the full DDL and the cardinality rule:
  * Add a literal or value column, a reserved User entity, an alias table, and a unique key on (type, normalized_name).
  * Mark each predicate as functional or multi-valued, and supersede facts only for functional predicates (`lives_in`, `scheduled_on`).
  * Add an extraction JSON schema and a schema_version table.

  Make "forget" a hard delete across relations, entities, turns and preference pairs. It should also cancel in-flight extraction, remove the content from the live context and confirm aloud what was forgotten. Zoom-out suggests deferring voice "forget that" to v1.x and shipping only the panel's delete button. Either way, S5 needs a dedicated test for these cases.

### B9. The localhost server has no threat model, and any website can reach the memory endpoints - Severity: Blocker
* **Reviewer Lens:** `structural`, `design`
* **Affected Scope:** Lines 25, 92, 136-137
* **Discrepancy Description:** Binding to `127.0.0.1` is not a security boundary. Any page open in the user's browser can reach the server through cross-site requests or DNS rebinding. With no Origin or Host checks, it could read or delete the Memory graph or open a session.

  The plain SQLite file holds mental-health and sexuality disclosures, possibly on a shared Mac. Model downloads have no checksums or pinned revisions.
* **Rationale & Recommendation:** The fix is cheap, but it is a release blocker for a public product. Add a Security section covering:
  * a strict Host and Origin allowlist
  * a per-install random token in the served page
  * no permissive CORS
  * 0600 permissions on the data directory
  * a decision on encryption at rest (SQLCipher or a Keychain-wrapped key)
  * model downloads pinned by hash

---

### M1. Slices are too large for one autonomous task - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `zoom-out`
* **Affected Scope:** Lines 164-168
* **Discrepancy Description:** S3 bundles five subsystems plus an eval. S4 combines UI and onboarding, and onboarding depends on S7. S5 combines schema, extraction, retrieval, an API, a panel, voice forget and an eval. S7 combines downloading with packaging.
* **Rationale & Recommendation:** Split them as follows:
  * S3:
    * a: sanitizer and mood mapper (pure functions)
    * b: prompt assembly
    * c: Orpheus service
    * d: persona baseline
  * S4:
    * a: orb, transcript, controls
    * b: onboarding, after S7a
  * S5:
    * a: schema and repository
    * b: extraction
    * c: retrieval
    * d: API and panel
    * e: forget
    * f: eval
  * S7:
    * a: download manager (resume, hashes, progress API)
    * b: wheel with the built client assets and entry point

### M2. The latency budget leaves out prefill, retrieval and thinking mode, and its terms are inconsistent - Severity: Major
* **Reviewer Lens:** `design`, `comprehension`, `style`, `structural`
* **Affected Scope:** Lines 33, 47, 70-79
* **Discrepancy Description:** The system prompt, persona, memory block, history and tag list come to about 1.5-3k tokens. Without a prefix cache, prefill alone blows the 450 ms budget. Putting the memory block before the history breaks the cache on every turn. Qwen3's default thinking mode must be disabled. Retrieval runs on the critical path but has no line in the budget.

  Terminology does not match: "voice-to-voice median" (L33), "end of speech -> first audio" (L70) and "p50" (L141). The 1.4 s budget and the 1.5 s gate leave 100 ms of headroom with no p90 target.
* **Rationale & Recommendation:** Keep a stable prompt prefix (persona, then history) and inject memory late, next to the user turn. Disable thinking mode. Add rows for retrieval and prefill. Define one latency term and set p50 and p90 targets on the named reference machine.

### M3. Barge-in is a v1 exit requirement but is unspecified and untested - Severity: Major
* **Reviewer Lens:** `design`, `comprehension`, `implementation-readiness`
* **Affected Scope:** Lines 33, 56, 81
* **Discrepancy Description:** The doc never says whether the assistant's history is truncated to the audio actually played. Without that, Ames "remembers" saying things the user never heard, and extraction learns from them. It also leaves these open:
  * whether generation is actually cancelled on the GPU
  * what minimum interruption counts (a cough, "mm-hm")
  * echo-induced false barge-in from her own laughs over speakers
* **Rationale & Recommendation:** Specify how history is truncated, the minimum interruption, and cancellation. Add an acceptance test: playback stops within X ms, generation is aborted and the transcript is truncated correctly.

### M4. The Pipecat claim is overstated: Orpheus and Parakeet are custom services - Severity: Major
* **Reviewer Lens:** `zoom-out`, `comprehension`, `design`, `structural`
* **Affected Scope:** Lines 46, 49, 56, 68, 163-164
* **Discrepancy Description:** L56 credits Pipecat with Whisper, Ollama and Kokoro, but the primary picks, Parakeet and Orpheus with streaming SNAC decode, need custom services. That is audio plumbing hidden inside S3. L68 says S2 benchmarks "each" model, but Orpheus only arrives in S3, so the riskiest component (a 400 ms TTS budget) is not benchmarked early.
* **Rationale & Recommendation:** Default STT to Pipecat's built-in Whisper MLX, and add Parakeet only if S2 shows Whisper misses its budget. Keep Orpheus as the one justified custom service, list it as an explicit risk, and benchmark it in S2.

### M5. Extraction errors become persistent false memories - Severity: Major
* **Reviewer Lens:** `design`
* **Affected Scope:** Lines 102-103
* **Discrepancy Description:** An 8B model will misattribute third-party statements to the user, and will extract hypotheticals, sarcasm, quotes and study material as personal facts. A wrong fact brought up proactively does more harm than a forgotten one.
* **Rationale & Recommendation:** Extract only from user utterances, with speaker-attribution rules. Set a retrieval confidence threshold. Add a voice correction path ("no, that's wrong"). Gate on memory precision in the eval.

### M6. v1 is over-scoped - Severity: Major
* **Reviewer Lens:** `zoom-out`, `comprehension`, `style`, `design`
* **Affected Scope:** Lines 25, 86, 130-132, 136
* **Discrepancy Description:** Each extra multiplies eval or QA work:
  * A separate six-way mood classifier on top of the emotion tags.
  * "Orb or avatar".
  * A Settings panel with model size, voice and an undefined persona-intensity slider. No slice owns Settings, and each model or intensity level multiplies the persona, crisis and latency evals.
  * Firefox, which L25 lists but S4 does not test.
  * A scripted 20-second intro.
  * A browser download-progress UI.
* **Rationale & Recommendation:** v1 ships one model, one voice, one persona and an orb animated from speaking state plus the last sanitized tag or control-token mood. Support Chrome, or Chrome and Safari, and make L25 match S4. Replace the intro with a persona greeting. Use `huggingface_hub` progress in the terminal. Defer the rest to v1.x.

### M7. Missing sections: Alternatives, Risks, Non-goals, Failure modes - Severity: Major
* **Reviewer Lens:** `structural`, `design`
* **Affected Scope:** Whole document; only L56 gives any rationale
* **Discrepancy Description:** The doc fails the feature-spec standard: it has no rejected alternatives, no risk table, no non-goals and no degradation policy. These failure paths have no design:
  * microphone permission denied
  * out of memory or a model failing to load
  * a download that is corrupt or fails
  * malformed extraction or crisis JSON
  * WebRTC disconnect
  * runtime fallback to Kokoro
  * thermal throttling on fanless Airs
* **Rationale & Recommendation:** Add the following:
  * an Alternatives table: Pipecat vs LiveKit vs custom; MLX vs Ollama vs llama.cpp; SQLite triples vs vector store; Orpheus vs Kokoro as the v1 default
  * a Risks and Mitigations table
  * Goals and Non-goals: mobile, multi-user, sync, non-English, tools and web access, medical claims
  * a Failure Modes table with detection, behaviour and user message, including a fail-safe default for crisis parse errors

### M8. The emotion tag set and streaming sanitizer are underspecified - Severity: Major
* **Reviewer Lens:** `design`, `implementation-readiness`, `comprehension`
* **Affected Scope:** Lines 49, 81, 85-86
* **Discrepancy Description:** "<laugh> <sigh> ..." is not a list. The doc does not cover:
  * tags split across stream chunks
  * a sentence splitter cutting inside a tag
  * `*laughs*` asterisk actions
  * behaviour under the Kokoro fallback
  * whether tags appear in the transcript
  * how mood is derived and when it is available
* **Rationale & Recommendation:** Enumerate the exact tag set. Put a streaming-aware parser before the sentence splitter, with unit tests at chunk boundaries. Derive mood from the leading control token (B4).

### M9. Privacy and data retention are unspecified - Severity: Major
* **Reviewer Lens:** `structural`, `design`, `comprehension`
* **Affected Scope:** Lines 9, 92-104, 149
* **Discrepancy Description:** L9 promises that nothing leaves the device but never says what stays on it:
  * raw audio and transcripts
  * DPO pairs
  * logs and crash dumps
  * export

  "Delete everything" does not say what it covers.
* **Rationale & Recommendation:** Add a Data and Retention table: item, location, retention, user control and deletion semantics. Name the data directory, for example `~/Library/Application Support/Ames`.

### M10. The "no network calls" promise is not defined or enforced - Severity: Major
* **Reviewer Lens:** `comprehension`, `design`
* **Affected Scope:** Lines 9, 23, 136
* **Discrepancy Description:** The doc does not say where models come from or whether they are verified. It does not cover HF Hub, Ollama or Pipecat telemetry and update checks, or frontend CDNs and fonts.
* **Rationale & Recommendation:** Write a network policy covering:
  * allowed hosts during download only, with pinned revisions and hashes
  * `HF_HUB_OFFLINE` after setup
  * telemetry off
  * all frontend assets bundled

  Add an egress test (a session with the network blocked) to S7.

### M11. Open questions contradict constraints and block slices - Severity: Major
* **Reviewer Lens:** `zoom-out`, `structural`, `implementation-readiness`, `comprehension`
* **Affected Scope:** Lines 24, 132, 136, 170-175
* **Discrepancy Description:** L24 already fixes Apple Silicon, yet Q1 asks whether v1 is Mac-only, and Q1 drives the S2 and S7 runtime. Q3 (licenses) can invalidate packaging, and the Llama-derived Orpheus license needs checking before S3 commits to it. Q4 (18+ gate) affects onboarding but is phrased as "probably".
* **Rationale & Recommendation:** Close Q1: v1 runs on macOS only. Move the license check before S3. Promote the age gate to a pre-release requirement: a one-screen 18+ attestation on first run. Have each open question name the sections it blocks and the slice it must be decided by.

### M12. Model and dependency versions are not pinned - Severity: Major
* **Reviewer Lens:** `implementation-readiness`, `style`, `design`
* **Affected Scope:** Lines 46-49, 62, 68
* **Discrepancy Description:** The doc gives no HF repo IDs or revisions, no Parakeet version (v2 or v3), no Pipecat version, and a possibly stale Smart Turn v2. L68 says choices "since mid-2026 need a fresh check" but gives no owner or time.
* **Rationale & Recommendation:** Add a pinned table: model ID, revision or hash, quantization, runtime and package version. Re-check model choices before S2 starts, not during it.

### M13. v2, v3 and fine-tuning material is mixed into the v1 design - Severity: Major
* **Reviewer Lens:** `structural`, `zoom-out`, `style`
* **Affected Scope:** Lines 1, 34-36, sections 10-11, Q2
* **Discrepancy Description:** Speculative later work sits next to the build spec, with exit criteria that cannot be measured ("natural overlap", "beats base"). The "Track" row breaks the meaning of the Version column. Codex may build feedback hooks that nobody asked for.
* **Rationale & Recommendation:** Move sections 10 and 11 and Q2 to `docs/roadmap.md`. Retitle the doc "Ames v1: Design Doc". State explicitly that v1 ships no thumbs up/down UI and no feedback logging. In the fine-tuning plan, hold the persona eval prompts out of training (L148).

---

### Nits

| # | Lines | Lens | Issue and fix |
| --- | --- | --- | --- |
| N1 | 45, 46, 62, 65, 103, 34, 153, 154 | comprehension | Undefined jargon: Smart Turn, TDT, SNAC, one-hop, backchannel, Moshi, Chatterbox-class. Add a short glossary. |
| N2 | 68 | style, comprehension | "Model choices since mid-2026..." is ambiguous. Rewrite it with a date and a slice gate. |
| N3 | 9 | style | "Because of that, users can talk to her about anything" ties the content policy to privacy. Remove the causal link. |
| N4 | 15, 114, 142 | style | "Cringe" cannot be measured. Use "violates no rule in 6.2" and rename 6.2 to "Advice rules". |
| N5 | 142-143 | style | "About 100" and "about 30": state exact counts and the split (15/15). |
| N6 | 45/62, 46/63, 49/65, 86/130/165, 86/125/167 | style, structural | Naming drift: Smart Turn and its version, Parakeet formatting, Orpheus label, orb and avatar, data channel and side channel. Use one term for each. |
| N7 | 25 vs 137 | comprehension | `localhost` vs `127.0.0.1`: `localhost` may resolve to `::1` first. Pick one and state the port. |
| N8 | 136 | comprehension, implementation-readiness | "Or a single script": pick one install method. Say how the Vite client ships inside the wheel. Check that `ames` is free on PyPI. |
| N9 | 60-66 | style | The RAM column mixes "under 0.5" and "~1.5". Use "Approx RAM (GB)" with bare numbers. |
| N10 | 27, 154, 68 | structural, style | Plain-text cross-references. Use "Section 6.3" or anchor links. |
| N11 | 40, 94 | style | Code fences have no language tag. Add `text`. |
| N12 | 3-5, 108, 56/68/158 | structural, style | The header has no date or version. "Versioned" persona is not explained. The doc switches between "we" and third person. |

---

## Suggested minimal v1 path (from zoom-out, consistent with the other findings)

1. S1: loopback
2. S2: Whisper MLX + one in-process LLM runtime + Kokoro + harness, with Orpheus and concurrent load benchmarked on the slowest supported Mac
3. Eval: framework and datasets
4. S3: persona + Orpheus + streaming sanitizer + leading control token (crisis and mood)
5. S4a: orb UI on RTVI events (Chrome)
6. S7a: download manager
7. S5: memory, with idle-time extraction and delete from the panel
8. S6: crisis resources and eval
9. S7b: packaging

Deferred to v1.x: mood classifier, Settings sliders and model-size choice, Firefox, scripted intro, browser download UI, voice "forget that", and Parakeet unless the benchmark needs it.
