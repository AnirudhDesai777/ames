import asyncio
import os
import sys
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server.llm import MODEL_DIR, MLXLanguageModel


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("HF_HOME", "unused")
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    class Cache:
        def __init__(self, max_size):
            assert max_size == 1
            self.saved = None

        def fetch_nearest_cache(self, model, tokens):
            if self.saved and self.saved[0][: len(tokens)] == tokens:
                return self.saved[1], []
            return None, tokens

        def insert_cache(self, model, tokens, cache):
            self.saved = (tokens, cache)

    tokenizer = Mock()
    tokenizer.apply_chat_template.return_value = [1, 2, 3]
    cache = SimpleNamespace(
        LRUPromptCache=Cache, make_prompt_cache=Mock(side_effect=lambda model: [])
    )
    threads = []

    def stream(model, tokenizer, prompt, **kwargs):
        threads.append(get_ident())
        yield SimpleNamespace(text="hello", token=4)
        yield SimpleNamespace(text=".", token=5)

    mlx = SimpleNamespace(load=Mock(return_value=(object(), tokenizer)), stream_generate=Mock())
    mlx.stream_generate.side_effect = stream
    monkeypatch.setitem(sys.modules, "mlx_lm", mlx)
    monkeypatch.setitem(sys.modules, "mlx_lm.models.cache", cache)
    return SimpleNamespace(mlx=mlx, cache=cache, tokenizer=tokenizer, threads=threads)


async def test_separate_caches_reuse_prefix_and_disable_thinking(tmp_path, runtime):
    model = MLXLanguageModel(tmp_path)
    messages = [{"role": "user", "content": "hi"}]
    try:
        for name in ("conversation", "extraction", "conversation"):
            assert [text async for text in model.tokens(messages, name)] == ["hello", "."]
        assert model.caches["conversation"] is not model.caches["extraction"]
        assert runtime.mlx.load.call_count == 1
        assert runtime.cache.make_prompt_cache.call_count == 2
        assert [call.args[2] for call in runtime.mlx.stream_generate.call_args_list] == [
            [1, 2, 3],
            [1, 2, 3],
            [3],
        ]
        runtime.tokenizer.apply_chat_template.assert_called_with(
            messages, tokenize=True, add_generation_prompt=True, enable_thinking=False
        )
        assert set(runtime.threads) != {get_ident()}
        assert len(set(runtime.threads)) == 1
        assert os.environ["HF_HOME"] == str(MODEL_DIR)
        assert os.environ["HF_HUB_OFFLINE"] == "1"
    finally:
        await model.close()


async def test_cancel_during_token_stops_before_next_and_keeps_cache(tmp_path, runtime):
    entered, release = Event(), Event()
    generated = []
    closed = []

    def stream(*args, **kwargs):
        try:
            for token in range(100):
                generated.append(token)
                if token == 1:
                    entered.set()
                    assert release.wait(5)
                yield SimpleNamespace(text=str(token), token=token)
        finally:
            closed.append(True)

    runtime.mlx.stream_generate.side_effect = stream
    model = MLXLanguageModel(tmp_path)

    async def consume():
        return [text async for text in model.tokens([])]

    task = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert generated == [0, 1]
        assert closed == [True]
        assert model.caches["conversation"].saved is None
    finally:
        release.set()
        await model.close()


async def test_errors_reach_caller_and_executor_can_be_closed(tmp_path, runtime):
    runtime.mlx.load.side_effect = RuntimeError("load failed")
    model = MLXLanguageModel(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="load failed"):
            [text async for text in model.tokens([])]
        with pytest.raises(ValueError, match="cache"):
            [text async for text in model.tokens([], "invalid")]
    finally:
        await model.close()


def test_model_directory_must_exist(tmp_path):
    with pytest.raises(ValueError, match="local model directory"):
        MLXLanguageModel(tmp_path / "missing")
