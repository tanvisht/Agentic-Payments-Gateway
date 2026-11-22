"""ACP merchant service.

    session/new ─► session/prompt (clarify ⟲) ─► offers[] (merchant-signed, expiring)
                                                     │
    buyer agent pays via AgentPay ◄──────────────────┘
                                                     │
    commerce/pay {offer, payment_intent} ─► verify with gateway ─► order (fulfilled once)

Enhancements over a typical demo merchant: sessions persist in the gateway
store (not process memory), offers are Ed25519-signed and price-locked with an
expiry so the buyer can prove what was quoted, and an order is created only
after the merchant independently verifies the gateway payment (status,
amount, currency, merchant), with each payment usable for exactly one order.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentpay.acp import catalog, requirements
from agentpay.core import crypto
from agentpay.core.errors import GatewayError, NotFound, ValidationFailed
from agentpay.core.ids import new_id, now
from agentpay.core.models import IntentStatus, PaymentIntent
from agentpay.gateway import Gateway

OFFER_TTL_S = 600
PROTOCOL_VERSION = "acp/2026-11"


class AcpSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sess"))
    buyer_id: str
    status: str = "active"  # active | closed
    requirements: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)
    offer_ids: list[str] = Field(default_factory=list)
    created_at: int = Field(default_factory=now)


class Offer(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ofr"))
    session_id: str
    sku: str
    merchant_id: str
    name: str
    category: str
    price: int
    currency: str
    size: str | None = None
    expires_at: int
    signature: str = ""
    status: str = "open"  # open | paid | expired
    created_at: int = Field(default_factory=now)

    def signing_payload(self) -> dict[str, Any]:
        return {"type": "agentpay.offer.v1", **self.model_dump(exclude={"signature", "status", "created_at"})}


class Order(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ord"))
    offer_id: str
    session_id: str
    payment_intent_id: str
    merchant_id: str
    amount: int
    currency: str
    status: str = "confirmed"
    created_at: int = Field(default_factory=now)


class AcpError(GatewayError):
    code, http_status = "acp_error", 400


class MerchantService:
    def __init__(self, gw: Gateway, signing_key: crypto.KeyPair | None = None) -> None:
        self.gw = gw
        self.key = signing_key or crypto.KeyPair.generate()

    @property
    def public_key(self) -> str:
        return self.key.public_b64

    # -- sessions ----------------------------------------------------------
    def _session(self, sid: str | None) -> AcpSession:
        s = self.gw.store.get("acp_session", sid or "", AcpSession)
        if not s:
            raise NotFound(f"session {sid!r} not found; call session/new")
        return s

    def _save(self, s: AcpSession) -> None:
        self.gw.store.put("acp_session", s, k1=s.buyer_id)

    def initialize(self) -> dict:
        return {"protocolVersion": PROTOCOL_VERSION, "merchantKey": self.public_key,
                "capabilities": {"sessions": True, "clarification": True, "signedOffers": True,
                                 "payment": {"rails": ["agentpay"], "offerTtlSeconds": OFFER_TTL_S}},
                "methods": ["initialize", "session/new", "session/load", "session/prompt", "session/close",
                            "catalog/search", "commerce/offer", "commerce/pay", "order/get"]}

    def session_new(self, buyer_id: str) -> dict:
        if not buyer_id:
            raise ValidationFailed("buyerId is required")
        s = AcpSession(buyer_id=buyer_id)
        self._save(s)
        return {"sessionId": s.id}

    def session_load(self, sid: str) -> dict:
        s = self._session(sid)
        return s.model_dump()

    def session_close(self, sid: str) -> dict:
        s = self._session(sid)
        s.status = "closed"
        self._save(s)
        return {"sessionId": s.id, "status": "closed"}

    def prompt(self, sid: str, text: str) -> dict:
        s = self._session(sid)
        if s.status != "active":
            raise AcpError("session is closed")
        if not text or len(text) > 4000:
            raise ValidationFailed("prompt text must be 1-4000 characters")
        s.requirements = requirements.merge(s.requirements, requirements.extract(text))
        s.history.append({"role": "buyer", "text": text, "at": now()})
        need = requirements.missing(s.requirements)
        if need:
            q = requirements.question(need)
            s.history.append({"role": "merchant", "type": "clarification", "text": q, "at": now()})
            self._save(s)
            return {"type": "clarification", "missing": need, "question": q, "requirements": s.requirements}
        # Product words can arrive in any turn ("headphones" ... "under $400"), so search
        # over everything the buyer has said in this session, not just the last message.
        query = " ".join(h["text"] for h in s.history if h.get("role") == "buyer")
        offers = [self._make_offer(s, p) for p in catalog.search(
            query, max_price=s.requirements["max_price"], category=s.requirements["category"],
            size=s.requirements.get("size"), limit=3)]
        alternatives: list[dict] = []
        if not offers:
            # Never substitute silently: unrelated items in the same category are returned as
            # unsigned *alternatives* the buyer must explicitly ask for, not as offers.
            alternatives = [p.to_dict() for p in catalog.search(
                "", max_price=s.requirements["max_price"], category=s.requirements["category"],
                size=s.requirements.get("size"), limit=3)]
        s.history.append({"role": "merchant", "type": "offers", "offer_ids": [o.id for o in offers], "at": now()})
        self._save(s)
        out = {"type": "offers", "offers": [o.model_dump() for o in offers], "requirements": s.requirements}
        if not offers:
            out["message"] = "Nothing matches exactly; see alternatives." if alternatives else "Nothing matches those constraints."
            out["alternatives"] = alternatives
        return out

    # -- offers --------------------------------------------------------------
    def _make_offer(self, s: AcpSession, p: catalog.Product, size: str | None = None) -> Offer:
        o = Offer(session_id=s.id, sku=p.sku, merchant_id=p.merchant_id, name=p.name, category=p.category,
                  price=p.price, currency=p.currency, size=size or s.requirements.get("size"),
                  expires_at=now() + OFFER_TTL_S)
        o.signature = self.key.sign(o.signing_payload())
        self.gw.store.put("acp_offer", o, k1=s.id)
        s.offer_ids.append(o.id)
        return o

    def offer_for_sku(self, sid: str, sku: str, size: str | None = None) -> dict:
        s = self._session(sid)
        p = catalog.get(sku)
        if not p:
            raise NotFound(f"sku {sku!r} not found")
        if size and p.sizes and size not in p.sizes:
            raise ValidationFailed(f"size {size} unavailable")
        o = self._make_offer(s, p, size)
        self._save(s)
        return o.model_dump()

    @staticmethod
    def verify_offer(offer: dict, merchant_key: str) -> bool:
        o = Offer.model_validate(offer)
        return crypto.verify(merchant_key, o.signing_payload(), o.signature)

    # -- payment -------------------------------------------------------------
    def pay(self, sid: str, offer_id: str, payment_intent_id: str) -> dict:
        s = self._session(sid)
        o = self.gw.store.get("acp_offer", offer_id, Offer)
        if not o or o.session_id != s.id:
            raise NotFound(f"offer {offer_id} not found in this session")
        if o.status == "paid":
            existing = self.gw.store.find_one("acp_order", o.id, Order)
            return {"order": existing.model_dump() if existing else None, "replayed": True}
        if now() >= o.expires_at:
            o.status = "expired"
            self.gw.store.put("acp_offer", o, k1=o.session_id)
            raise AcpError("offer expired; request a new one")
        pi = self.gw.store.get("payment_intent", payment_intent_id, PaymentIntent)
        if not pi:
            raise NotFound("payment intent not found")
        problems = []
        if pi.status not in (IntentStatus.SUCCEEDED, IntentStatus.REQUIRES_CAPTURE):
            problems.append(f"payment status is {pi.status.value}")
        if pi.amount != o.price or pi.currency != o.currency:
            problems.append("amount/currency does not match offer")
        if pi.merchant_id != o.merchant_id:
            problems.append("payment was made to a different merchant")
        if problems:
            raise AcpError("payment does not satisfy offer: " + "; ".join(problems))
        with self.gw.store.tx():
            if self.gw.store.find_by_field("acp_order", "payment_intent_id", pi.id, Order):
                raise AcpError("payment intent already used for another order")
            o.status = "paid"
            self.gw.store.put("acp_offer", o, k1=o.session_id)
            order = Order(offer_id=o.id, session_id=s.id, payment_intent_id=pi.id, merchant_id=o.merchant_id,
                          amount=o.price, currency=o.currency)
            self.gw.store.put("acp_order", order, k1=o.id)
            s.history.append({"role": "merchant", "type": "order", "order_id": order.id, "at": now()})
            self._save(s)
        self.gw.events.emit("acp.order.confirmed", {"order": order.model_dump()})
        self._rate_buyer(pi, order)
        return {"order": order.model_dump(), "replayed": False}

    def _rate_buyer(self, pi: PaymentIntent, order: Order) -> None:
        """Leave signed feedback for the buyer agent if this merchant is registered."""
        from agentpay.trust.registry import Feedback, MerchantIdentity
        ident = self.gw.store.get("merchant_identity", pi.merchant_id, MerchantIdentity)
        if not ident or ident.public_key != self.public_key or self.gw.registry is None:
            return
        fb = Feedback(subject_agent_id=pi.agent_id, merchant_id=pi.merchant_id, payment_intent_id=pi.id,
                      score=95, tags=["paid_on_time", "acp"], signature="")
        fb.signature = self.key.sign(fb.payload())
        try:
            self.gw.registry.submit_feedback(fb)
        except GatewayError:
            pass

    def order(self, order_id: str) -> dict:
        o = self.gw.store.get("acp_order", order_id, Order)
        if not o:
            raise NotFound("order not found")
        return o.model_dump()
