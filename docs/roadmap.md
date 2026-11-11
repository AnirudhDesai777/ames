# Ames roadmap after v1

Nothing here is built until v1 ships (launch checklist #10 complete), except the items under "Planned for v1", which must ship before launch. Design v8 is a proposal the maintainer has not yet confirmed: it makes Ames a private thought partner with no moods, and the items marked PROPOSED below depend on it. Each item has a backlog issue.

## Planned for v1, not built yet

### Memory (#30, #31, #32)

The maintainer decided on 2026-11-10 (design v7) that memory across sessions is required before the public v1 launch. Today the code keeps history in server memory only, and the storage layer from #27 in `server/memory/` is unused. Still to build, as designed in design section 6: idle-time fact extraction (#30), retrieval into the late block (#31), and the memory API and panel (#32). Wiring then adds turn rows, the retrieval slot and the idle signal, the crisis exit marks stored turns, and the memory gate (design 10.3) and the extraction load run (10.5) apply. bolt-e4b scored 0.28 precision and 0.10 recall on extraction in the bake-off, so extraction quality needs work first: a tighter prompt, fewer predicates, or fine-tuning.

### Context summarising (PROPOSED, design v8 section 5.5)

When history nears the 3000-token cap, the LLM summarises the oldest turns while idle and the summary replaces them at the front of history. Durable facts still go through memory extraction. Builds on chunked history trimming (#86). Not built and no issue yet.

## v1.x

Small follow-ups deferred from v1: a Settings panel, Firefox and Safari support, voice "forget that", and encryption at rest for the memory database.

## v1.1

### NVIDIA support (#6)

Docker Compose for Linux and Windows (WSL2) machines with an NVIDIA GPU. Docker cannot reach the Apple GPU, so Macs keep the native install. Needs a runtime that serves both platforms, such as llama.cpp, and a GGUF export of any fine-tuned adapter.

### Web search (#5)

After v1 (PROPOSED in design v8). Opt-in, off by default, with a visible indicator whenever she searches. Turning it on breaks the "nothing leaves your machine" promise for search queries, and the UI must say so. Needs tool calling from the chosen LLM and a search backend.

## Proposed in design v8, after v1

- A phone client (#102).
- A smaller fine-tuned LLM (#29). Without moods the fine-tune targets the thought-partner replies and the crisis flag, not tone.
- A smaller TTS. Instruction control is no longer a requirement, which reopens options that the voice bake-off excluded for ignoring instructions. Unverified by listening.

## Preference tuning (#7)

Opt-in thumbs up or down in the UI produces local preference pairs for DPO with an `mlx-lm` LoRA. No data leaves the machine. Persona eval prompts are held out of all training data.

Distillation is not here: it is slice D in the v1 design and runs before v1 ships if the bake-off finds no passing model.

## v2: full duplex (#8)

A speech-to-speech model that listens while it speaks and makes small listening sounds. The browser transport and the interfaces in design section 5.4 stay the same; only the server changes. Duplex models follow prompts poorly, so the persona has to come from fine-tuning.

## v3: voice cloning (#9)

Product intent is not decided yet: Ames speaking in the user's voice, or letting the user design her voice. Either way it needs explicit consent, runs on-device only, and the clone is deleted at session end unless the user saves it.
