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
not transcribe, generate text, or synthesize speech. Real services belong to S2a.
Without `AMES_STUB=1`, construction fails with a setup message.

The committed fixture is a generated 200 ms, 440 Hz, mono PCM16 tone at 16 kHz.
Tests need no models, GPU, microphone, browser, or network connection.

The `mac` extra pins mlx-lm for later model work and only installs it on macOS
arm64. It is not needed for the echo. HTTP and WebRTC integration tests use only
local sockets; they do not contact external services. The ICE regression test
also checks the underlying aiortc configuration, where `None` would enable STUN.

## Streaming reply text

`server.control_tokens.parse_chunk(state, text)` is a pure function. Start each
reply with a fresh `ParserState()`, pass the returned `state` into the next call,
and finish with `parse_chunk(state, "", final=True)`. Discard the state when a
reply ends or is interrupted. No state is shared between replies.

The initial `<state mood=... crisis=...>` sets `state.mood` and `state.crisis`
without reaching either output. Partial tokens, inline tags, and `*actions*`
wait for their closing delimiter across chunks; unfinished markup is dropped
at the end. Missing or malformed control tokens default to neutral and 0.
`error_code` reports `control_token_missing` or `control_token_malformed` once
per reply. The caller must log only this code, never text or parser state.

Feed `tts_text` into the sentence splitter before TTS. It keeps only the six
inline tags listed in design section 7. Send `transcript_text` to the visible
transcript; it has no tags or actions. Ordinary text and spacing are preserved
after the leading control token. Pipeline wiring and the crisis exit belong to
later slices; this module only parses the crisis flag.

Run the chunk-boundary, malformed-token, and transcript checks without models:

```sh
AMES_STUB=1 uv run --locked pytest -q server/tests/test_control_tokens.py
```
