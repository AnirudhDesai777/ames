# C4 level 2: containers

Ames v1 is a browser client and one Python server process. The three models run inside that process on MLX, so the diagram shows them as in-process modules, not as separate services. Source: `docs/design.md` sections 5 and 9, `server/app.py`, `server/tools.py`, `server/README.md`.

![C4 container diagram](c4-containers.png)

The Mermaid source below is the source of truth. The PNG is a render of it.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 90, "rankSpacing": 110}}}%%
flowchart TB
    user["<b>User</b><br/>[Person]<br/>Talks to Ames by voice or text"]

    subgraph chrome["Chrome on the user's Mac"]
        web["<b>Web client</b><br/>[Container: TypeScript, Vite, bundled assets]<br/>Orb, transcript, text box, mute, end, voice choice, web search switch, saved memories, advisory, notices"]
        ls[("<b>Browser storage</b><br/>[localStorage, sessionStorage]<br/>Advisory flag and voice choice (localStorage). Session token for this tab (sessionStorage)")]
    end

    subgraph server["Ames server: one Python process on 127.0.0.1:8765"]
        http["<b>HTTP app</b><br/>[Container: Python, aiohttp]<br/>GET /, /api/health, /api/offer, /api/rate, /api/search, /api/memories. Host, Origin and token checks. One active session"]
        pipeline["<b>Voice pipeline</b><br/>[Container: Pipecat, SmallWebRTC]<br/>VAD, turn start, STT, prompt, tool loop and LLM, sentence split, TTS. See component diagram"]
        stt["<b>Parakeet TDT 0.6B v2</b><br/>[Container: MLX in-process]<br/>Speech to text"]
        llm["<b>LLM service</b><br/>[Container: mlx-lm in-process, model from AMES_LLM_MODEL]<br/>Reply generation, prompt caches"]
        tts["<b>Pocket TTS 100M</b><br/>[Container: MLX in-process]<br/>Text to speech, female or male voice"]
        tools["<b>Tools</b><br/>[Container: server.tools]<br/>remember, recall_memory, get_time, web_search"]
        hist[("<b>Session history</b><br/>[In-process Python list]<br/>User transcripts and Ames sentences already sent. Cleared when the session ends")]
    end

    files[("<b>Local files</b><br/>[macOS filesystem, ~/Library/Application Support/Ames]<br/>models/, token (0600), search-enabled.json, turns/ (dev only)")]
    mem[("<b>Memory store</b><br/>[SQLite, ames.db]<br/>Saved free-text facts")]
    search["<b>Search services</b><br/>[External: SearXNG, Brave]<br/>Only with web search on"]

    user -->|"Speaks, types and listens"| web
    web -->|"Reads and writes<br/>[Web Storage API]"| ls
    web -->|"Loads page, sends offer, search switch, memories<br/>[HTTP with X-Ames-Token]"| http
    web -->|"Mic audio in, Ames audio out<br/>[WebRTC audio]"| pipeline
    web -->|"text, set_voice<br/>[WebRTC data channel]"| pipeline
    pipeline -->|"transcript, speaking, search, notice<br/>[RTVI over WebRTC data channel]"| web
    http -->|"Creates and ends sessions<br/>[Python call]"| pipeline
    pipeline -->|"Audio segments<br/>[Python call]"| stt
    pipeline -->|"Prompt in, tokens out<br/>[Python call]"| llm
    pipeline -->|"Sentences in, audio out<br/>[Python call]"| tts
    pipeline -->|"Validated tool calls<br/>[Python call]"| tools
    pipeline -->|"Reads and appends turns<br/>[Python call]"| hist
    tools -->|"remember, recall<br/>[sqlite3]"| mem
    http -->|"List and delete facts<br/>[sqlite3]"| mem
    tools -->|"Queries, opt-in<br/>[HTTP]"| search
    http -->|"Writes token, search switch<br/>[file write]"| files
    stt -->|"Loads weights<br/>[file read]"| files
    llm -->|"Loads weights<br/>[file read, path from AMES_LLM_MODEL]"| files
    tts -->|"Loads weights<br/>[file read]"| files

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef cont fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef ext fill:#999999,stroke:#6b6b6b,color:#ffffff
    class user person
    class web,http,pipeline,stt,llm,tts,tools,ls,hist,files,mem cont
    class search ext
```

Notes:

- The memory store holds facts that the model saves with the `remember` tool. The page lists and deletes them through `/api/memories`. Automatic extraction and retrieval into the prompt are planned for v1 and are not built yet.
- The server writes the token file when it starts. Chrome gets the token in the URL fragment, which never goes to the server.
- The server writes conversation text to disk only in two cases: facts that the model saves, and the dev-only turn log (`AMES_TURN_LOG=1`).
