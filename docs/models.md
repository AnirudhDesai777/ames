# Models

Every model Ames runs, pinned to an exact revision. Hashes are SHA-256 of the
weight files. The recommended conversation LLM is the best scorer in the
[LLM bake-off](evals/llm-bakeoff.md) (#12). A fine-tuned Ames model is in
progress and is not published yet.

| Role | Repository | Revision | Weight files (SHA-256) | License |
|---|---|---|---|---|
| STT | `mlx-community/parakeet-tdt-0.6b-v2` | `8ae155301e23d820d82aa60d24817c900e69e487` | `model.safetensors` `b958c37a6baa6874a279108755c8f2818e27bf647d72d54800a234a421341dfe` | CC-BY-4.0 |
| TTS | `mlx-community/pocket-tts` (Pocket TTS, 100M) | `cbf71d5f6657bbc3f4bc02f85ee408261225bec7` | `model.safetensors` `60ddd85019dddbe6c1d220e311ca5fc753972978a89f563c0bbe1ae943120072`; `embeddings/azelma.safetensors` `ef33fad34437cb187d2702f0a946d8ba7a01efdb8efbc8088c770d49c181ba73`; `embeddings/marius.safetensors` `33f75e45fac0005630671f4b1bb632d51b6a083b18417de94855bbd7596a0630` | CC-BY-4.0 (weights and `azelma`), CC0 (`marius`) |
| VAD | Silero VAD, bundled in `pipecat-ai` 1.15.0 | `pipecat-ai==1.15.0` | `pipecat/audio/vad/data/silero_vad.onnx` `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` | MIT |
| Turn detection | Smart Turn v3.2, bundled in `pipecat-ai` 1.15.0 | `pipecat-ai==1.15.0` | `pipecat/audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx` `2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f` | BSD-2-Clause |
| LLM | `lmstudio-community/NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-4bit` (NVIDIA Nemotron 3.5 Bolt E4B, MLX 4-bit) | `4274caa99dbacb12cbbca9bd9595a5063a33880f` | `model.safetensors` `4f2e02b7ac3caa4fe3c34da096030dea4c5ac130230267e42d18bd5f16ab1af4` | OpenMDW 1.1 |

## License check

All five allow local use, redistribution of Ames's own code, and commercial use.

- Parakeet is CC-BY-4.0, inherited from `nvidia/parakeet-tdt-0.6b-v2`. It
  requires attribution: the README must credit NVIDIA and link the license
  before launch (#10).
- Pocket TTS weights (`kyutai/pocket-tts`, `mlx-community/pocket-tts`) are
  CC-BY-4.0. Credit Kyutai and link the license before launch (#10).
- The `azelma` voice (the female voice) is an embedding built from VCTK speaker
  p303, CC-BY-4.0, from the Voice Cloning Toolkit, University of Edinburgh.
- The `marius` voice (the male voice) is an embedding built from a Kyutai
  Unmute voice donation, CC0.
- Source: https://huggingface.co/kyutai/tts-voices README, voice repository
  revision `d4fdd22ae8c8e1cb3634e150ebeff1dab2d16df3`, checked 2026-11-13.
- Nemotron 3.5 Bolt E4B is OpenMDW 1.1, from the model card of the base
  model `nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-BF16`
  (https://openmdw.ai/license/1-1/). The LM Studio MLX conversion states no
  license of its own. Ames does not redistribute the weights; the user
  downloads them.
- Do not add `cosette`, `jean` or other Expresso or EARS voices: those are
  CC-BY-NC-4.0.
- Only `azelma.safetensors` and `marius.safetensors` are used; the snapshot's
  other embeddings stay unused.
- Silero VAD and Smart Turn ship inside the pinned Pipecat wheel, so bumping
  Pipecat changes these two rows.

Ames does not redistribute weights. `ames setup` (#35) downloads the pinned
revisions and checks these hashes.
