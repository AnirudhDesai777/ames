# Ames

A local-only voice companion that runs in your browser. Every model runs on your own machine.

Status: S1b foundation. The repository has a model-free Pipecat SmallWebRTC audio
echo, an authenticated loopback HTTP app, and a browser connection flow.
Conversation models are not implemented yet.

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
Connect echo requests the microphone and plays its input back. Use headphones.

See [server/README.md](server/README.md) and [web/README.md](web/README.md) for
the foundation interfaces. CI runs these checks on Ubuntu with `AMES_STUB=1`
without models or hardware. The v1 plan remains in [docs/design.md](docs/design.md).
