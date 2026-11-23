# Architecture

These C4 diagrams show Ames v1. They are Mermaid flowcharts, which GitHub renders. They follow `docs/design.md` (version 8.1). They were checked against the code in `server/` and `web/`.

| Level | Diagram | What it shows |
|---|---|---|
| 1 | [Context](c4-context.md) | The user, Ames, and the external systems: search services (opt-in) and Hugging Face (planned) |
| 2 | [Containers](c4-containers.md) | The web client, the single server process, the in-process models, the tools, and the local files |
| 3 | [Voice pipeline](c4-components-voice-pipeline.md) | The live audio path from the microphone to the speaker, with the tool loop and session events |

## Memory

The model saves and recalls free-text facts in SQLite (`ames.db`) through the `remember` and `recall_memory` tools. The page lists and deletes these facts. Automatic extraction of facts, retrieval of facts into the prompt, and summaries are planned for v1 and are not built yet. Conversation history stays in server memory for the session only.

## Differences between the design and the code

Where the design doc and the code are different, the diagrams show what the code does:

- Turn detection: the design lists Smart Turn v3 next to VAD. The live pipeline starts turns from Silero VAD only (`VADUserTurnStartStrategy`). It does not run Smart Turn.
- Setup and launch: the design describes an `ames` command (`ames setup`, `ames open`). Today you start the server with `python -m server.app`, and you put the models in place yourself.
- Web search: the design puts web search after v1. The code has it now, as an opt-in tool that is off by default.

## Regenerate the PNGs

Each diagram has a PNG render next to its source, for viewers that do not render Mermaid. After you edit a Mermaid block, regenerate its PNG from the repository root. You need `mmdc` from `@mermaid-js/mermaid-cli`.

```sh
for n in c4-context c4-containers c4-components-voice-pipeline; do
  sed -n '/^```mermaid/,/^```$/p' docs/architecture/$n.md | sed '1d;$d' > "${TMPDIR:-/tmp}/$n.mmd"
  mmdc -i "${TMPDIR:-/tmp}/$n.mmd" -o docs/architecture/$n.png -b white -s 3
done
```
