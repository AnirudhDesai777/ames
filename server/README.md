# Local HTTP server

Use Python 3.12 and run from the repository root:

```sh
uv sync --locked
AMES_STUB=1 uv run --locked pytest
uv run --locked ruff check server
uv run --locked ruff format --check server
```

Build the browser client with `cd web && npm ci && npm run build`, then run from
the repository root:

```sh
AMES_STUB=1 uv run --locked python -m server.app
```

The server binds only to `127.0.0.1:8765` and opens Chrome with a new random
token in the URL fragment. It writes the token to
`~/Library/Application Support/Ames/token` with mode 0600 inside a mode 0700
directory. The token rotates on each startup. The page and bundled assets are
public and contain no token; all API requests need `X-Ames-Token`. Every route
rejects other Hosts and foreign Origins, and sends no CORS headers. Requests
without an Origin are allowed for local clients with the token.

`GET /api/health` reports the three loaded stubs. `POST /api/offer` accepts
`{ "type": "offer", "sdp": "..." }` and returns an SDP answer, `pc_id`, and a
random `session_id`. A valid new offer closes the previous session after sending
`notice: session_replaced`. Invalid offers leave the active session alone.

`server.events.Events` wraps transcript, speaking, state, and notice payloads
from design 5.4 in Pipecat RTVI `server-message` envelopes. Each connection gets
a neutral state, no speaker, and a placeholder transcript. These values do not
describe the echoed audio. Session replacement and shutdown emit real notices.

`server.echo.echo_connection()` creates a SmallWebRTC connection with no ICE
servers. After an offer initializes it, `await server.echo.run_echo(connection)`
runs audio input through the stub stages and back to audio output. It cancels on
disconnect and closes the connection when the pipeline ends.

S1 deliberately preserves PCM through the STT and LLM stubs. The TTS stub wraps
it as output audio, so the test can check the echo byte for byte. These stages do
not transcribe, generate text, or synthesize speech. The S2a recorded-turn cascade
below provides transcription and text generation separately from this echo.
Without `AMES_STUB=1`, construction fails with a setup message.

The committed fixture is a generated 200 ms, 440 Hz, mono PCM16 tone at 16 kHz.
Tests need no models, GPU, microphone, browser, or network connection.

The `mac` extra installs mlx-lm and Pipecat's Whisper extras only on macOS
arm64. It is not needed for the echo or stub harness. HTTP and WebRTC integration
tests use only local sockets; they do not contact external services. The ICE regression test
also checks the underlying aiortc configuration, where `None` would enable STUN.

## Recorded cascade and latency

S2a adds `server.cascade`: Pipecat Whisper large-v3-turbo MLX transcription,
a plain single-user-turn prompt, an in-process mlx-lm service, Pipecat sentence
aggregation, and tone-only stub TTS. The HTTP app still runs the S1 echo;
live turn detection and the later conversation wiring are outside this harness.
No persona, memory retrieval, extraction scheduling, or real speech synthesis
runs here.

From the repository root:

```sh
AMES_STUB=1 uv run --locked python -m server.latency
```

This reads all 30 entries in `evals/latency/manifest.jsonl`. In stub mode it
uses each entry's transcript and the generated S1 tone fixture. It does not need
the pending recorded WAVs, import MLX, or download models. CI runs this command
and checks the full cascade with stubs in pytest.

The harness warms the first entry once, then prints nearest-rank p50 and p90
in milliseconds over all entries. Pipecat observers record turn end, final
transcript, first LLM token, first sentence, first TTS chunk, and first audio
sent. Rows show adjacent intervals plus the total. Memory retrieval is absent.
The output uses Pipecat's audio queue with a sink that accepts PCM and discards
it; the send timestamp follows a successful write, not arrival at the queue.
Replay declares turn end at the beginning of processing an already segmented
recording. It does not time VAD, Smart Turn, WebRTC, or browser playback.
Stub numbers do not measure model performance or establish the v1 latency gate.

For the manual run on the reference M4 Pro Mac with 24 GB RAM:

1. Record the manifest WAVs at their listed paths, as nonempty 16 kHz mono PCM16.
2. Run `uv sync --locked --extra mac`.
3. Place the complete Whisper large-v3-turbo MLX model directory at
   `~/Library/Application Support/Ames/models/whisper-large-v3-turbo/`.
   Place a complete mlx-lm model directory under the same `models/` directory.
   Model selection and pinned downloads are later slices; this command never
   downloads weights.
4. Run with the Mac idle, using the local LLM directory:

   ```sh
   AMES_STUB=0 AMES_LLM_MODEL="$HOME/Library/Application Support/Ames/models/llm" \
     uv run --locked --extra mac python -m server.latency
   ```

Real mode requires every listed WAV and fails if files are missing or malformed.
It still uses stub TTS, so it cannot validate the real voice budget. No microphone
or browser is needed during replay. Use `--manifest PATH` to replay another
manifest with the same schema; WAV paths resolve from the repository root.

Before importing model libraries the server sets `HF_HOME` to the models
directory and `HF_HUB_OFFLINE=1`. The LLM uses the tokenizer's chat template with
`enable_thinking=False`. It keeps independent one-entry prefix caches for
`conversation` and `extraction`; `model.tokens(messages, "extraction")` uses the
latter without evicting the former. A single worker thread serializes model
access and checks cancellation before advancing each token. User speech,
interruption, and pipeline cancellation stop generation; a ten-second token
timeout emits an error. A failed or incomplete turn produces no percentile
report. No transcript or generated text is printed by the harness.
