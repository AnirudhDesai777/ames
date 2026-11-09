# Models

Every model Ames runs, pinned to an exact revision. Hashes are SHA-256 of the
weight files. The conversation LLM is added by #28 after the LLM
bake-off (#12).

| Role | Repository | Revision | Weight files (SHA-256) | License |
|---|---|---|---|---|
| STT | `mlx-community/parakeet-tdt-0.6b-v2` | `8ae155301e23d820d82aa60d24817c900e69e487` | `model.safetensors` `b958c37a6baa6874a279108755c8f2818e27bf647d72d54800a234a421341dfe` | CC-BY-4.0 |
| TTS | `mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` | `41d3337e8b7f2843a75841595fc14e4b9a7a4b96` | `model.safetensors` `b696f4bf45f54497b6de758981e76f1410bd68af05a657477dee466b0202df1a`; `speech_tokenizer/model.safetensors` `836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258` | Apache-2.0 |
| VAD | Silero VAD, bundled in `pipecat-ai` 1.15.0 | `pipecat-ai==1.15.0` | `pipecat/audio/vad/data/silero_vad.onnx` `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` | MIT |
| Turn detection | Smart Turn v3.2, bundled in `pipecat-ai` 1.15.0 | `pipecat-ai==1.15.0` | `pipecat/audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx` `2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f` | BSD-2-Clause |

## License check

All four allow local use, redistribution of Ames's own code, and commercial use.

- Parakeet is CC-BY-4.0, inherited from `nvidia/parakeet-tdt-0.6b-v2`. It
  requires attribution: the README must credit NVIDIA and link the license
  before launch (#10).
- Qwen3-TTS is Apache-2.0, inherited from `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.
- Silero VAD and Smart Turn ship inside the pinned Pipecat wheel, so bumping
  Pipecat changes these two rows.

Ames does not redistribute weights. `ames setup` (#35) downloads the pinned
revisions and checks these hashes.
