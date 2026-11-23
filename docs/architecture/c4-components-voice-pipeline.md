# C4 level 3: voice pipeline components

This diagram shows the live pipeline inside the server, in the order that frames go through it. Source: `server/live.py` (`build_live`, `live_stages`, `ToolLoop`), `server/cascade.py` (`cascade_stages`), `server/prompt.py`, `server/control_tokens.py`, `server/tools.py`, `server/events.py`.

![C4 voice pipeline component diagram](c4-components-voice-pipeline.png)

The Mermaid source below is the source of truth. The PNG is a render of it.

```mermaid
flowchart TB
    browser["<b>Web client</b><br/>[Container]<br/>Mic, speaker, text box"]

    subgraph pipe["Voice pipeline [Pipecat pipeline, one per session]"]
        tin["<b>Transport input</b><br/>[SmallWebRTCTransport]<br/>Receives mic audio, 16 kHz"]
        vad["<b>VAD and turn start</b><br/>[Silero VAD, VADUserTurnStartStrategy]<br/>Speech of 300 ms or more starts a turn and interrupts"]
        stt["<b>STT</b><br/>[ParakeetSTTService, MLX]<br/>Transcribes each VAD segment"]
        prompt["<b>Prompt assembly</b><br/>[SessionPrompt, PromptAssembler]<br/>Persona and tool instructions, last 3000 history tokens, new user turn"]
        loop["<b>Tool loop</b><br/>[ToolLoop, mlx-lm, parse_chunk]<br/>Streams safe speech, runs one validated tool call, generates again. Strips control tokens and tags. Reads the crisis flag"]
        split["<b>Sentence splitter</b><br/>[SentenceSplitter, SimpleTextAggregator]<br/>Emits complete sentences"]
        tts["<b>TTS</b><br/>[PocketTTSService, MLX]<br/>Female or male voice, 24 kHz PCM, streaming"]
        tout["<b>Transport output</b><br/>[SmallWebRTCTransport]<br/>Sends Ames audio"]
        rec["<b>Session output</b><br/>[SessionOutput]<br/>Records sentences after they are sent, speaking events, crisis exit"]
    end

    tools["<b>Tools</b><br/>[server.tools]<br/>remember, recall_memory, get_time, web_search"]
    events["<b>Session events</b><br/>[Events, RTVI server messages]<br/>transcript, speaking, search, notice"]
    hist[("<b>Session history</b><br/>[In-process list]")]

    browser -->|"Mic audio<br/>[WebRTC]"| tin
    browser -->|"Typed text<br/>[data channel]"| prompt
    tin -->|"Audio frames"| vad
    vad -->|"Speech segments"| stt
    stt -->|"Transcript text"| prompt
    prompt -->|"Message list"| loop
    loop <-->|"Call and receipt"| tools
    loop -->|"Spoken text"| split
    split -->|"Sentences"| tts
    tts -->|"PCM audio"| tout
    tout -->|"Ames audio<br/>[WebRTC]"| browser
    tout -->|"Sent-sentence receipts"| rec

    prompt -->|"Reads turns, adds user turn"| hist
    rec -->|"Adds played sentences"| hist
    prompt -->|"user transcript, speaking"| events
    loop -->|"search"| events
    life["<b>Session lifecycle</b><br/>[Session, on_pipeline_error]<br/>Pipeline errors, session replaced or ended"]
    life -->|"notice"| events
    rec -->|"ames transcript, speaking, crisis notice"| events
    events -->|"[RTVI over WebRTC data channel]"| browser

    classDef comp fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef ext fill:#438dd5,stroke:#2e6295,color:#ffffff
    class tin,vad,stt,prompt,loop,split,tts,tout,rec,events,life,tools comp
    class browser,hist ext
```

Notes:

- The pipeline splits a reply into sentences before TTS. Speech starts at the first complete sentence while the LLM continues to generate.
- History gets only sentences whose audio the transport has sent. On interruption, the pipeline drops a cut sentence and queued sentences.
- On interruption (`InterruptionFrame`), the pipeline cancels LLM generation and drops queued TTS.
- Typed text enters the same way as barge-in: an `InterruptionFrame` stops any reply, then the text starts a turn. The diagram draws it into prompt assembly for clarity.
- `Conversation` derives the `speaking` event one time from two flags (user speaking, Ames speaking). Thus a late bot stop during barge-in cannot hide the user.
- When a reply has `crisis=1`, the tool loop speaks a fixed crisis line in place of the reply. After the line plays, session output sends `session_ended` and closes the connection.
- Notices come from the session lifecycle (the pipeline error handler in `server/live.py`, session close and replace in `server/app.py`), not from a pipeline stage.
- The HTTP app sends the first `speaking: none` event when it accepts the offer (`server/app.py`), before the pipeline runs.
- Prompt assembly has a slot for memory retrieval, but it gets an empty late block until automatic retrieval is built.
