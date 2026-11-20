"""Shopping agent loop.

With ANTHROPIC_API_KEY set, Claude drives the tools (manual tool-use loop on the
Messages API). Without it, a deterministic planner executes the same tools in
the same order, so CI and offline demos exercise the identical payment path.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable

from agentpay.agent.tools import TOOLS, ToolExecutor

SYSTEM = (
    "You are a purchasing agent acting for a user under a cryptographically signed spending mandate. "
    "Always check the mandate first. Prefer negotiating with the merchant via ask_merchant (answer its clarifying "
    "questions from the user's goal), accept only offers with signature_valid=true, and buy with buy_offer; fall back "
    "to search_products + purchase when no merchant is connected. Pick the best option that satisfies both the "
    "user's goal and the mandate, then purchase exactly one item. Never retry a declined payment with a different item "
    "unless the user's goal allows it. Report the payment id and status at the end."
)


def run_with_claude(goal: str, tools: ToolExecutor, *, model: str | None = None, max_turns: int = 12,
                    on_event: Callable[[str, dict], None] | None = None) -> str:
    import anthropic

    emit = on_event or (lambda *_: None)
    client = anthropic.Anthropic()
    model = model or os.environ.get("AGENTPAY_AGENT_MODEL", "claude-opus-5-5")
    messages: list = [{"role": "user", "content": goal}]
    for _ in range(max_turns):
        resp = client.messages.create(model=model, max_tokens=16000, system=SYSTEM, tools=TOOLS,
                                      output_config={"effort": "medium"}, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                emit("agent_message", {"text": b.text})
        if resp.stop_reason == "refusal":
            return "The model declined this request."
        if resp.stop_reason != "tool_use":
            return next((b.text for b in reversed(resp.content) if b.type == "text"), "")
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out, err = tools(b.name, b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
        messages.append({"role": "user", "content": results})
    return "Stopped: turn limit reached."


def run_deterministic(goal: str, tools: ToolExecutor, on_event: Callable[[str, dict], None] | None = None) -> str:
    emit = on_event or (lambda *_: None)
    m = json.loads(tools("check_mandate", {})[0])
    budget = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", goal)
    cap = min(int(float(budget.group(1)) * 100) if budget else m["scope"]["max_amount_per_txn"],
              m["scope"]["max_amount_per_txn"], m["remaining"])
    words = [w for w in re.findall(r"[a-z]+", goal.lower()) if w in ("running", "shoes", "shoe", "tee", "flight", "travel")]
    found = json.loads(tools("search_products", {"query": " ".join(words) or goal, "max_price": cap})[0])["results"]
    allowed = m["scope"]["merchants_allowed"]
    found = [p for p in found if not allowed or p["merchant_id"] in allowed]
    if not found:
        emit("agent_message", {"text": "No product satisfies both the goal and the mandate."})
        return "No eligible product found."
    pick = found[-1]  # best (most premium) option within budget
    emit("agent_message", {"text": f"Selected {pick['name']} from {pick['merchant_id']} at ${pick['price']/100:.2f}."})
    res, err = tools("purchase", {"sku": pick["sku"]})
    r = json.loads(res)
    msg = f"Payment {r.get('id')} → {r.get('status')}" if not err else f"Payment failed: {r}"
    emit("agent_message", {"text": msg})
    return msg


def run_acp_deterministic(goal: str, tools: ToolExecutor, on_event: Callable[[str, dict], None] | None = None,
                          answers: list[str] | None = None) -> str:
    """Negotiate with the merchant (answering clarifications), pick the best verified offer, pay, confirm."""
    emit = on_event or (lambda *_: None)
    m = json.loads(tools("check_mandate", {})[0])
    pending = list(answers or [])
    res, err = tools("ask_merchant", {"message": goal})
    res = json.loads(res)
    for _ in range(4):
        if err or res.get("type") != "clarification":
            break
        if not pending:
            emit("agent_message", {"text": f"Merchant asks: {res['question']} (no answer available)"})
            return "Needs more information from the user."
        reply = pending.pop(0)
        emit("agent_message", {"text": f"Answering merchant: {reply}"})
        res, err = tools("ask_merchant", {"message": reply})
        res = json.loads(res)
    allowed = m["scope"]["merchants_allowed"]
    offers = [o for o in res.get("offers", []) if o.get("signature_valid") and o["price"] <= min(
        m["remaining"], m["scope"]["max_amount_per_txn"]) and (not allowed or o["merchant_id"] in allowed)]
    if not offers:
        return "No verified offer fits the mandate."
    pick = max(offers, key=lambda o: o["price"])
    emit("agent_message", {"text": f"Accepting signed offer {pick['id']}: {pick['name']} at ${pick['price']/100:.2f}"})
    out = json.loads(tools("buy_offer", {"offer_id": pick["id"]})[0])
    order = out.get("order")
    msg = f"Order {order['id']} confirmed, payment {out['payment']['id']}" if order else f"Payment {out.get('payment')}"
    emit("agent_message", {"text": msg})
    return msg


def run_agent(goal: str, tools: ToolExecutor, on_event: Callable[[str, dict], None] | None = None) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return run_with_claude(goal, tools, on_event=on_event)
    return run_deterministic(goal, tools, on_event=on_event)
