# C4 level 2: containers

Ames v1 is a browser client plus one Python server process. The three models run inside that process on MLX, so they are shown as in-process modules, not as separate services. Source: `docs/design.md` sections 5 and 9, `server/app.py`, `server/README.md`.

```mermaid
flowchart TB
    user["<b>User</b><br/>[Person]<br/>Talks to Ames by voice"]

    subgraph chrome["Chrome on the user's Mac"]
        web["<b>Web client</b><br/>[Container: TypeScript, Vite, bundled assets]<br/>Orb, transcript, mute, end, advisory, notices"]
        ls[("<b>Browser storage</b><br/>[localStorage, sessionStorage]<br/>Advisory seen flag, per-install token")]
    end

    subgraph server["Ames server: one Python process on 127.0.0.1:8765"]
        http["<b>HTTP app</b><br/>[Container: Python, aiohttp]<br/>GET /, POST /api/offer, GET /api/health. Host, Origin and token checks. One active session"]
        pipeline["<b>Voice pipeline</b><br/>[Container: Pipecat, SmallWebRTC]<br/>VAD, turn start, STT, prompt, LLM, sanitizer, sentence split, TTS. See component diagram"]
        stt["<b>Parakeet TDT 0.6B v2</b><br/>[Container: MLX in-process]<br/>Speech to text"]
        llm["<b>LLM service</b><br/>[Container: mlx-lm in-process, model from AMES_LLM_MODEL]<br/>Reply generation, one conversation prompt cache"]
        tts["<b>Qwen3-TTS 1.7B CustomVoice 8bit</b><br/>[Container: MLX in-process]<br/>Text to speech, voice Ryan"]
        hist[("<b>Session history</b><br/>[In-process Python list]<br/>Spoken text only, cleared when the session ends")]
    end

    files[("<b>Local files</b><br/>[macOS filesystem, ~/Library/Application Support/Ames]<br/>models/ directory, token file mode 0600")]
    mem[("<b>Memory store, planned for v1.1</b><br/>[SQLite, ames.db]<br/>Not used in v1")]

    user -->|"Speaks and listens"| web
    web -->|"Reads and writes<br/>[Web Storage API]"| ls
    web -->|"Loads page and assets<br/>[HTTP GET]"| http
    web -->|"Sends offer with X-Ames-Token<br/>[HTTP POST]"| http
    web -->|"Mic audio in, Ames audio out<br/>[WebRTC audio]"| pipeline
    pipeline -->|"transcript, speaking, state, notice<br/>[RTVI over WebRTC data channel]"| web
    http -->|"Creates and ends sessions<br/>[Python call]"| pipeline
    pipeline -->|"Audio segments<br/>[Python call]"| stt
    pipeline -->|"Prompt in, tokens out<br/>[Python call]"| llm
    pipeline -->|"Sentences in, PCM audio out<br/>[Python call]"| tts
    pipeline -->|"Reads and appends turns<br/>[Python call]"| hist
    http -->|"Writes token at startup<br/>[file write]"| files
    stt -->|"Loads weights<br/>[file read]"| files
    llm -->|"Loads weights<br/>[file read]"| files
    tts -->|"Loads weights<br/>[file read]"| files
    pipeline -.->|"v1.1: facts and turns<br/>[sqlite3]"| mem

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef cont fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef planned fill:#ffffff,stroke:#888888,color:#555555,stroke-dasharray: 5 5
    class user person
    class web,http,pipeline,stt,llm,tts,ls,hist,files cont
    class mem planned
```

Notes:

- The memory store is drawn dashed and white because it is planned for v1.1. The schema and repository exist in `server/memory/` but nothing in v1 opens them.
- The token file is written by the server when it starts. Chrome gets the token in the URL fragment, which never reaches the server.
- Conversation text is never written to disk in v1.
