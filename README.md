# Ames

Ames is a private voice agent. You talk to it in Chrome, and it talks back. Every model runs on your own Mac. Use it to talk through ideas and to do general research.

## Status

The live voice loop works end to end. These parts are built:

- Speech to text, a local language model, and text to speech, connected over WebRTC.
- A female voice and a male voice. You select one in the page.
- A text box. A typed message stops the current reply, and Ames speaks the answer.
- Tools that the model can call:
  - `remember` and `recall_memory` keep short facts in a local SQLite database. The page lists the facts and lets you delete them.
  - `get_time` reads the local clock.
  - `web_search` is off by default. You turn it on in the page.

These parts are planned for v1 and are not built yet:

- Automatic extraction of facts from the conversation.
- Automatic retrieval of facts into the prompt.
- Summaries of long conversations.

Conversation history stays in server memory for the current session only.

If the model marks a reply as a crisis, Ames does not speak that reply. It speaks a fixed line that tells you to call your local emergency number or to contact someone you trust. Then it ends the session and closes the connection. Ames is not a crisis service.

## Privacy

- The server listens only on `127.0.0.1`. No other computer can connect to it.
- Audio and transcripts do not leave your Mac.
- The server does not download models. You put them on disk yourself (see [server/README.md](server/README.md)).
- Web search is the only feature that sends data out. It is off by default. When you turn it on, each search query goes to a SearXNG server (default `http://localhost:8080`, setup in [docs/searxng.md](docs/searxng.md)). SearXNG sends the query to public search engines. If `BRAVE_API_KEY` is set and SearXNG fails, the query goes to the Brave Search API.

## How it works

The browser sends microphone audio to the server. The server turns speech into text, writes a reply with the language model, and turns the reply into speech. The browser plays the speech.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture/svg/context-dark.svg">
  <img alt="C4 context: the user talks to Ames on their own Mac. Only opt-in web search queries leave the Mac." src="docs/architecture/svg/context-light.svg">
</picture>

Inside the Mac, Chrome talks to one Python server. The models run inside the server process. Saved facts go to `ames.db`. The [architecture docs](docs/architecture/README.md) have the full C4 diagrams.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture/svg/containers-dark.svg">
  <img alt="C4 containers: Chrome, the Ames server with its voice pipeline, HTTP app, in-process models, session history and tools, the ames.db memory store, and opt-in search services." src="docs/architecture/svg/containers-light.svg">
</picture>

One spoken turn goes through these steps:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture/svg/turn-dark.svg">
  <img alt="One spoken turn: mic audio goes to speech to text, the transcript goes to the language model, and each complete sentence goes to text to speech and plays. Speaking for 300 ms or more stops Ames." src="docs/architecture/svg/turn-light.svg">
</picture>

Ames starts to speak after the first complete sentence. The model continues to write the next sentences at the same time.

## Requirements

- An Apple Silicon Mac with 24 GB of RAM or more. The reference machine is an M4 Pro with 24 GB.
- The text-to-speech real-time factor at startup must be 0.9 or less. If the Mac is too slow, the server stops. There is no fallback voice.
- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- Node 22.18 or a later Node 22 release.
- Chrome.

Stub mode (`AMES_STUB=1`) needs no models and no special hardware. CI uses stub mode on Ubuntu.

## Build and test

Run these commands from the repository root:

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

## Run in stub mode

Stub mode uses a fixed transcript, a fixed reply, and a tone in place of speech.

1. Build the web client (see the previous section).
2. From the repository root, run:

   ```sh
   AMES_STUB=1 uv run --locked python -m server.app
   ```

3. The server opens Chrome. The URL contains a private token after the `#`.
4. Accept the first-run advisory.
5. Select **Start conversation**. Chrome asks for the microphone.
6. Speak, or type in the text box. You hear the stub reply as a tone.

## Run with real models

1. Build the web client (see [Build and test](#build-and-test)).
2. Install the Mac dependencies:

   ```sh
   uv sync --locked --extra mac
   ```

3. Put the models on disk. The server never downloads weights. The revisions and hashes are in [docs/models.md](docs/models.md). All paths are under `~/Library/Application Support/Ames/models/`:
   - `parakeet-tdt-0.6b-v2/`: the complete `mlx-community/parakeet-tdt-0.6b-v2` MLX model directory.
   - `pocket-tts/`: the full `mlx-community/pocket-tts` snapshot. It must include `embeddings/azelma.safetensors` and `embeddings/marius.safetensors`.
   - A complete mlx-lm model directory for the language model.

   The recommended language model is NVIDIA Nemotron 3.5 Bolt E4B, MLX 4-bit. It scored best in the [LLM bake-off](docs/evals/llm-bakeoff.md). To download it:

   ```sh
   uv run --locked --extra mac hf download lmstudio-community/NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-4bit \
     --revision 4274caa99dbacb12cbbca9bd9595a5063a33880f \
     --local-dir "$HOME/Library/Application Support/Ames/models/nemotron-3.5-bolt-e4b-mlx-4bit"
   ```

   A fine-tuned Ames model is in progress and is not published yet. You can use a different local mlx-lm model directory.

4. Set `AMES_LLM_MODEL` to the language model directory. The server does not start without it. There is no default. Then run:

   ```sh
   AMES_STUB=0 AMES_LLM_MODEL="$HOME/Library/Application Support/Ames/models/nemotron-3.5-bolt-e4b-mlx-4bit" \
     uv run --locked --extra mac python -m server.app
   ```

## Repository layout

| Path | Contents |
|---|---|
| [server/](server/README.md) | Python server: HTTP app, voice pipeline, tools, memory store |
| [web/](web/README.md) | Browser client: TypeScript and Vite |
| [persona/](persona/ames.md) | Character sheet and system prompt |
| [evals/](evals/README.md) | Frozen eval sets and the eval runner |
| [docs/design.md](docs/design.md) | v1 design |
| [docs/models.md](docs/models.md) | Pinned model revisions, hashes and licenses |
| [docs/searxng.md](docs/searxng.md) | Local SearXNG setup for web search |
| [docs/tool-calling.md](docs/tool-calling.md) | Tool protocol |
| [docs/architecture/](docs/architecture/README.md) | C4 architecture diagrams |
| [docs/roadmap.md](docs/roadmap.md) | Work after v1 |
| [AGENTS.md](AGENTS.md) | Rules for contributors and coding agents |

## Credits

Ames runs these models. Each keeps its own license.

- Speech to text: [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) by NVIDIA, MLX conversion by `mlx-community`. [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
- Text to speech: [Pocket TTS](https://huggingface.co/kyutai/pocket-tts) by Kyutai, MLX conversion by `mlx-community`. [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
- Female voice (`azelma`): built from VCTK speaker p303, Voice Cloning Toolkit, Centre for Speech Technology Research, University of Edinburgh. [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Source: [kyutai/tts-voices](https://huggingface.co/kyutai/tts-voices).
- Male voice (`marius`): a Kyutai Unmute voice donation. [CC0](https://creativecommons.org/publicdomain/zero/1.0/). Source: [kyutai/tts-voices](https://huggingface.co/kyutai/tts-voices).
- Language model (recommended): [NVIDIA Nemotron 3.5 Bolt E4B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-BF16) by NVIDIA, MLX conversion by LM Studio. [OpenMDW 1.1](https://openmdw.ai/license/1-1/).
- Voice activity detection: Silero VAD, bundled in Pipecat. MIT.
- Turn detection: Smart Turn v3.2 by Pipecat. BSD-2-Clause.

## License

Apache License 2.0. See [LICENSE](LICENSE).
