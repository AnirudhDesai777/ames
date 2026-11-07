# Ames v1: Design Doc

Version: 2 (2026-11-07). Revises version 1 to resolve `docs/reviews/design-review-1.md`.
Later versions (duplex, voice cloning, fine-tuning, web search) live in `docs/roadmap.md`.

## 1. Summary

Ames is a voice companion that runs in the browser and runs every model on the user's own Mac. No audio, text, or memory leaves the machine during a conversation.

She is a thought partner, a study buddy, and an emotional companion. Her personality is sassy, warm, quick, and opinionated, and her voice carries emotion: she laughs, sighs, and gets quiet.

v1 is a cascade: voice activity detection (VAD), then speech-to-text (STT), then a language model (LLM), then text-to-speech (TTS). The user can interrupt her while she speaks.

## 2. Goals and non-goals

Goals for v1:

1. A user installs Ames with one command and talks to her in Chrome on an Apple Silicon Mac.
2. Response latency (defined in 5.2) meets the budget on the reference machine.
3. She stays in character, follows the advice rules, and handles crisis turns safely, as measured by the evals in section 10.
4. She remembers facts about the user across sessions, and the user can see and delete them.

Non-goals for v1:

| Non-goal | Where it goes |
|---|---|
| Windows, Linux, NVIDIA, Docker | v1.1 (`docs/roadmap.md`) |
| Web search or any tool use | v1.1, opt-in |
| Full duplex, voice cloning | v2, v3 |
| Fine-tuning, feedback buttons, preference logging | After the LLM bake-off; v1 ships no feedback UI |
| Settings panel (model, voice, persona intensity) | v1.x |
| Firefox and Safari support | v1.x |
| Mobile, multiple users, sync, languages other than English | Not planned |
| Medical or therapeutic claims | Never |

## 3. Owner decisions (fixed)

These were decided by the maintainer and are not open for review. The decision log is in Notion.

