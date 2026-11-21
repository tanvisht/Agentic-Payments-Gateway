"""HTTP surface of the gateway (FastAPI)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agentpay import __version__
from agentpay.api import schemas as sc
from agentpay.api.auth import Caller, authenticate
from pydantic import ValidationError

from agentpay.core.errors import AuthenticationError, GatewayError, NotFound, ValidationFailed
from agentpay.core.models import Event, Mandate, PaymentIntent
from agentpay.core.crypto import sha256_hex
from agentpay.core.errors import RateLimited
from agentpay.core.ids import new_id
from agentpay.gateway import Gateway
from agentpay.observability.logging import configure as configure_logging, log, request_id
from agentpay.observability.ratelimit import RateLimiter

api_log = logging.getLogger("agentpay.api")


def _dump(m: Any) -> Any:
    return m.model_dump(mode="json") if hasattr(m, "model_dump") else m


def _public_agent(a) -> dict:
    d = _dump(a)
    d.pop("api_key_hash", None)
    return d


def create_app(gw: Gateway | None = None, *, merchant=None) -> FastAPI:
    gw = gw or Gateway.build()
    from agentpay.acp.rpc import build_router
    from agentpay.acp.service import MerchantService
    merchant = merchant or MerchantService(gw)
    app = FastAPI(title="AgentPay Gateway", version=__version__,
                  description="Contactless, mandate-bound payments for autonomous AI agents.")
    app.state.gw = gw
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    limiter = RateLimiter(gw.settings.rate_per_s, gw.settings.rate_burst)
    configure_logging()

    @app.middleware("http")
    async def _context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_id("req")
        token = request_id.set(rid[:64])
        started = time.perf_counter()
        try:
            auth = request.headers.get("authorization", "")
            key = "k:" + sha256_hex(auth)[:16] if auth else "ip:" + (request.client.host if request.client else "?")
            ok, retry = limiter.take(key)
            if not ok:
                err = RateLimited("too many requests")
                resp = JSONResponse(err.to_dict(), status_code=429, headers={"Retry-After": str(max(1, int(retry + 0.999)))})
            else:
                resp = await call_next(request)
            resp.headers["X-Request-Id"] = rid[:64]
            log(api_log, logging.INFO, "request", method=request.method, path=request.url.path,
                status=resp.status_code, ms=round((time.perf_counter() - started) * 1000, 2))
            return resp
        finally:
            request_id.reset(token)

    @app.exception_handler(GatewayError)
    async def _gw_error(_: Request, exc: GatewayError):
        body = exc.to_dict()
        body["error"]["request_id"] = request_id.get()
        return JSONResponse(body, status_code=exc.http_status)

    async def admin(request: Request) -> Caller:
        c = await authenticate(gw, request, require_signature=False)
        if c.kind != "admin":
            raise AuthenticationError("admin credentials required")
        return c

    async def parse(model, request: Request):
        """Parse the body only *after* authentication, so unauthenticated callers learn nothing."""
        raw = await request.body()
        try:
            return model.model_validate_json(raw or b"{}")
        except ValidationError as exc:
            raise ValidationFailed("invalid request body", details={"errors": json.loads(exc.json(include_url=False))}) from None

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

    app.state.merchant = merchant
    app.include_router(build_router(merchant))

    # -- meta ---------------------------------------------------------
    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__, "providers": gw.router.health()}

    # -- registry (admin) ------------------------------------------------
    @app.post("/v1/principals", status_code=201)
    async def create_principal(body: sc.PrincipalIn, request: Request):
        await admin(request)
        return _dump(gw.create_principal(body.name, body.public_key, body.default_payment_method, body.email))

    @app.post("/v1/principals/{principal_id}/payment_method_setup", status_code=201)
    async def start_setup(principal_id: str, body: sc.SetupIn, request: Request):
        """Returns a SetupIntent client_secret for the principal's browser to confirm."""
        await admin(request)
        return gw.start_payment_method_setup(principal_id, body.provider)

    @app.post("/v1/principals/{principal_id}/payment_method_setup/complete")
    async def complete_setup(principal_id: str, body: sc.SetupIn, request: Request):
        await admin(request)
        return _dump(gw.complete_payment_method_setup(principal_id, body.provider))

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
    async def create_intent(request: Request):
        caller = await authenticate(gw, request, require_signature=True)
        if caller.kind != "agent":
            raise AuthenticationError("payment intents are created by agents")
        body = await parse(sc.PaymentIntentIn, request)

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

    async def _mutate(intent_id: str, request: Request, model, op):
        caller = await authenticate(gw, request, require_signature=True)
        own(caller, gw.payments.get(intent_id))
        body = await parse(model, request) if model else None
        return await idempotent(request, caller, body.model_dump() if body else None,
                                lambda: (200, _dump(op(body))))

    @app.post("/v1/payment_intents/{intent_id}/capture")
    async def capture(intent_id: str, request: Request):
        return await _mutate(intent_id, request, sc.CaptureIn, lambda b: gw.payments.capture(intent_id, b.amount))

    @app.post("/v1/payment_intents/{intent_id}/cancel")
    async def cancel(intent_id: str, request: Request):
        return await _mutate(intent_id, request, None, lambda b: gw.payments.cancel(intent_id))

    @app.post("/v1/payment_intents/{intent_id}/refunds")
    async def refund(intent_id: str, request: Request):
        return await _mutate(intent_id, request, sc.RefundIn, lambda b: gw.payments.refund(intent_id, b.amount, b.reason))

    @app.post("/v1/payment_intents/{intent_id}/retry")
    async def retry(intent_id: str, request: Request):
        caller = await authenticate(gw, request, require_signature=True)
        if caller.kind != "agent":
            raise AuthenticationError("only the owning agent can retry")
        body = await parse(sc.RetryIn, request)
        return await idempotent(request, caller, body.model_dump(),
                                lambda: (200, _dump(gw.payments.retry(intent_id, agent=caller.agent, payment_method=body.payment_method))))

    @app.get("/v1/payment_intents/{intent_id}/receipt")
    async def receipt(intent_id: str, request: Request, format: str = "json"):
        caller = await authenticate(gw, request, require_signature=False)
        own(caller, gw.payments.get(intent_id))
        rec = gw.receipt(intent_id)
        if format == "pdf":
            from fastapi.responses import Response
            from agentpay.core.receipts import to_pdf
            return Response(to_pdf(rec), media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="receipt-{intent_id}.pdf"'})
        return rec

    @app.get("/.well-known/agentpay")
    def discovery():
        return {"gateway_key": gw.signing_key.public_b64, "version": __version__,
                "receipt_type": "agentpay.receipt.v1", "acp": "/acp/rpc"}

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

    # -- identity & reputation ---------------------------------------------
    from agentpay.trust.registry import AgentCard, Feedback

    @app.post("/v1/registry/agents", status_code=201)
    async def register_card(request: Request):
        await admin(request)
        card = await parse(AgentCard, request)
        gw.get_agent(card.id)
        return _dump(gw.registry.register_card(card))

    @app.get("/v1/registry/agents/{agent_id}")
    async def get_card(agent_id: str):
        return {"card": _dump(gw.registry.card(agent_id)), "reputation": gw.registry.reputation(agent_id)}

    @app.post("/v1/registry/merchants", status_code=201)
    async def register_merchant(body: sc.MerchantIn, request: Request):
        await admin(request)
        return _dump(gw.registry.register_merchant(body.merchant_id, body.public_key, body.display_name))

    @app.post("/v1/registry/feedback", status_code=201)
    async def feedback(request: Request):
        fb = await parse(Feedback, request)  # the merchant signature is the credential
        return _dump(gw.registry.submit_feedback(fb))

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
