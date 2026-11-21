"""Merchant-side x402 paywall: any FastAPI route can demand payment.

    GET /x402/products/{sku}            → 402 {x402Version, accepts:[requirements]}
    GET ... with X-PAYMENT: <b64>       → verify + settle → 200 + X-PAYMENT-RESPONSE
"""

from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agentpay.acp import catalog
from agentpay.x402.facilitator import Facilitator
from agentpay.x402.protocol import X402_VERSION, PaymentRequirements, decode_header


def build_router(facilitator: Facilitator, *, asset: str, network: str, merchant_addresses: dict[str, str]) -> APIRouter:
    r = APIRouter()

    @r.get("/x402/products/{sku}")
    async def product(sku: str, request: Request):
        p = catalog.get(sku)
        if not p or p.merchant_id not in merchant_addresses:
            return JSONResponse({"error": "not found"}, status_code=404)
        req = PaymentRequirements(network=network, maxAmountRequired=str(p.price * 10**4), resource=str(request.url),
                                  description=p.name, payTo=merchant_addresses[p.merchant_id], asset=asset)
        header = request.headers.get("x-payment")
        if not header:
            return JSONResponse({"x402Version": X402_VERSION, "error": "X-PAYMENT header is required",
                                 "accepts": [req.model_dump()]}, status_code=402)
        try:
            payment = decode_header(header)
        except ValueError as exc:
            return JSONResponse({"x402Version": X402_VERSION, "error": str(exc), "accepts": [req.model_dump()]}, status_code=402)
        settled = facilitator.settle(payment, req)
        if not settled.get("success"):
            return JSONResponse({"x402Version": X402_VERSION, "error": settled.get("errorReason"),
                                 "accepts": [req.model_dump()]}, status_code=402)
        receipt = base64.b64encode(json.dumps(settled).encode()).decode()
        return JSONResponse({"product": p.to_dict(), "paid": True}, headers={"X-PAYMENT-RESPONSE": receipt})

    return r