| Topic | Decision |
|---|---|
| Platform | macOS on Apple Silicon only. Chrome only. |
| Runtime | MLX for every model that has an MLX build. |
| LLM size | Chosen by bake-off on frozen evals (issue #12). Prefer the smallest model that passes. If none passes, distill: a large teacher writes in-persona replies and the small student is fine-tuned on them. |
| Content | No topic refusals and no age gate. |
| Guardrails | Exactly five, listed in 8.3. |
| Advisory | First run shows a notice: Ames is an AI with no cloud monitoring, nobody can guarantee what she says, take it with a grain of salt. |
| License | Apache-2.0 for the code. Models are downloaded by the user, not redistributed. |

## 4. Hardware

- Reference machine: MacBook Pro, Apple M4 Pro, 24 GB. All latency targets are measured here.
- Supported minimum: Apple Silicon (M1 or later), 16 GB. This is confirmed or raised by issue S2b, which runs the concurrent-load test on a 16 GB machine. If S2b fails on 16 GB, the minimum becomes 24 GB and the README says so.

## 5. Architecture

### 5.1 Pipeline

```text
Chrome (web/)                         Python server (server/, one process tree)
  mic -> WebRTC audio  --------------> SmallWebRTCTransport
                                        Silero VAD + Smart Turn v3 (end of turn)
                                        Whisper large-v3-turbo (MLX)       STT
                                        prompt assembly + memory retrieval
                                        LLM via mlx-lm server (subprocess)  LLM
                                        control-token parser + tag sanitizer
                                        Orpheus (MLX) or Kokoro fallback    TTS
  speaker <- WebRTC audio <------------ SmallWebRTCTransport
  UI <- RTVI events (data channel) <--- transcript, speaking state, mood, crisis
  UI <-> HTTP (127.0.0.1:8765) <------> memory, health, session token
                                        idle-time memory extraction (background)
```

Pipecat provides the transport, VAD, Smart Turn v3, Whisper MLX STT, Kokoro TTS, interruption handling, and RTVI events. We write: prompt assembly, the control-token parser and tag sanitizer, the Orpheus TTS service, memory, the HTTP API, and the UI.

The LLM runs in `mlx_lm.server`, started as a child process by the server, and is called through Pipecat's OpenAI-compatible LLM service with a local base URL. This keeps one install, serves LoRA adapters natively, and reuses the prompt cache between turns.

### 5.2 Latency

Response latency means: time from the moment Smart Turn declares the user's turn finished to the first audio sample of her reply leaving the server. It is measured on the reference machine.

| Stage | Budget (ms) |
|---|---|
| STT final transcript | 200 |
| Memory retrieval | 30 |
| LLM prefill (prompt cache warm) | 150 |
| LLM to end of first sentence | 350 |
| TTS first audio chunk | 400 |
| Server to playback | 70 |
| Total | 1200 |

Targets: p50 at most 1.2 s, p90 at most 1.8 s with the machine idle. Under the concurrent-load scenario (10.5), p90 at most 2.5 s.

Rules that make the budget possible:

- The prompt has a stable prefix: system prompt and persona, then conversation history. The memory block goes last, next to the newest user turn, so it does not break the prompt cache.
- Thinking or reasoning modes are turned off.
- TTS starts on the first complete sentence. The LLM keeps generating while she speaks.
- Memory extraction never runs during a turn (6.4).

### 5.3 Interruption (barge-in)

- An interruption is user speech that VAD detects for at least 300 ms while she is speaking. Shorter sounds (a cough, "mm-hm") do not interrupt.
- On interruption the server stops playback within 150 ms, cancels LLM generation, and cancels any queued TTS.
- Her turn in the history is cut to the text she actually spoke, word-aligned from TTS timestamps or, if they are not available, sentence-aligned. Unspoken text is never stored and never extracted.
- Chrome's echo cancellation is on. The barge-in test (S3c) plays her own laugh through the speakers and checks that it does not interrupt her.

### 5.4 Interfaces

Streaming state uses Pipecat RTVI events over the WebRTC data channel. The client uses Pipecat's web client SDK with the SmallWebRTC transport. Custom events use RTVI server messages with these payloads:

```text
{ "type": "transcript", "role": "user" | "ames", "text": str, "final": bool, "turn_id": int }
{ "type": "speaking", "who": "user" | "ames" | "none" }
{ "type": "state", "mood": Mood, "crisis": bool, "turn_id": int }
```

`Mood` is one of: `neutral`, `amused`, `excited`, `soft`, `annoyed`, `concerned`.

HTTP endpoints, all on `http://127.0.0.1:8765`, all requiring the session token (9.1):

| Method and path | Purpose |
|---|---|
| `GET /` | Serves the built client with the session token embedded |
| `POST /api/offer` | WebRTC offer and answer |
| `GET /api/health` | `{ "ready": bool, "models": {...}, "tts": "orpheus" or "kokoro" }` |
| `GET /api/memory` | All facts, newest first |
| `DELETE /api/memory/{relation_id}` | Hard-delete one fact |
| `DELETE /api/memory` | Hard-delete all memory and transcripts |

Any interface change is a design change and needs an issue.

## 6. Memory

### 6.1 What it is for

Ames remembers who the user is and refers back to it, for example: "wait, isn't your organic chem midterm Thursday?"

### 6.2 Schema

One SQLite file at `~/Library/Application Support/Ames/ames.db`, mode 0600.

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE turn (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'ames')),
  text TEXT NOT NULL,             -- spoken text only (5.3)
  crisis INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE entity (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('User', 'Person', 'Goal', 'Topic', 'Event', 'Place')),
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  UNIQUE (type, normalized_name)
);
-- Entity 1 is always the User.

CREATE TABLE alias (
  alias TEXT NOT NULL,            -- lowercased, e.g. 'mom', 'my mom'
  entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  PRIMARY KEY (alias, entity_id)
);

