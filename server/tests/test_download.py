import hashlib

import pytest

from server import download


def write_pinned_files(monkeypatch, corrupt=None):
    """Stub the network: write every pinned file, with wrong bytes for `corrupt`."""
    contents = {}
    for name, model in download.MODELS.items():
        for file in model["hashes"]:
            data = f"{name}/{file}".encode()
            contents[(name, file)] = data
            model["hashes"][file] = hashlib.sha256(data).hexdigest()

    def fetch(repo, revision, local_dir, patterns):
        for (name, file), data in contents.items():
            if local_dir.name == name:
                path = local_dir / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"bad" if file == corrupt else data)

    monkeypatch.setattr(download, "fetch", fetch)


@pytest.fixture(autouse=True)
def restore_hashes():
    saved = {name: dict(model["hashes"]) for name, model in download.MODELS.items()}
    yield
    for name, hashes in saved.items():
        download.MODELS[name]["hashes"] = hashes


def test_download_accepts_matching_hashes(tmp_path, monkeypatch):
    write_pinned_files(monkeypatch)

    assert download.download(tmp_path) == []
    assert (tmp_path / "pocket-tts/embeddings/marius.safetensors").is_file()


def test_download_removes_a_file_with_a_wrong_hash(tmp_path, monkeypatch):
    write_pinned_files(monkeypatch, corrupt="embeddings/azelma.safetensors")

    bad = download.download(tmp_path)

    assert bad == [tmp_path / "pocket-tts/embeddings/azelma.safetensors"]
    assert not bad[0].exists()


def test_pocket_tts_skips_non_commercial_voices():
    patterns = download.MODELS["pocket-tts"]["patterns"]

    assert "embeddings/cosette.safetensors" not in patterns
    assert not any("*" in p and "embeddings" in p for p in patterns)
