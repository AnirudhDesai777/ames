# Architecture

C4 diagrams of Ames v1, drawn as Mermaid flowcharts that GitHub renders. They follow `docs/design.md` (version 6) and were checked against the code in `server/`.

| Level | Diagram | Shows |
|---|---|---|
| 1 | [Context](c4-context.md) | The user, Ames, and the one external system (Hugging Face, download only) |
| 2 | [Containers](c4-containers.md) | Web client, the single server process, in-process models, local files |
| 3 | [Voice pipeline](c4-components-voice-pipeline.md) | The live audio path from mic to speaker, with session events |

Memory across sessions (SQLite, design section 6) is deferred to v1.1. The container diagram shows it dashed as planned, and the other diagrams leave it out. v1 keeps history in server memory for the session only.

Where the design doc and the code differ, the diagrams show what the code does:

- Turn detection: the design lists Smart Turn v3 next to VAD. The live pipeline starts turns from Silero VAD only (`VADUserTurnStartStrategy`) and does not run Smart Turn.
- Setup and launch: the design describes an `ames` command (`ames setup`, `ames open`). The server is currently started with `python -m server.app` and models are placed by hand.
