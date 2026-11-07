# Ames roadmap after v1

Nothing here is built until v1 ships (issue #10 closed). Each item has a backlog issue.

## v1.1

### NVIDIA support (#6)

Docker Compose for Linux and Windows (WSL2) machines with an NVIDIA GPU. Docker cannot reach the Apple GPU, so Macs keep the native install. Needs a runtime that serves both platforms, such as llama.cpp, and a GGUF export of any fine-tuned adapter.

### Web search (#5)

Opt-in, off by default, with a visible indicator whenever she searches. Turning it on breaks the "nothing leaves your machine" promise for search queries, and the UI must say so. Needs tool calling from the chosen LLM and a search backend.

## Fine-tuning (#7)

1. Distillation (only if the bake-off in #12 finds no small model that passes): a large teacher model writes in-persona replies; the small student is fine-tuned on them with an `mlx-lm` LoRA.
2. Preference tuning: opt-in thumbs up or down in the UI produces local preference pairs for DPO. No data leaves the machine.

Persona eval prompts are held out of all training data.

## v2: full duplex (#8)

A speech-to-speech model that listens while it speaks and makes small listening sounds. The browser transport and the interfaces in design section 5.4 stay the same; only the server changes. Duplex models follow prompts poorly, so the persona has to come from fine-tuning.

## v3: voice cloning (#9)

Product intent is not decided yet: Ames speaking in the user's voice, or letting the user design her voice. Either way it needs explicit consent, runs on-device only, and the clone is deleted at session end unless the user saves it.
