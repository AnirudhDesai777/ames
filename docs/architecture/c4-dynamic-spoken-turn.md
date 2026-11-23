# C4 dynamic: one spoken turn

This diagram shows the order of calls for one spoken turn. Source: `server/live.py`, `server/cascade.py`, `server/tts.py`.

```mermaid
C4Dynamic
  title One spoken turn

  Person(user, "User", "Talks by voice")
  Container(web, "Web client", "TypeScript, Vite", "Mic, speaker, transcript")

  Container_Boundary(server, "Ames server") {
    Component(stt, "Speech to text", "Parakeet TDT 0.6B, MLX", "Transcribes each VAD segment")
    Component(llm, "Language model", "mlx-lm", "Writes the reply, one sentence at a time")
    Component(tts, "Text to speech", "Pocket TTS 100M, MLX", "Speaks each complete sentence")
  }

  Rel(user, web, "Speaks")
  Rel(web, stt, "Sends mic audio", "WebRTC")
  Rel(stt, llm, "Sends transcript and session history")
  Rel(llm, tts, "Sends each complete sentence")
  Rel(tts, web, "Sends speech", "WebRTC")
  Rel(web, user, "Plays speech")
```

Notes:

- Steps 4 to 6 repeat for each sentence. Ames starts to speak after the first complete sentence, while the model writes the next one.
- If the user speaks for 300 ms or more, Ames stops and listens (see the voice pipeline component diagram).
