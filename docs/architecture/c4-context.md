# C4 level 1: system context

Ames is a local voice companion. One person talks to it in Chrome on their own Apple Silicon Mac, and every model runs on that Mac. Source: `docs/design.md` sections 1, 3 and 9.3.

```mermaid
flowchart TB
    user["<b>User</b><br/>[Person]<br/>Talks to Ames by voice in Chrome"]
    ames["<b>Ames</b><br/>[Software system]<br/>Local voice companion. Runs STT, LLM and TTS on the user's Mac. v1 binds 127.0.0.1:8765 only"]
    hf["<b>Hugging Face Hub</b><br/>[External system]<br/>Hosts the pinned model weights"]

    user -->|"Speaks and listens<br/>[WebRTC audio, HTTP on 127.0.0.1:8765]"| ames
    hf -.->|"Model weights, download only<br/>[HTTPS, ames setup, before first run]"| ames

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef ext fill:#999999,stroke:#6b6b6b,color:#ffffff
    class user person
    class ames system
    class hf ext
```

Notes:

- The dashed arrow happens only during `ames setup`. After the models are on disk the server sets `HF_HUB_OFFLINE=1` and makes no network calls, and WebRTC uses an empty `iceServers` list.
- `ames setup` (#35) is not built yet. Today the models are placed by hand (see `server/README.md`).
- Memory across sessions (SQLite, design section 6) is planned for v1.1 and is not part of the v1 context diagram. v1 keeps history in server memory for the session only.
