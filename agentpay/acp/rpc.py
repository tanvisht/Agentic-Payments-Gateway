"""JSON-RPC 2.0 transport for the ACP merchant (supports batches)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agentpay.acp import catalog
from agentpay.acp.service import MerchantService
from agentpay.core.errors import GatewayError

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
_APP_CODES = {"not_found": -32004, "validation_failed": INVALID_PARAMS, "acp_error": -32010}


def build_router(merchant: MerchantService) -> APIRouter:
    r = APIRouter()

    methods = {
        "initialize": lambda p: merchant.initialize(),
        "session/new": lambda p: merchant.session_new(p.get("buyerId", "")),
        "session/load": lambda p: merchant.session_load(p.get("sessionId")),
        "session/close": lambda p: merchant.session_close(p.get("sessionId")),
        "session/prompt": lambda p: merchant.prompt(p.get("sessionId"), _text(p)),
        "catalog/search": lambda p: {"products": [x.to_dict() for x in catalog.search(
            p.get("query", ""), max_price=p.get("maxPrice"), category=p.get("category"), size=p.get("size"),
            limit=min(int(p.get("limit", 5)), 20))]},
        "commerce/offer": lambda p: merchant.offer_for_sku(p.get("sessionId"), p.get("sku", ""), p.get("size")),
        "commerce/pay": lambda p: merchant.pay(p.get("sessionId"), p.get("offerId", ""), p.get("paymentIntentId", "")),
        "order/get": lambda p: merchant.order(p.get("orderId", "")),
    }

    def handle(msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _err(None, INVALID_REQUEST, "invalid JSON-RPC request")
        id_ = msg.get("id")
        fn = methods.get(msg["method"])
        if fn is None:
            return _err(id_, METHOD_NOT_FOUND, f"method {msg['method']!r} not found")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return _err(id_, INVALID_PARAMS, "params must be an object")
        try:
            result = fn(params)
        except GatewayError as e:
            return _err(id_, _APP_CODES.get(e.code, -32000), e.message, {"code": e.code, **e.details})
        except (TypeError, ValueError) as e:
            return _err(id_, INVALID_PARAMS, str(e))
        return None if "id" not in msg else {"jsonrpc": "2.0", "id": id_, "result": result}

    @r.post("/acp/rpc")
    async def rpc(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(_err(None, PARSE_ERROR, "parse error"))
        if isinstance(body, list):
            if not body:
                return JSONResponse(_err(None, INVALID_REQUEST, "empty batch"))
            out = [x for x in (handle(m) for m in body[:50]) if x is not None]
            return JSONResponse(out)
        res = handle(body)
        return JSONResponse(res) if res is not None else JSONResponse(None, status_code=204)

    @r.get("/acp/.well-known/merchant")
    async def merchant_card():
        return merchant.initialize()

    return r


def _text(p: dict) -> str:
    if isinstance(p.get("prompt"), list):  # ACP content blocks
        return " ".join(b.get("text", "") for b in p["prompt"] if isinstance(b, dict))
    return str(p.get("text", ""))


def _err(id_, code: int, message: str, data: dict | None = None) -> dict:
    e: dict[str, Any] = {"code": code, "message": message}
    if data:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": e}
