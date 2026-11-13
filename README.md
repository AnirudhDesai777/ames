# Ames

A private voice thought partner that runs in your browser: talk an idea through and get clear on what you think, with every model on your own machine so nothing leaves it. This is the product described in the design v8 proposal, which the maintainer has not yet confirmed.

Status: live pipeline wired. The authenticated loopback app connects browser
audio to local Parakeet, the persona prompt, mlx-lm, and Pocket TTS over WebRTC, with a female or male voice you pick in the page.
History stays in server memory for the current session. Memory across sessions and context summarising are planned for v1 and are not built yet; `server/memory/` is unused.

The real-voice minimum is Apple Silicon with 24 GB RAM and a TTS startup
real-time factor at most 0.9 (reference: M4 Pro, 24 GB). No fallback voice.

Use Python 3.12 with uv and Node 22.18 or newer within Node 22:

```sh
uv sync --locked
AMES_STUB=1 uv run --locked pytest -q
uv run --locked ruff check server
uv run --locked ruff format --check server
cd web
npm ci
npm run lint
npm test
npm run build
```

After building, run `AMES_STUB=1 uv run --locked python -m server.app` from the
repository root. It opens Chrome with a private token in the URL fragment.
Acknowledge the first-run advisory, then choose Start conversation to request
the microphone and hear the stub reply as tone audio. The client displays the
server transcript and session notices, with local mute and end controls. For
real speech, install the local models and set `AMES_LLM_MODEL` as described in
[server/README.md](server/README.md), then run with `AMES_STUB=0`.

See [server/README.md](server/README.md) and [web/README.md](web/README.md) for
the local interfaces. CI runs these checks on Ubuntu with `AMES_STUB=1`
without models or hardware. The v1 plan remains in [docs/design.md](docs/design.md), and the C4
architecture diagrams are in [docs/architecture/](docs/architecture/README.md).
