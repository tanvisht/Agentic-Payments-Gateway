import secrets
import time

import pytest

from agentpay.core.crypto import KeyPair
from agentpay.core.errors import MandateViolation, SignatureError, ValidationFailed
from agentpay.core.models import MandateScope
from agentpay.mandates.service import mandate_signing_payload


def _register(gw, principal, pkp, agent, *, signer=None, nonce=None, **scope_kw):
    scope = MandateScope(**{"currency": "USD", "max_amount_per_txn": 1000, "max_total_amount": 3000, **scope_kw})
    nb, exp, nonce = int(time.time()) - 1, int(time.time()) + 3600, nonce or secrets.token_hex(8)
    sig = (signer or pkp).sign(mandate_signing_payload(principal_id=principal.id, agent_id=agent.id, scope=scope,
                                                       nonce=nonce, not_before=nb, expires_at=exp))
    return gw.mandates.register(principal=principal, agent=agent, scope=scope, nonce=nonce,
                                not_before=nb, expires_at=exp, signature=sig)


def test_forged_signature_rejected(gw, world):
    with pytest.raises(SignatureError):
        _register(gw, world["principal"], world["pkp"], world["agent"], signer=KeyPair.generate())


def test_nonce_replay_rejected(gw, world):
    _register(gw, world["principal"], world["pkp"], world["agent"], nonce="fixednonce1")
    with pytest.raises(SignatureError):
        _register(gw, world["principal"], world["pkp"], world["agent"], nonce="fixednonce1")


def test_tampered_scope_rejected(gw, world):
    p, a, kp = world["principal"], world["agent"], world["pkp"]
    scope = MandateScope(currency="USD", max_amount_per_txn=1000, max_total_amount=3000)
    nb, exp = int(time.time()), int(time.time()) + 60
    sig = kp.sign(mandate_signing_payload(principal_id=p.id, agent_id=a.id, scope=scope, nonce="n" * 8, not_before=nb, expires_at=exp))
    scope.max_total_amount = 10**9  # agent tries to raise its own budget
    with pytest.raises(SignatureError):
        gw.mandates.register(principal=p, agent=a, scope=scope, nonce="n" * 8, not_before=nb, expires_at=exp, signature=sig)


@pytest.mark.parametrize("kw", [{"max_amount_per_txn": 0}, {"max_amount_per_txn": 5000}, {"currency": "ZZZ"}])
def test_invalid_scopes(gw, world, kw):
    with pytest.raises((ValidationFailed, ValueError)):
        _register(gw, world["principal"], world["pkp"], world["agent"], **kw)


@pytest.mark.parametrize("kw,rule", [
    ({"amount": 1001}, "per_txn_limit"), ({"currency": "EUR"}, "currency"),
    ({"merchant_id": "evil.com"}, "merchant"), ({"category": "jewelry"}, "category"),
    ({"agent_id": "agt_other"}, "agent_mismatch"),
])
def test_check_rules(gw, world, kw, rule):
    m = _register(gw, world["principal"], world["pkp"], world["agent"], merchants_allowed=["nike.com"], categories_allowed=["running"])
    args = {"agent_id": world["agent"].id, "amount": 500, "currency": "USD", "merchant_id": "nike.com", "category": "running", **kw}
    with pytest.raises(MandateViolation) as e:
        gw.mandates.check(m, **args)
    assert e.value.details["rule"] == rule


def test_expired_and_revoked(gw, world):
    m = _register(gw, world["principal"], world["pkp"], world["agent"])
    base = dict(agent_id=world["agent"].id, amount=10, currency="USD", merchant_id="x", category=None)
    with pytest.raises(MandateViolation):
        gw.mandates.check(m, **base, at=m.expires_at)
    gw.mandates.revoke(m.id)
    with pytest.raises(MandateViolation):
        gw.mandates.check(gw.mandates.get(m.id), **base)


def test_budget_reservation_and_release(gw, world):
    m = _register(gw, world["principal"], world["pkp"], world["agent"])
    with gw.store.tx():
        gw.mandates.reserve(m.id, 1000)
        gw.mandates.reserve(m.id, 1000)
        gw.mandates.reserve(m.id, 1000)
        with pytest.raises(MandateViolation):
            gw.mandates.reserve(m.id, 1)
    gw.mandates.release(m.id, 1000)
    assert gw.mandates.get(m.id).remaining() == 1000
