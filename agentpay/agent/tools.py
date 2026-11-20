"""Tool surface exposed to the LLM. The model never sees keys or card data —
only these narrow, mandate-bounded capabilities."""

from __future__ import annotations

import json
from typing import Any, Callable

from agentpay.agent import merchants
from agentpay.agent.client import GatewayClient, GatewayHTTPError

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_products",
        "description": "Search merchant catalogs. Prices are integer cents (USD). Use before buying.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"],
                         "properties": {"query": {"type": "string"},
                                        "max_price": {"type": "integer", "description": "max price in cents"},
                                        "category": {"type": "string"}}},
        "strict": True,
    },
    {
        "name": "check_mandate",
        "description": "Read the spending mandate the user signed: limits, remaining budget, allowed merchants/categories.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        "strict": True,
    },
    {
        "name": "purchase",
        "description": "Pay for one product by SKU under the mandate. Returns the payment status. "
                       "If status is requires_action, stop and tell the user approval is needed.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["sku"],
                         "properties": {"sku": {"type": "string"}}},
        "strict": True,
    },
    {
        "name": "get_payment",
        "description": "Fetch a payment intent by id.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["payment_intent_id"],
                         "properties": {"payment_intent_id": {"type": "string"}}},
        "strict": True,
    },
]


class ToolExecutor:
    def __init__(self, client: GatewayClient, mandate_id: str, on_event: Callable[[str, dict], None] | None = None) -> None:
        self.client, self.mandate_id = client, mandate_id
        self.on_event = on_event or (lambda *_: None)

    def __call__(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Returns (json result, is_error)."""
        self.on_event("tool_call", {"name": name, "input": args})
        try:
            out = self._run(name, args)
            err = False
        except GatewayHTTPError as exc:
            out, err = exc.body, True
        except (KeyError, ValueError) as exc:
            out, err = {"error": str(exc)}, True
        self.on_event("tool_result", {"name": name, "output": out, "is_error": err})
        return json.dumps(out, default=str), err

    def _run(self, name: str, a: dict[str, Any]) -> Any:
        if name == "search_products":
            return {"results": merchants.search(a["query"], a.get("max_price"), a.get("category"))}
        if name == "check_mandate":
            m = self.client.get_mandate(self.mandate_id)
            return {k: m[k] for k in ("id", "scope", "spent_amount", "reserved_amount", "remaining", "expires_at", "status")}
        if name == "purchase":
            p = merchants.get(a["sku"])
            if not p:
                raise ValueError(f"unknown sku {a['sku']}")
            pi = self.client.create_payment(mandate_id=self.mandate_id, amount=p["price"], currency=p["currency"],
                                            merchant_id=p["merchant_id"], category=p["category"],
                                            description=p["name"], metadata={"sku": p["sku"]},
                                            idempotency_key=f"buy:{self.mandate_id}:{p['sku']}")
            return {k: pi.get(k) for k in ("id", "status", "amount", "currency", "merchant_id", "provider", "next_action", "last_error")}
        if name == "get_payment":
            return self.client.get_payment(a["payment_intent_id"])
        raise ValueError(f"unknown tool {name}")
