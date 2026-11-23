# C4 level 1: system context

Ames is a local voice agent. One person talks to it in Chrome on their own Apple Silicon Mac. Every model runs on that Mac. Source: `docs/design.md` sections 1, 3, and 9.3, and `server/tools.py`.

![C4 context diagram](c4-context.png)

The Mermaid source below is the source of truth. The PNG is a render of it.

```mermaid
flowchart TB
    user["<b>User</b><br/>[Person]<br/>Talks to Ames by voice or text in Chrome"]
    ames["<b>Ames</b><br/>[Software system]<br/>Local voice agent. Runs STT, LLM and TTS on the user's Mac. Binds 127.0.0.1:8765 only"]
    search["<b>Search services</b><br/>[External system]<br/>SearXNG at AMES_SEARXNG_URL, then Brave Search API if BRAVE_API_KEY is set"]
    hf["<b>Hugging Face Hub</b><br/>[External system]<br/>Hosts the model weights. STT and TTS are pinned"]

    user -->|"Speaks, types and listens<br/>[WebRTC audio, HTTP on 127.0.0.1:8765]"| ames
    ames -->|"Opt-in, off by default: search queries<br/>[HTTP]"| search
    hf -.->|"Planned: model weights, download only<br/>[HTTPS, ames setup (#35), before first run]"| ames

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef ext fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef planned fill:#ffffff,stroke:#888888,color:#555555,stroke-dasharray: 5 5
    class user person
    class ames system
    class search ext
    class hf planned
```

Notes:

- Web search is the only runtime network use. It is off by default. The user turns it on in the page. SearXNG sends each query to public search engines.
- The dashed arrow and the Hugging Face box are planned. `ames setup` (#35) is not built yet. Today you put the models in place yourself (see `server/README.md`).
- After the models are on disk, the server sets `HF_HUB_OFFLINE=1`. WebRTC uses an empty `iceServers` list.
