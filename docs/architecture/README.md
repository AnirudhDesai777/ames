# Architecture

C4 diagrams of Ames v1, drawn as Mermaid flowcharts that GitHub renders. They follow `docs/design.md` (version 6) and were checked against the code in `server/`.

| Level | Diagram | Shows |
|---|---|---|
| 1 | [Context](c4-context.md) | The user, Ames, and the one external system (Hugging Face, planned download via `ames setup`) |
| 2 | [Containers](c4-containers.md) | Web client, the single server process, in-process models, local files |
| 3 | [Voice pipeline](c4-components-voice-pipeline.md) | The live audio path from mic to speaker, with session events |

Memory across sessions (SQLite, design section 6) is deferred to v1.1. The container diagram shows it dashed as planned, and the other diagrams leave it out. v1 keeps history in server memory for the session only.

Where the design doc and the code differ, the diagrams show what the code does:

- Turn detection: the design lists Smart Turn v3 next to VAD. The live pipeline starts turns from Silero VAD only (`VADUserTurnStartStrategy`) and does not run Smart Turn.
- Setup and launch: the design describes an `ames` command (`ames setup`, `ames open`). The server is currently started with `python -m server.app` and models are placed by hand.

## Regenerating the PNGs

Each diagram has a PNG render next to its source for viewers that do not render Mermaid. After editing a Mermaid block, regenerate its PNG from the repository root (needs `mmdc` from `@mermaid-js/mermaid-cli`). This example is for the context diagram; repeat it for the other two names:

```sh
n=c4-context
sed -n '/^```mermaid/,/^```$/p' docs/architecture/$n.md | sed '1d;$d' > "${TMPDIR:-/tmp}/$n.mmd"
mmdc -i "${TMPDIR:-/tmp}/$n.mmd" -o docs/architecture/$n.png -b white -s 3
```

Use `c4-containers` and `c4-components-voice-pipeline` for the other two.
