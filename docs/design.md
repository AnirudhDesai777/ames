# Ames: Design Doc (v1, with v2/v3 roadmap)

Status: draft for review
Build process: Claude Code orchestrates and reviews, Codex builds narrow slices.

## 1. What Ames is

Ames is a voice companion that runs in the browser and runs every model on the user's own machine. No audio, text, or memory leaves the device. Because of that, users can talk to her about anything.

She plays three roles:

- Thought partner: pushes back, asks the sharper question, does not just agree.
- Study buddy: quizzes, explains, notices when the user is stuck.
- Emotional companion: listens first, gives grounded advice, never cringe or therapy-speak.

Personality: sassy, warm, quick, and opinionated. She uses her voice to show emotion: she laughs, sighs, sounds excited, and gets quiet.

## 2. Constraints

| Constraint | Value |
|---|---|
| Inference | 100% local, no network calls after model download |
| Target hardware (v1) | Apple Silicon Mac, 16 GB unified memory minimum, 24 GB recommended |
| Client | Desktop Chrome/Safari/Firefox at `http://localhost` |
| Audience | Public product: users install it themselves |
| Content policy | No topic refusals. Crisis-aware (see 6.3) |

## 3. Roadmap

| Version | Pipeline | Exit criteria |
|---|---|---|
| v1 | Cascade: VAD -> STT -> LLM -> TTS, with barge-in | Median voice-to-voice latency under 1.5 s on M4 Pro; persona eval pass; memory recall works across sessions |
| v2 | Full duplex speech-to-speech model (listens while speaking, backchannels) | Natural overlap and backchannel; persona preserved |
| v3 | Live voice cloning from the user's own speech | Clone from under 30 s of audio, explicit consent, on-device only |
| Track | Persona fine-tuning (SFT, then DPO from local opt-in feedback) | Fine-tuned model beats the prompted base model on persona eval |

## 4. v1 architecture

```
Browser (UI + mic/speaker)
   |  WebRTC audio (Pipecat SmallWebRTC transport, localhost)
   v
Python server (Pipecat pipeline)
   Silero VAD + Smart Turn (end-of-turn detection)
   -> STT    : Parakeet TDT 0.6B (MLX)  [fallback: Whisper large-v3-turbo MLX]
   -> Memory : retrieve relevant graph facts -> inject into context
   -> LLM    : Qwen3-8B 4-bit via MLX/Ollama, streams text with emotion tags
   -> TTS    : Orpheus 3B (emotion tags: <laugh> <sigh> ...)  [fallback: Kokoro 82M, not expressive]
   -> Memory : async extraction of new facts after each turn
   |
   v
Browser plays audio, shows transcript and emotion state
```

Why Pipecat: it already provides local VAD, turn detection, interruption handling, MLX Whisper, Ollama and Kokoro services, and a browser WebRTC transport. We write the persona, memory, emotion mapping, and UI, not the audio plumbing.

### 4.1 Model choices and memory budget (24 GB machine)

| Component | Model | Approx RAM |
|---|---|---|
| VAD + turn | Silero + Smart Turn v2 | under 0.5 GB |
| STT | Parakeet TDT 0.6B MLX | ~1.5 GB |
| LLM | Qwen3-8B Q4 | ~5.5 GB |
| TTS | Orpheus 3B Q4 + SNAC decoder | ~3 GB |
| Total | | ~11 GB |

All picks are provisional. Slice S2 benchmarks each one and we swap any that miss the latency budget. Model choices since mid-2026 need a fresh check before we lock them.

### 4.2 Latency budget (end of user speech -> first audio)

| Stage | Budget |
|---|---|
| Turn detection | 300 ms |
| STT final | 150 ms |
| LLM first sentence | 450 ms |
| TTS first audio chunk | 400 ms |
| Transport + playback | 100 ms |
| Total | 1.4 s |

TTS starts on the first complete sentence. The LLM keeps streaming while she speaks.

### 4.3 Emotion

- The LLM may emit only the tags the TTS supports. The system prompt lists those tags, and the server strips any unknown tags.
- The server also maps each reply to a coarse mood (`neutral`, `amused`, `excited`, `soft`, `annoyed`, `concerned`) and sends it to the UI over the data channel. The UI uses it to animate her avatar.

## 5. Memory graph (ontology)

Goal: Ames remembers who the user is and refers back naturally, for example: "wait, isn't your organic chem midterm Thursday?"

Storage: one SQLite file in the user's data directory. No graph database.

```
entity(id, type, name, created_at)
relation(subject_id, predicate, object_id, confidence, source_turn_id, valid_from, valid_to)
```

Entity types (closed set, v1): `Person`, `Goal`, `Topic`, `Event`, `Feeling`, `Preference`, `Place`.
Predicates (closed set, v1): `knows`, `wants`, `studies`, `feels_about`, `scheduled_on`, `likes`, `dislikes`, `lives_in`, `related_to`.

