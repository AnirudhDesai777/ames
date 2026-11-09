"""In-process MLX generation, serialized away from the audio event loop."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from server.mlx_runtime import MLX_LOCK

MODEL_DIR = Path.home() / "Library/Application Support/Ames/models"


def configure_models():
    os.environ["HF_HOME"] = str(MODEL_DIR)
    os.environ["HF_HUB_OFFLINE"] = "1"


class MLXLanguageModel:
    def __init__(self, model_path: Path):
        if not model_path.is_dir():
            raise ValueError("AMES_LLM_MODEL must name an existing local model directory")
        self.model_path = model_path
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ames-llm")
        self.model = None
        self.caches = {}

    def generate(self, messages, cache_name, cancelled, emit):
        with MLX_LOCK:
            configure_models()
            import mlx.core as mx
            from mlx_lm import load, stream_generate
            from mlx_lm.generate import generation_stream
            from mlx_lm.models.cache import LRUPromptCache, make_prompt_cache

            if cancelled.is_set():
                return
            if self.model is None:
                self.model, self.tokenizer = load(str(self.model_path))
                self.caches = {
                    name: LRUPromptCache(max_size=1) for name in ("conversation", "extraction")
                }
            prompt = self.tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, enable_thinking=False
            )
            store = self.caches[cache_name]
            # Leave one prompt token to evaluate even on an exact cache hit.
            cache, rest = store.fetch_nearest_cache(str(self.model_path), prompt[:-1])
            rest = rest + prompt[-1:]
            if cache is None:
                cache = make_prompt_cache(self.model)
            tokens = list(prompt)
            stream = stream_generate(
                self.model, self.tokenizer, rest, prompt_cache=cache, max_tokens=256
            )
            mx.synchronize()
        try:
            while not cancelled.is_set():
                # Release between tokens so TTS can start before generation ends.
                with MLX_LOCK:
                    if cancelled.is_set():
                        return
                    response = next(stream, None)
                    mx.synchronize(generation_stream)
                if response is None:
                    break
                tokens.append(response.token)
                if cancelled.is_set():
                    return
                emit(response.text)
            if not cancelled.is_set():
                with MLX_LOCK:
                    store.insert_cache(str(self.model_path), tokens, cache)
        finally:
            with MLX_LOCK:
                stream.close()
                mx.synchronize(generation_stream)
                mx.synchronize()

    async def tokens(self, messages, cache_name="conversation"):
        if cache_name not in {"conversation", "extraction"}:
            raise ValueError("Unknown prompt cache")
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        cancelled = Event()

        def emit(value):
            loop.call_soon_threadsafe(queue.put_nowait, value)

        def run():
            try:
                self.generate(messages, cache_name, cancelled, emit)
            except Exception as error:
                emit(error)
            finally:
                emit(None)

        future = loop.run_in_executor(self.executor, run)
        try:
            while (value := await queue.get()) is not None:
                if isinstance(value, Exception):
                    raise value
                yield value
        finally:
            cancelled.set()
            # Let interruption frames reach the audio queue before GPU work finishes.
            if not asyncio.current_task().cancelling():
                await asyncio.shield(future)

    async def close(self):
        await asyncio.to_thread(self.executor.shutdown, wait=True)


class StubLanguageModel:
    async def tokens(self, messages, cache_name="conversation"):
        for text in ("<state mood=neutral crisis=0>I heard you. ", "Tell me more."):
            await asyncio.sleep(0)
            yield text

    async def close(self):
        pass
