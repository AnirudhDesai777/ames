"""Serialize MLX evaluation across STT, LLM, and TTS workers."""

from threading import RLock

MLX_LOCK = RLock()
