"""Event log + outbound webhook delivery with exponential backoff."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import httpx

from agentpay.core.models import Event, WebhookEndpoint
from agentpay.storage import Store
from agentpay.webhooks import signing

log = logging.getLogger("agentpay.webhooks")


class EventBus:
    def __init__(self, store: Store, *, transport: httpx.BaseTransport | None = None,
                 max_attempts: int = 5, base_delay_s: float = 0.5, async_delivery: bool = True,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.async_delivery = async_delivery
        self.sleep = sleep
        self._client = httpx.Client(timeout=5.0, transport=transport)
        self.deliveries: list[dict] = []
        self._listeners: list[Callable[[Event], None]] = []

    def subscribe_local(self, fn: Callable[[Event], None]) -> None:
        """In-process listener (used by the SSE stream / live demo)."""
        self._listeners.append(fn)

    def emit(self, type_: str, data: dict) -> Event:
        evt = Event(type=type_, data=data)
        self.store.put("event", evt, k1=type_)
        for fn in list(self._listeners):
            try:
                fn(evt)
            except Exception:  # noqa: BLE001
                log.exception("local listener failed")
        for ep in self.store.list("webhook_endpoint", WebhookEndpoint, limit=1000):
            if "*" in ep.events or type_ in ep.events:
                if self.async_delivery:
                    threading.Thread(target=self._deliver, args=(ep, evt), daemon=True).start()
                else:
                    self._deliver(ep, evt)
        return evt

    def _deliver(self, ep: WebhookEndpoint, evt: Event) -> bool:
        body = json.dumps(evt.model_dump(), separators=(",", ":")).encode()
        for attempt in range(1, self.max_attempts + 1):
            headers = {"Content-Type": "application/json", signing.HEADER: signing.sign(ep.secret, body),
                       "AgentPay-Event-Id": evt.id}
            try:
                r = self._client.post(ep.url, content=body, headers=headers)
                ok = 200 <= r.status_code < 300
            except httpx.HTTPError as exc:
                ok, r = False, None
                log.warning("webhook %s attempt %d failed: %s", ep.url, attempt, exc)
            self.deliveries.append({"endpoint": ep.id, "event": evt.id, "attempt": attempt,
                                    "status": r.status_code if r is not None else None, "ok": ok})
            if ok:
                return True
            if attempt < self.max_attempts:
                self.sleep(self.base_delay_s * 2 ** (attempt - 1))
        return False
