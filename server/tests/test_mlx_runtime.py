import asyncio
import sys
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from server.llm import MLXLanguageModel
from server.stt import ParakeetSTTService
from server.tts import QwenTTSService


async def test_stt_llm_and_tts_never_evaluate_mlx_concurrently(tmp_path, monkeypatch):
    guard = Lock()
    calls = []
    overlap = []

    def evaluate(name):
        if not guard.acquire(blocking=False):
            overlap.append(name)
            return
        try:
            calls.append(name)
            Event().wait(0.005)
        finally:
            guard.release()

    def llm_stream(*args, **kwargs):
        for i in range(3):
            evaluate("llm")
            yield SimpleNamespace(text="Hi.", token=4)

    def tts_stream(**kwargs):
        for i in range(3):
            evaluate("tts")
            yield SimpleNamespace(audio=np.ones(240), sample_rate=24000)

    def transcribe(signal):
        evaluate("stt")
        return SimpleNamespace(text="hello")

    mx = SimpleNamespace(
        array=np.asarray,
        eval=lambda *args: evaluate("load"),
        synchronize=lambda *args: evaluate("sync"),
    )
    tokenizer = SimpleNamespace(apply_chat_template=lambda *args, **kwargs: [1, 2, 3])
    cache = SimpleNamespace(
        fetch_nearest_cache=lambda *args: (None, [1, 2]),
        insert_cache=lambda *args: None,
    )
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(
        sys.modules,
        "mlx_lm",
        SimpleNamespace(
            load=lambda *args: (object(), tokenizer),
            stream_generate=llm_stream,
        ),
    )
    monkeypatch.setitem(sys.modules, "mlx_lm.generate", SimpleNamespace(generation_stream=object()))
    monkeypatch.setitem(
        sys.modules, "mlx_lm.sample_utils", SimpleNamespace(make_sampler=lambda **kwargs: None)
    )
    monkeypatch.setitem(
        sys.modules,
        "mlx_lm.models.cache",
        SimpleNamespace(
            LRUPromptCache=lambda **kwargs: cache,
            make_prompt_cache=lambda *args: [],
        ),
    )
    for role, generate in [("stt", transcribe), ("tts", tts_stream)]:
        model = SimpleNamespace(generate=generate, parameters=lambda: [])
        monkeypatch.setitem(
            sys.modules,
            f"mlx_audio.{role}.utils",
            SimpleNamespace(
                load_model=Mock(return_value=model),
            ),
        )
    stt = ParakeetSTTService(tmp_path)
    llm = MLXLanguageModel(tmp_path)
    tts = QwenTTSService(tmp_path)
    loop = asyncio.get_running_loop()

    async def speech():
        await loop.run_in_executor(stt.executor, stt.load_model)
        return [frame async for frame in stt.run_stt(b"\x01\x00" * 320)]

    async def language():
        return [token async for token in llm.tokens([])]

    async def voice():
        await loop.run_in_executor(tts.executor, tts.load_model)
        return [frame async for frame in tts.run_tts("Hi.", "id")]

    try:
        await asyncio.gather(speech(), language(), voice())
        assert {"stt", "llm", "tts", "load", "sync"} <= set(calls)
        assert overlap == []
    finally:
        await asyncio.gather(stt.cleanup(), llm.close(), tts.cleanup())
