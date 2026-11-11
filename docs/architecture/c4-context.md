# C4 level 1: system context

Ames is a local voice companion. One person talks to it in Chrome on their own Apple Silicon Mac, and every model runs on that Mac. Source: `docs/design.md` sections 1, 3 and 9.3.

![C4 context diagram](c4-context.png)

The Mermaid source below is the source of truth; the PNG is a render of it.

```mermaid
flowchart TB
    user["<b>User</b><br/>[Person]<br/>Talks to Ames by voice in Chrome"]
    ames["<b>Ames</b><br/>[Software system]<br/>Local voice companion. Runs STT, LLM and TTS on the user's Mac. v1 binds 127.0.0.1:8765 only"]
    hf["<b>Hugging Face Hub</b><br/>[External system]<br/>Hosts the model weights. STT and TTS are pinned, the LLM not yet (#28)"]

    user -->|"Speaks and listens<br/>[WebRTC audio, HTTP on 127.0.0.1:8765]"| ames
    hf -.->|"Planned: model weights, download only<br/>[HTTPS, ames setup (#35), before first run]"| ames

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef ext fill:#999999,stroke:#6b6b6b,color:#ffffff
    class user person
    class ames system
    class hf planned
    classDef planned fill:#ffffff,stroke:#888888,color:#555555,stroke-dasharray: 5 5
```

Notes:

- The dashed arrow and Hugging Face box are planned: `ames setup` (#35) is not built yet, and today the models are placed by hand (see `server/README.md`). Once built, it is the only network use. After the models are on disk the server sets `HF_HUB_OFFLINE=1` and makes no network calls, and WebRTC uses an empty `iceServers` list.
- Memory across sessions (SQLite, design section 6) is planned for v1 and not built yet, so it is not in this diagram. Until it is built, history stays in server memory for the session only.
