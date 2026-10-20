"""Real-time event hub: WebSocket fan-out with optional Redis relay across processes.

Events are queued on the DB session and only published AFTER the transaction commits, so
clients never see state that was rolled back.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import utcnow

log = logging.getLogger(__name__)

EVENTS = {"MICROGRID_STATE", "ASSET_UPDATE", "METER_READING", "ORDER_CREATED", "MARKET_CLEARED", "TRADE_CREATED",
          "RISK_EVENT", "FORECAST_UPDATE", "BALANCING_EVENT", "GRID_CONGESTION", "SETTLEMENT",
          "CERTIFICATE_EVENT", "GRID_OUTAGE", "ISLAND_MODE", "RECOVERY"}
REDIS_CHANNEL = "mgx:events"


def envelope(event_name: str, microgrid_id: uuid.UUID | str | None, data: dict[str, Any]) -> dict:
    from app.audit.service import _jsonable
    return {"event": event_name, "timestamp": utcnow().isoformat(),
            "microgrid_id": str(microgrid_id) if microgrid_id else None, "data": _jsonable(data)}


class _Client:
    def __init__(self, ws: WebSocket, microgrid_id: str | None, channels: set[str] | None):
        self.ws, self.microgrid_id, self.channels = ws, microgrid_id, channels

    def wants(self, env: dict) -> bool:
        if self.microgrid_id and env.get("microgrid_id") != self.microgrid_id:
            return False
        return not self.channels or env["event"] in self.channels


class EventHub:
    def __init__(self) -> None:
        self.clients: list[_Client] = []
        self.instance_id = uuid.uuid4().hex
        self._redis = None
        self._listener: asyncio.Task | None = None
        self.history: list[dict] = []  # small replay buffer (debug/tests)

    async def connect(self, ws: WebSocket, microgrid_id: str | None, channels: set[str] | None) -> _Client:
        await ws.accept()
        c = _Client(ws, microgrid_id, channels)
        self.clients.append(c)
        return c

    def disconnect(self, c: _Client) -> None:
        if c in self.clients:
            self.clients.remove(c)

    async def _deliver(self, env: dict) -> None:
        for c in list(self.clients):
            if not c.wants(env):
                continue
            try:
                await c.ws.send_json(env)
            except Exception as exc:  # client went away: drop it, but log
                log.info("dropping websocket client: %s", exc)
                self.disconnect(c)

    async def publish(self, event_name: str, microgrid_id, data: dict) -> dict:
        env = envelope(event_name, microgrid_id, data)
        self.history.append(env)
        del self.history[:-500]
        await self._deliver(env)
        if self._redis is not None:
            try:
                await self._redis.publish(REDIS_CHANNEL, json.dumps({"origin": self.instance_id, "env": env}))
            except Exception as exc:
                log.warning("redis publish failed: %s", exc)
        return env

    async def start_redis(self) -> None:
        url = get_settings().redis_url
        if not url:
            return
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(url, decode_responses=True)
            await r.ping()
        except Exception as exc:
            log.warning("redis unavailable (%s); running with in-process event hub only", exc)
            return
        self._redis = r
        self._listener = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                body = json.loads(msg["data"])
                if body["origin"] != self.instance_id:
                    await self._deliver(body["env"])
            except Exception as exc:
                log.warning("bad redis event: %s", exc)

    async def stop(self) -> None:
        if self._listener:
            self._listener.cancel()
        if self._redis is not None:
            await self._redis.aclose()


hub = EventHub()


def queue_event(session, event_name: str, microgrid_id, data: dict) -> None:
    """Queue an event to be published after the surrounding transaction commits."""
    sync = getattr(session, "sync_session", session)
    sync.info.setdefault("pending_events", []).append((event_name, microgrid_id, data))


@event.listens_for(Session, "after_commit")
def _flush_events(sync_session) -> None:
    pending = sync_session.info.pop("pending_events", None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover
        return
    for name, mg, data in pending:
        loop.create_task(hub.publish(name, mg, data))


@event.listens_for(Session, "after_rollback")
def _drop_events(sync_session) -> None:
    sync_session.info.pop("pending_events", None)
