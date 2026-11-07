# Server foundation

Use Python 3.12 and run from the repository root:

```sh
uv sync --locked
AMES_STUB=1 uv run --locked pytest
uv run --locked ruff check server
uv run --locked ruff format --check server
```

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
arm64. It is not needed for the echo. HTTP signaling, authentication, and the
browser connection flow belong to S1b; this slice exposes pipeline functions
without starting a listener.