- Extraction: after each turn, an async LLM call with a JSON schema proposes triples. Triples outside the ontology are dropped. A contradicting triple closes the old one by setting `valid_to`, so history is kept.
- Retrieval: entities mentioned in the current turn plus their one-hop neighbors, and recent `Event` and `Goal` items. The budget is 400 tokens.
- The user can view and delete everything in a Memory panel. "Forget that" works by voice.

## 6. Persona

### 6.1 Character sheet (lives in `persona/ames.md`, versioned)

- Voice: short sentences, contractions, teasing but never mean, swears lightly if the user does.
- She has opinions and states them. She admits when she does not know.
- Spoken-first: no lists, no markdown, at most 3 sentences unless the user asks her to go deep.

### 6.2 Advice rules (anti-cringe)

- Ask before advising when the situation is unclear.
- No therapy clichés ("it's valid to feel...", "have you tried journaling").
- Concrete over generic. One suggestion, not five.
- She disagrees with the user when they are wrong.

### 6.3 Crisis handling

- No topic is refused. Dark topics, sexuality, drugs, and venting are all allowed.
- On real self-harm or suicide signals, she drops the sass, slows down, stays present, and gives crisis resources. She does not end the conversation or lecture.
- Detection: the LLM sets a `crisis` flag in a structured side output. It is not a keyword filter.
- Resources: a locale-aware list bundled with the app, so it works offline.

## 7. UI

- One screen: an animated orb or avatar that reacts to mood and speaking state, a live transcript drawer, mute, and an end button.
- Panels: Memory (view/delete), Settings (voice, model size, persona intensity slider).
- First-run onboarding: microphone permission, model download progress, and a 20-second intro from Ames.

## 8. Packaging

- One command installs it: `uv tool install ames` or a single script. The first run downloads models with resume support and shows progress in the browser.
- The server binds to `127.0.0.1` only.

## 9. Evaluation

- Latency harness: replays recorded user turns and reports p50/p90 for each stage.
- Persona eval: about 100 scripted prompts, judged by a local LLM rubric (in character, not cringe, concise, pushes back when warranted).
- Crisis eval: about 30 prompts, half true crisis and half dark-but-not-crisis. Measures recall and false positives.
- Memory eval: a scripted multi-session conversation followed by recall questions.

## 10. Fine-tuning track (after v1)

1. SFT: build a persona dataset by writing synthetic conversations with a strong local model and filtering them through the persona eval. Train a LoRA on the v1 LLM with `mlx-lm`.
2. DPO: opt-in thumbs up/down in the UI produces local preference pairs. DPO LoRA runs on-device. No data leaves the machine.

## 11. v2 and v3 notes

- v2 duplex: evaluate the MLX ports of Moshi and its successors. Keep the browser transport and UI contract identical so only the server changes. The persona must be carried over by fine-tuning, because duplex models are weak at following prompts.
- v3 cloning: zero-shot cloning (Chatterbox-class model) on a short rolling buffer of the user's speech. It needs explicit opt-in, and the clone is deleted when the session ends unless the user saves it. Product intent is still open (see 13).

## 12. Build plan (v1 slices for Codex)

Each slice is one Codex task with acceptance criteria. Claude Code reviews each slice before the next one starts.

| Slice | Scope | Acceptance |
|---|---|---|
| S1 | Repo scaffold (uv, Python server, Vite+TS client), Pipecat SmallWebRTC loopback | Browser mic audio echoes back via server |
| S2 | Cascade with Kokoro + Whisper/Parakeet + Ollama, latency harness | Talk to a plain assistant; harness prints per-stage p50/p90 |
| S3 | Persona prompt, emotion tags, Orpheus TTS, tag sanitizer, mood events | Persona eval baseline recorded; laughs/sighs audible |
| S4 | UI: orb, transcript, mood animation, mute/end, onboarding | Manual QA checklist passes in Chrome and Safari |
| S5 | Memory graph: schema, extraction, retrieval, Memory panel, "forget that" | Memory eval passes |
| S6 | Crisis side-channel and offline resources | Crisis eval recall at least 0.9 |
| S7 | Packaging and first-run model download | Fresh Mac goes from install to talking in one command |

## 13. Open questions

1. Platforms: is v1 Mac-only (MLX), or do we also need Linux/Windows with NVIDIA (llama.cpp/CUDA) at launch?
2. v3 intent: does Ames speak in the user's voice, or is cloning used to let the user design her voice? This changes the consent design.
3. License: open source (which license?) or closed binary? Model licenses (Orpheus, Qwen, Parakeet) must allow redistribution either way.
4. Age gating: a public companion product with uncensored content probably needs an 18+ gate.