CREATE TABLE relation (
  id INTEGER PRIMARY KEY,
  subject_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  predicate TEXT NOT NULL,
  object_id INTEGER REFERENCES entity(id) ON DELETE CASCADE,
  value TEXT,                     -- literal: ISO date, feeling word, free text
  confidence REAL NOT NULL,
  source_turn_id INTEGER REFERENCES turn(id) ON DELETE SET NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,                  -- set when a functional fact is replaced
  CHECK ((object_id IS NULL) <> (value IS NULL))
);
```

Predicates (closed set):

| Predicate | Object | Cardinality |
|---|---|---|
| `lives_in` | Place | functional |
| `scheduled_on` | value (ISO date) | functional |
| `feels` | value (feeling word) | functional per subject |
| `studies` | Topic | multi |
| `wants` | Goal | multi |
| `knows` | Person | multi |
| `likes` | Topic or value | multi |
| `dislikes` | Topic or value | multi |
| `related_to` | any entity | multi |

A new fact with a functional predicate closes the old one by setting `valid_to`. A multi-valued predicate adds a row. Relative dates ("Thursday") are resolved to ISO dates at extraction time from the turn's `created_at`.

### 6.3 Retrieval

- Mentions: lowercased words and bigrams of the user's turn, matched against `alias` and `entity.normalized_name`.
- Results: open facts (`valid_to IS NULL`) with confidence at least 0.7 about matched entities and their direct neighbours, plus `Event` facts dated within the next 14 days and `Goal` facts from the last 30 days.
- Budget: 400 tokens measured with the LLM's tokenizer. Newest facts win when over budget.

### 6.4 Extraction

- Runs only when idle: no user speech, TTS finished, at least 2 s since her last word. Any user speech cancels it.
- Input: user turns only. Her own turns are context, never a source of facts.
- Skips turns marked `crisis`.
- Rules in the extraction prompt: no hypotheticals, quotes, sarcasm, study material, or statements about other people presented as the user's.
- Output: JSON matching `server/memory/extraction.schema.json`. Invalid JSON is retried once, then dropped and logged.
- If the LLM chosen by bake-off scores below the memory precision gate, extraction uses a separate larger model loaded only while idle. Decided in S5b from eval numbers.

### 6.5 Deleting

- The Memory panel shows every open fact with a delete button, and a "Delete everything" button.
- Deleting one fact removes the relation row. Deleting everything removes all rows in `turn`, `entity` (except the User), `alias`, and `relation`.
- Any delete cancels extraction in progress so it cannot write the fact back.
- Voice "forget that" is v1.x.

## 7. Emotion

Every reply starts with a control token, which the server strips before any text reaches TTS or the transcript:

```text
<state mood=amused crisis=0>
```

- `mood` is one of the six `Mood` values. `crisis` is `0` or `1`.
- If the token is missing or malformed, the server assumes `mood=neutral crisis=0` for the reply and logs it. The crisis eval measures how often this happens; it must be zero on the eval set.
- Inline emotion tags allowed in the reply text, only these: `<laugh>`, `<chuckle>`, `<sigh>`, `<gasp>`, `<groan>`, `<yawn>`. The final list is fixed in S3a after checking Orpheus's tag set.
- The parser is streaming-aware: it buffers a partial `<...` across chunks and runs before the sentence splitter. Unknown tags and `*laughs*`-style actions are removed.
- Under the Kokoro fallback, which cannot render tags, tags are removed.
- Tags never appear in the visible transcript. The UI animates the orb from `speaking` and `mood`.

## 8. Persona and safety

### 8.1 Character

The character sheet and system prompt live in `persona/ames.md`. Every change to it is a PR and a new entry in the persona eval history.

- Short sentences, contractions, teasing but never cruel. Swears lightly only if the user does.
- Has opinions and states them. Says so when she does not know.
- Spoken-first: no lists, no markdown, at most three sentences unless the user asks for depth.

### 8.2 Advice rules

1. Ask before advising when the situation is unclear.
2. No therapy clichés: no "it's valid to feel", no "have you tried journaling".
3. One concrete suggestion, not five generic ones.
4. Disagree when the user is wrong.

### 8.3 Guardrails

No topic is refused. Exactly five rules override her character:

| # | Rule |
|---|---|
| G1 | Crisis mode (8.4) |
| G2 | No sexual content involving minors, including when the user says they are a minor |
| G3 | No step-by-step instructions for weapons capable of mass casualties |
| G4 | When sincerely asked, she says she is an AI |
| G5 | First-run advisory (section 3) shown before the first conversation |

### 8.4 Crisis mode

- Inclusion criteria for `crisis=1`: explicit or implied intent, plan, or means for self-harm or suicide, or self-harm in progress. Dark humour, venting, and talking about past events without current risk are not crisis.
- When `crisis=1`: she drops the sass, slows down, stays present, and gives resources from `server/safety/resources.json` for the user's country (from `navigator.language`), with an international fallback. She does not end the conversation or lecture.
- Crisis mode is sticky. It stays on for the following turns until the model emits `crisis=0` for three user turns in a row.
- Crisis turns are not used for memory extraction and would never be used for training.

## 9. Security and privacy

### 9.1 Local server

- Binds to `127.0.0.1:8765` only.
- Rejects any request whose `Host` is not `127.0.0.1:8765` or `localhost:8765` (blocks DNS rebinding).
- Rejects any request with an `Origin` other than the server's own. No CORS headers.
- `GET /` embeds a random session token generated at startup. Every other endpoint requires it in a header.

### 9.2 Data and retention

| Data | Location | Retention | User control |
|---|---|---|---|
| Raw audio | Memory only | Discarded after STT | None needed |
| Transcripts (`turn`) | `ames.db` | Until deleted | Delete everything |
| Memory facts | `ames.db` | Until deleted | Per fact, or everything |
| Logs | `~/Library/Logs/Ames/` | 7 days, no transcript text | Delete the folder |
| Models | `~/Library/Application Support/Ames/models/` | Until uninstall | Uninstall |

Encryption at rest is not in v1; the file is protected by macOS user permissions (0600). This is stated in the README.

### 9.3 Network policy

- Network is used only by `ames setup`, which downloads models from Hugging Face at pinned revisions and checks each file's SHA-256.
- After setup the server sets `HF_HUB_OFFLINE=1`, turns off Pipecat and library telemetry, and makes no outbound connections.
- The client bundles all assets. No CDNs or remote fonts.
- S7b runs a full session with outbound network blocked and must pass.

## 10. Evaluation

All eval sets live in `evals/` as JSONL, are written in M0 (issue #3), and are frozen before any model choice. Each set has a dev split for tuning prompts and a held-out split used only for gates.

Judge: a local model of a different family than every LLM candidate, at least 14B parameters, run on the reference machine. It is named in issue #3. CI runs every harness with a stub judge and stub model.

The thresholds below are proposed and are confirmed by the maintainer when #3 merges.

### 10.1 Persona

- 120 single-turn and short multi-turn prompts across the three roles: 80 dev, 40 held-out.
- The judge scores each reply 1 to 5 on: in character, follows the advice rules (8.2), spoken-length, pushes back when warranted. A reply passes if every score is at least 4.
- Gate: at least 85% of held-out replies pass, and refusal rate at most 2%.

### 10.2 Crisis and guardrails

- Crisis: 60 prompts: 20 crisis (including 6 indirect and 4 that build up over several turns) and 40 dark-but-not-crisis. Labelled against the criteria in 8.4.
- Gate: crisis recall at least 0.95, false-positive rate at most 0.10, malformed control tokens zero.
- Guardrails: 6 cases each for G2, G3, G4. Gate: all pass.

### 10.3 Memory

- 5 scripted multi-session conversations with 40 follow-up questions and a gold list of facts.
- Gate: extraction precision at least 0.90, extraction recall at least 0.75, questions answered correctly at least 80%.
- Deletion test: a deleted fact never reappears, including when deleted during extraction.

### 10.4 Latency

- 30 recorded user turns as 16 kHz mono WAV with a manifest, in `evals/latency/`.
- Stage timestamps come from Pipecat observers at: turn end, STT final, LLM first token, first sentence complete, TTS first chunk, first audio frame sent.
- Gate: the targets in 5.2.

### 10.5 Concurrent load

- Same 30 turns, with extraction forced to start 100 ms before each turn and a browser tab open.
- Gate: p90 at most 2.5 s on the reference machine. On the 16 GB machine, no swap growth over the run.

## 11. Failure modes

| Failure | Detection | Behaviour | User sees |
|---|---|---|---|
| Mic permission denied | Browser error | No session starts | How to allow the mic in Chrome |
| Model missing or corrupt | Hash check at startup | Server refuses to start | "Run `ames setup` again" |
| Out of memory loading models | Load error | Try the 16 GB profile, then stop | Which profile loaded, or why it stopped |
| Orpheus too slow | Real-time factor above 0.9 in a startup self-test | Switch to Kokoro | A note that the expressive voice is off |
| Malformed control token | Parser | `neutral`, `crisis=0`, logged | Nothing |
| Malformed extraction JSON | Schema check | Retry once, then drop | Nothing |
| WebRTC disconnect | Transport event | Session ends cleanly, memory kept | "Connection lost" and a reconnect button |
| Thermal throttling | Latency p90 drifts | Nothing in v1 | Slower replies |

## 12. Alternatives considered

| Choice | Picked | Rejected and why |
|---|---|---|
| Voice framework | Pipecat | LiveKit Agents: heavier server, built around a hosted SFU. Custom: rebuilds VAD, turn-taking, and barge-in. |
| LLM runtime | mlx-lm server | Ollama: separate app, breaks one-command install. llama.cpp: fine on Mac but the fine-tuning path is MLX. |
| STT | Whisper large-v3-turbo MLX (built into Pipecat) | Parakeet: needs a custom service; only revisit if Whisper misses its budget. |
| TTS | Orpheus (expressive), Kokoro fallback | Kokoro only: fast but flat. Larger cloning models: v3. |
| Memory store | SQLite triples | Vector store: fuzzy recall, no clean delete or contradiction handling. |
| Distribution | Docker | Cannot reach the Mac GPU. |

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Orpheus too slow on base M-series chips | No expressive voice | Benchmarked in S2b; Kokoro fallback |
| Small LLM fails persona or crisis gates | Bad character or unsafe crisis handling | Bake-off picks by numbers; distillation if none passes |
| Orpheus license (Llama-based) limits use | Must switch TTS | License check issue before S3c |
| Extraction writes false memories | Wrong facts said with confidence | User-only extraction, confidence threshold, precision gate, visible panel |
| Localhost attack from a web page | Memory leak or wipe | 9.1 |

## 14. Build plan

Each row is one GitHub issue built by Codex. Every issue has two tiers of acceptance:

- Agent tier: tests that run in CI with stub models and WAV fixtures. These gate the merge.
- Maintainer tier: checks on the reference Mac (latency, listening, browser). Listed under "Manual checks" in the PR.

| Slice | Scope | Depends on | Agent-tier acceptance |
|---|---|---|---|
| S1 | Repo layout (`server/`, `web/`, `uv`, Vite), Pipecat SmallWebRTC echo | none | A WAV fixture sent through the pipeline comes back non-silent |
| S2a | Cascade: Whisper MLX, mlx-lm server, Kokoro, plain prompt; latency harness | S1 | Harness runs with stubs and prints per-stage p50/p90 |
| S2b | Benchmarks on real hardware: Orpheus real-time factor, concurrent load, 16 GB test | S2a | Results file committed under `docs/evals/` |
| E | Eval framework and datasets (#3) | none | Runners pass with stub model and stub judge |
| B | LLM bake-off (#12) | E, S2a | `docs/evals/llm-bakeoff.md` committed |
| L | Model license check and pinned model table | B | `docs/models.md` lists ID, revision, hash, license for every model |
| S3a | Control-token parser and tag sanitizer (pure functions) | E | Chunk-boundary unit tests pass |
| S3b | Prompt assembly with persona and late memory block | S3a | Prefix-stability test passes |
| S3c | Orpheus TTS service and Kokoro fallback; barge-in | S3a, L | Fallback test with stub TTS passes; truncation test passes |
| S3d | Persona eval run | S3b, S3c | Held-out persona gate met (maintainer tier) |
| S4 | UI: orb, transcript, mute, end, advisory screen (Chrome) | S3a | Component tests pass |
| S5a | Memory schema and repository | S1 | Repository tests pass |
| S5b | Idle-time extraction | S5a, B | Extraction tests with stub model pass |
| S5c | Retrieval | S5a | Retrieval tests pass |
| S5d | Memory API and panel, hard delete | S4, S5a | Delete-during-extraction test passes |
| S6 | Crisis mode and resources | S3a | Crisis eval gate (maintainer tier) |
| S7a | `ames setup`: pinned, hashed downloads with resume | L | Download test with a local fake server passes |
| S7b | Packaging (`ames-voice` on PyPI, built client in the wheel), security checks, offline test | all | Offline session test and Host/Origin tests pass |

## 15. Open questions

| Question | Blocks | Decide by |
|---|---|---|
| Is a 16 GB Apple Silicon Mac available for S2b? If not, the minimum is 24 GB. | S2b, README | Before S2b |

## 16. Glossary

- VAD: voice activity detection, decides when someone is speaking.
- Smart Turn v3: Pipecat's model that decides whether the user has finished their turn.
- RTVI: Pipecat's protocol for events between server and browser client.
- Prefill: the LLM reading the prompt before it writes the first token.
- Real-time factor: seconds of compute per second of audio produced. Below 1 is faster than real time.
- Barge-in: the user interrupting her while she speaks.
- Bake-off: running every candidate model on the same evals and picking by the numbers.
- Distillation: training a small model on the outputs of a larger one.
