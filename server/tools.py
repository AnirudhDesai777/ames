"""Validated v1 tool execution; backends never see the raw model envelope."""

import asyncio
import os
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
from loguru import logger

from server.memory.repository import Repository
from server.tool_calls import validate_call


def memory_call(name, value):
    store = Repository()
    try:
        if name == "remember":
            store.remember(value)
            return {"saved": True}
        return {"episodes": store.recall(value)}
    finally:
        store.close()


def compact_results(items):
    results = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        title, url, snippet = item.get("title"), item.get("url"), item.get("snippet")
        if not all(isinstance(part, str) for part in (title, url, snippet)):
            continue
        if urlparse(url).scheme not in {"http", "https"}:
            continue
        results.append({"title": title[:120], "url": url[:300], "snippet": snippet[:240]})
    return {"results": results}


async def search(query):
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        try:
            base = os.environ.get("AMES_SEARXNG_URL", "http://localhost:8080").rstrip("/")
            async with session.get(
                base + "/search", params={"q": query, "format": "json"}
            ) as response:
                response.raise_for_status()
                payload = await response.json()
            return compact_results(payload["results"])
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
            logger.error("searxng_search_failed")
        key = os.environ.get("BRAVE_API_KEY")
        if key:
            try:
                async with session.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 3},
                    headers={"X-Subscription-Token": key},
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                return compact_results(
                    [
                        {
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "snippet": item.get("description"),
                        }
                        for item in payload["web"]["results"]
                    ]
                )
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                logger.error("brave_search_failed")
    raise RuntimeError("search_unavailable")


def local_time():
    current = datetime.now().astimezone()
    zone = current.tzinfo
    timezone = (
        zone.key if isinstance(zone, ZoneInfo) else (os.environ.get("TZ") or current.tzname())
    )
    return {"datetime": current.isoformat(), "timezone": timezone}


REGISTRY = {
    "recall_memory": lambda args: asyncio.to_thread(memory_call, "recall_memory", args["query"]),
    "remember": lambda args: asyncio.to_thread(memory_call, "remember", args["fact"]),
    "web_search": lambda args: search(args["query"]),
    "get_time": lambda args: asyncio.to_thread(local_time),
}


async def execute(call):
    validate_call(call)
    return await REGISTRY[call["name"]](call["arguments"])
