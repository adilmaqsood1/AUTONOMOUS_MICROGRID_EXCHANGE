"""Redis-backed ephemeral cache with in-memory fallback (order-book/state snapshots)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)


class Cache:
    def __init__(self) -> None:
        self._mem: dict[str, tuple[float, str]] = {}
        self._redis = None
        self._tried = False

    async def _client(self):
        if not self._tried:
            self._tried = True
            url = get_settings().redis_url
            if url:
                try:
                    import redis.asyncio as aioredis
                    r = aioredis.from_url(url, decode_responses=True)
                    await r.ping()
                    self._redis = r
                except Exception as exc:
                    log.warning("redis cache unavailable (%s); using in-memory cache", exc)
        return self._redis

    async def set_json(self, key: str, value: Any, ttl: int = 60) -> None:
        raw = json.dumps(value, default=str)
        r = await self._client()
        if r is not None:
            await r.set(key, raw, ex=ttl)
        else:
            self._mem[key] = (time.monotonic() + ttl, raw)

    async def get_json(self, key: str) -> Any | None:
        r = await self._client()
        if r is not None:
            raw = await r.get(key)
            return json.loads(raw) if raw else None
        item = self._mem.get(key)
        if not item or item[0] < time.monotonic():
            self._mem.pop(key, None)
            return None
        return json.loads(item[1])


cache = Cache()
