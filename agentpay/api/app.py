"""HTTP surface of the gateway (FastAPI)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agentpay import __version__
from agentpay.api import schemas as sc
from agentpay.api.auth import Caller, authenticate
from agentpay.core.errors import AuthenticationError, GatewayError, NotFound
from agentpay.core.models import Event, Mandate, PaymentIntent
from agentpay.gateway import Gateway

log = logging.getLogger("agentpay.api")


def _dump(m: Any) -> Any:
    return m.model_dump(mode="json") if hasattr(m, "model_dump") else m


def _public_agent(a) -> dict:
    d = _dump(a)
    d.pop("api_key_hash", None)
    return d


def create_app(gw: Gateway | None = None) -> FastAPI:
    gw = gw or Gateway.build()
    app = FastAPI(title="AgentPay Gateway", version=__version__,
                  description="Contactless, mandate-bound payments for autonomous AI agents.")
    app.state.gw = gw
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(GatewayError)
    async def _gw_error(_: Request, exc: GatewayError):
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    async def admin(request: Request) -> Caller:
        c = await authenticate(gw, request, require_signature=False)
        if c.kind != "admin":
            raise AuthenticationError("admin credentials required")
        return c

    def own(caller: Caller, pi: PaymentIntent) -> PaymentIntent:
        if caller.kind == "agent" and pi.agent_id != caller.agent.id:  # type: ignore[union-attr]
            raise NotFound(f"payment intent {pi.id} not found")
        return pi

    async def idempotent(request: Request, caller: Caller, body: dict | None,
                         fn: Callable[[], Awaitable[tuple[int, dict]] | tuple[int, dict]]) -> JSONResponse:
        key = request.headers.get("idempotency-key")
        if not key:
            res = fn()
            status, out = await res if asyncio.iscoroutine(res) else res
            return JSONResponse(out, status_code=status)
        fp = gw.idempotency.fingerprint(request.method, request.url.path, body)
        cached = gw.idempotency.begin(caller.scope, key, fp)
        if cached:
            return JSONResponse(cached.body, status_code=cached.status, headers={"Idempotent-Replayed": "true"})
        try:
            res = fn()
            status, out = await res if asyncio.iscoroutine(res) else res
        except GatewayError as exc:
            gw.idempotency.complete(caller.scope, key, exc.http_status, exc.to_dict())
            raise
        except Exception:
            gw.idempotency.abandon(caller.scope, key)
            raise
        gw.idempotency.complete(caller.scope, key, status, out)
        return JSONResponse(out, status_code=status)

    # -- meta ---------------------------------------------------------
    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__, "providers": gw.router.health()}

    # -- registry (admin) ------------------------------------------------
    @app.post("/v1/principals", status_code=201)
    async def create_principal(body: sc.PrincipalIn, request: Request):
        await admin(request)
        return _dump(gw.create_principal(body.name, body.public_key, body.default_payment_method))

    @app.post("/v1/agents", status_code=201)
    async def create_agent(body: sc.AgentIn, request: Request):
        await admin(request)
        agent, api_key = gw.create_agent(body.principal_id, body.name, body.public_key)
        return {**_public_agent(agent), "api_key": api_key}

    @app.post("/v1/agents/{agent_id}/status")
    async def agent_status(agent_id: str, body: sc.AgentStatusIn, request: Request):
        await admin(request)
        return _public_agent(gw.set_agent_status(agent_id, body.status))

    @app.post("/v1/mandates", status_code=201)
    async def create_mandate(body: sc.MandateIn, request: Request):
        await admin(request)
        m = gw.mandates.register(principal=gw.get_principal(body.principal_id), agent=gw.get_agent(body.agent_id),
                                 scope=body.scope, nonce=body.nonce, not_before=body.not_before,
                                 expires_at=body.expires_at, signature=body.signature, description=body.description)
        gw.events.emit("mandate.created", {"mandate": _dump(m)})
        return _dump(m)

    @app.get("/v1/mandates/{mandate_id}")
    async def get_mandate(mandate_id: str, request: Request):
        caller = await authenticate(gw, request, require_signature=False)
        m = gw.mandates.get(mandate_id)
        if caller.kind == "agent" and m.agent_id != caller.agent.id:  # type: ignore[union-attr]
            raise NotFound(f"mandate {mandate_id} not found")
        return {**_dump(m), "remaining": m.remaining()}

    @app.get("/v1/mandates")
    async def list_mandates(request: Request):
        caller = await authenticate(gw, request, require_signature=False)
        if caller.kind == "agent":
            ms = gw.store.list("mandate", Mandate, k1=caller.agent.id)  # type: ignore[union-attr]
        else:
            ms = gw.store.list("mandate", Mandate)
        return {"data": [{**_dump(m), "remaining": m.remaining()} for m in ms]}

    @app.post("/v1/mandates/{mandate_id}/revoke")
    async def revoke_mandate(mandate_id: str, request: Request):
        await admin(request)
        m = gw.mandates.revoke(mandate_id)
        gw.events.emit("mandate.revoked", {"mandate": _dump(m)})
        return _dump(m)

    # -- payments (agent, signed) ----------------------------------------
    @app.post("/v1/payment_intents")
    async def create_intent(body: sc.PaymentIntentIn, request: Request):
        caller = await authenticate(gw, request, require_signature=True)
        if caller.kind != "agent":
            raise AuthenticationError("payment intents are created by agents")

        def run():
            pi = gw.payments.create(agent=caller.agent, **body.model_dump())  # type: ignore[arg-type]
            return 201, _dump(pi)
        return await idempotent(request, caller, body.model_dump(), run)

    @app.get("/v1/payment_intents")
    async def list_intents(request: Request, limit: int = 50):
        caller = await authenticate(gw, request, require_signature=False)
        limit = max(1, min(limit, 200))
        if caller.kind == "agent":
            data = gw.payments.list_for_agent(caller.agent.id, limit)  # type: ignore[union-attr]
        else:
            data = gw.store.list("payment_intent", PaymentIntent, limit=limit)
        return {"data": [_dump(p) for p in data]}

    @app.get("/v1/payment_intents/{intent_id}")
    async def get_intent(intent_id: str, request: Request):
        caller = await authenticate(gw, request, require_signature=False)
        pi = own(caller, gw.payments.get(intent_id))
        return {**_dump(pi), "ledger": gw.ledger.entries(pi.id)}

    async def _mutate(intent_id: str, request: Request, body: dict | None, op):
        caller = await authenticate(gw, request, require_signature=True)
        own(caller, gw.payments.get(intent_id))
        return await idempotent(request, caller, body, lambda: (200, _dump(op(caller))))

    @app.post("/v1/payment_intents/{intent_id}/capture")
    async def capture(intent_id: str, body: sc.CaptureIn, request: Request):
        return await _mutate(intent_id, request, body.model_dump(), lambda c: gw.payments.capture(intent_id, body.amount))

    @app.post("/v1/payment_intents/{intent_id}/cancel")
    async def cancel(intent_id: str, request: Request):
        return await _mutate(intent_id, request, None, lambda c: gw.payments.cancel(intent_id))

    @app.post("/v1/payment_intents/{intent_id}/refunds")
    async def refund(intent_id: str, body: sc.RefundIn, request: Request):
        return await _mutate(intent_id, request, body.model_dump(),
                             lambda c: gw.payments.refund(intent_id, body.amount, body.reason))

    @app.post("/v1/payment_intents/{intent_id}/retry")
    async def retry(intent_id: str, body: sc.RetryIn, request: Request):
        caller = await authenticate(gw, request, require_signature=True)
        if caller.kind != "agent":
            raise AuthenticationError("only the owning agent can retry")
        return await idempotent(request, caller, body.model_dump(),
                                lambda: (200, _dump(gw.payments.retry(intent_id, agent=caller.agent, payment_method=body.payment_method))))

    @app.post("/v1/payment_intents/{intent_id}/sync")
    async def sync(intent_id: str, request: Request):
        caller = await authenticate(gw, request, require_signature=False)
        own(caller, gw.payments.get(intent_id))
        return _dump(gw.payments.sync(intent_id))

    # -- human-in-the-loop (signature is the credential) -------------------
    @app.post("/v1/payment_intents/{intent_id}/approve")
    async def approve(intent_id: str, body: sc.ApproveIn):
        return _dump(gw.payments.approve(intent_id, body.signature))

    # -- provider webhooks -----------------------------------------------
    @app.post("/v1/providers/{provider}/webhook")
    async def provider_webhook(provider: str, request: Request):
        p = gw.router.get(provider)
        update = p.parse_webhook(await request.body(), request.headers.get("stripe-signature"))
        pi = gw.payments.apply_webhook(update) if update else None
        return {"received": True, "payment_intent": pi.id if pi else None}

    # -- operator ---------------------------------------------------------
    @app.post("/v1/webhook_endpoints", status_code=201)
    async def add_endpoint(body: sc.WebhookEndpointIn, request: Request):
        await admin(request)
        return _dump(gw.add_webhook_endpoint(body.url, body.events))

    @app.get("/v1/events")
    async def events(request: Request, limit: int = 50):
        await admin(request)
        return {"data": [_dump(e) for e in gw.store.list("event", Event, limit=max(1, min(limit, 500)))]}

    @app.get("/v1/events/stream")
    async def stream(request: Request):
        """Server-sent events for dashboards / the live demo."""
        await admin(request)
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()
        listener = lambda e: loop.call_soon_threadsafe(q.put_nowait, e)  # noqa: E731
        gw.events.subscribe_local(listener)

        async def gen():
            try:
                while not await request.is_disconnected():
                    try:
                        e = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {e.type}\ndata: {json.dumps(_dump(e))}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                gw.events._listeners.remove(listener)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v1/ledger/trial_balance")
    async def trial_balance(request: Request):
        await admin(request)
        return {"trial_balance": gw.ledger.trial_balance()}

    @app.get("/v1/ledger/balance")
    async def balance(account: str, currency: str, request: Request):
        await admin(request)
        return {"account": account, "currency": currency.upper(), "balance": gw.ledger.balance(account, currency.upper())}

    return app
