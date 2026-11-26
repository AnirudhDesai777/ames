"""Download the pinned models from docs/models.md and check their hashes.

Run: uv run --locked --extra mac python -m server.download

HF_TOKEN is optional. When it is set in the environment or in .env, Hugging
Face uses it. The models are public, so the download works without it.
"""

import hashlib
import sys
from pathlib import Path

from dotenv import load_dotenv

from server.llm import MODEL_DIR

# shortcut: no resume, retries or progress of our own (huggingface_hub resumes), full setup is #35.
MODELS = {
    "parakeet-tdt-0.6b-v2": {
        "repo": "mlx-community/parakeet-tdt-0.6b-v2",
        "revision": "8ae155301e23d820d82aa60d24817c900e69e487",
        "patterns": None,
        "hashes": {
            "model.safetensors": "b958c37a6baa6874a279108755c8f2818e27bf647d72d54800a234a421341dfe",
        },
    },
    "pocket-tts": {
        "repo": "mlx-community/pocket-tts",
        "revision": "cbf71d5f6657bbc3f4bc02f85ee408261225bec7",
        # The other voices in the repo are CC-BY-NC-4.0 (see docs/models.md).
        "patterns": [
            "*.json",
            "model.safetensors",
            "embeddings/azelma.safetensors",
            "embeddings/marius.safetensors",
        ],
        "hashes": {
            "model.safetensors": "60ddd85019dddbe6c1d220e311ca5fc753972978a89f563c0bbe1ae943120072",
            "embeddings/azelma.safetensors": (
                "ef33fad34437cb187d2702f0a946d8ba7a01efdb8efbc8088c770d49c181ba73"
            ),
            "embeddings/marius.safetensors": (
                "33f75e45fac0005630671f4b1bb632d51b6a083b18417de94855bbd7596a0630"
            ),
        },
    },
    "nemotron-3.5-bolt-e4b-mlx-4bit": {
        "repo": "lmstudio-community/NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-4bit",
        "revision": "4274caa99dbacb12cbbca9bd9595a5063a33880f",
        "patterns": None,
        "hashes": {
            "model.safetensors": "4f2e02b7ac3caa4fe3c34da096030dea4c5ac130230267e42d18bd5f16ab1af4",
        },
    },
}


def fetch(repo, revision, local_dir, patterns):
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo, revision=revision, local_dir=local_dir, allow_patterns=patterns)


def download(models_dir=MODEL_DIR):
    """Download every model, then return the files whose hash is wrong."""
    load_dotenv()
    bad = []
    for name, model in MODELS.items():
        target = Path(models_dir) / name
        print(f"{name}: {model['repo']}@{model['revision'][:7]}", flush=True)
        fetch(model["repo"], model["revision"], target, model["patterns"])
        for file, expected in model["hashes"].items():
            path = target / file
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != expected:
                # Remove it so the next run downloads the file again.
                path.unlink()
                bad.append(path)
    return bad


def main():
    bad = download()
    for path in bad:
        print(f"Hash mismatch, removed {path}. Run the command again.", file=sys.stderr)
    if bad:
        sys.exit(1)
    print(f"All models are in {MODEL_DIR}")


if __name__ == "__main__":
    main()
