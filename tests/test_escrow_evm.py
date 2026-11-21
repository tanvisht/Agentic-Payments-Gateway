"""Executes the real compiled AgentEscrow bytecode on an in-process EVM (py-evm)."""

import pytest

web3 = pytest.importorskip("web3")
pytest.importorskip("eth_tester")

from agentpay.chain.evm import EvmChain  # noqa: E402
from agentpay.providers.base import AuthorizeRequest, ProviderStatus  # noqa: E402
from agentpay.providers.escrow import EscrowProvider  # noqa: E402

USDC = 10**6


@pytest.fixture(scope="module")
def chain_env():
    chain = EvmChain.in_memory()
    acct = chain.accounts
    token = chain.deploy("TestUSDC")
    escrow = chain.deploy("AgentEscrow")
    payer, merchant, stranger = acct[1], acct[2], acct[3]
    chain.send(token.functions.mint(payer, 10_000 * USDC), chain.operator)
    return chain, token, escrow, payer, merchant, stranger


def bal(token, a):
    return token.functions.balanceOf(a).call()


def test_lock_partial_release_returns_remainder(chain_env):
    chain, token, escrow, payer, merchant, _ = chain_env
    p0, m0 = bal(token, payer), bal(token, merchant)
    eid = b"\x01" * 32
    chain.send(token.functions.approve(escrow.address, 100 * USDC), payer)
    chain.send(escrow.functions.lock(eid, merchant, token.address, 100 * USDC, chain.now() + 3600, chain.operator), payer)
    assert bal(token, escrow.address) >= 100 * USDC
    chain.send(escrow.functions.release(eid, 60 * USDC), chain.operator)
    assert bal(token, merchant) - m0 == 60 * USDC
    assert p0 - bal(token, payer) == 60 * USDC
    with pytest.raises(Exception):
        chain.send(escrow.functions.release(eid, 1), chain.operator)  # already released


def test_only_parties_can_act_before_deadline_anyone_after(chain_env):
    chain, token, escrow, payer, merchant, stranger = chain_env
    eid = b"\x02" * 32
    chain.send(token.functions.approve(escrow.address, 5 * USDC), payer)
    chain.send(escrow.functions.lock(eid, merchant, token.address, 5 * USDC, chain.now() + 100, chain.operator), payer)
    with pytest.raises(Exception):
        chain.send(escrow.functions.release(eid, 5 * USDC), stranger)
    with pytest.raises(Exception):
        chain.send(escrow.functions.release(eid, 5 * USDC), merchant)  # payee cannot pay itself
    with pytest.raises(Exception):
        chain.send(escrow.functions.refund(eid), stranger)
    chain.advance_time(200)
    p0 = bal(token, payer)
    chain.send(escrow.functions.refund(eid), stranger)  # deadline passed: anyone may unstick funds
    assert bal(token, payer) - p0 == 5 * USDC


@pytest.mark.parametrize("args", [
    lambda c, t, m: (b"\x03" * 32, m, t.address, 0, c.now() + 10),          # zero amount
    lambda c, t, m: (b"\x04" * 32, m, t.address, 1, c.now() - 1),           # past deadline
    lambda c, t, m: (b"\x01" * 32, m, t.address, 1, c.now() + 10),          # duplicate id
])
def test_lock_rejections(chain_env, args):
    chain, token, escrow, payer, merchant, _ = chain_env
    chain.send(token.functions.approve(escrow.address, 10 * USDC), payer)
    with pytest.raises(Exception):
        chain.send(escrow.functions.lock(*args(chain, token, merchant), chain.operator), payer)


def test_lock_without_allowance_reverts(chain_env):
    chain, token, escrow, payer, merchant, _ = chain_env
    chain.send(token.functions.approve(escrow.address, 0), payer)
    with pytest.raises(Exception):
        chain.send(escrow.functions.lock(b"\x05" * 32, merchant, token.address, USDC, chain.now() + 10, chain.operator), payer)


def test_escrow_provider_through_gateway(chain_env):
    """Full gateway flow on the escrow rail: authorize(lock) → partial capture(release) and cancel(refund)."""
    from agentpay.agent.demo import bootstrap, in_process_client
    from agentpay.api.app import create_app
    from agentpay.config import Settings
    from agentpay.gateway import Gateway
    import secrets

    chain, token, escrow, payer, merchant, _ = chain_env
    prov = EscrowProvider(chain, escrow, token, merchant_addresses={"stride.run": merchant})
    gw = Gateway.build(Settings(admin_key="x", async_webhooks=False, default_provider="escrow"), providers=[prov])
    app = create_app(gw)
    _, _, _, akp, key, mandate = bootstrap(gw, merchants=["stride.run"])
    c = in_process_client(app, key, akp)
    m0 = bal(token, merchant)
    pi = c.create_payment(idempotency_key=secrets.token_hex(6), mandate_id=mandate.id, amount=12900, currency="USD",
                          merchant_id="stride.run", category="running", payment_method=f"escrow:{payer}",
                          capture_method="manual")
    assert pi["status"] == "requires_capture" and pi["provider"] == "escrow"
    assert prov.retrieve(pi["provider_ref"]).status is ProviderStatus.AUTHORIZED
    cap = c.request("POST", f"/v1/payment_intents/{pi['id']}/capture", {"amount": 10000})
    assert cap["status"] == "succeeded"
    assert bal(token, merchant) - m0 == 10000 * 10**4  # $100.00 in 6dp token units

    pi2 = c.create_payment(idempotency_key=secrets.token_hex(6), mandate_id=mandate.id, amount=500, currency="USD",
                           merchant_id="stride.run", category="running", payment_method=f"escrow:{payer}",
                           capture_method="manual")
    assert c.request("POST", f"/v1/payment_intents/{pi2['id']}/cancel")["status"] == "canceled"
    assert prov.retrieve(pi2["provider_ref"]).status is ProviderStatus.CANCELED
    assert gw.ledger.trial_balance() == {"USD": 0}


def test_escrow_insufficient_balance_is_a_decline(chain_env):
    chain, token, escrow, _, merchant, stranger = chain_env
    prov = EscrowProvider(chain, escrow, token, merchant_addresses={"m": merchant})
    from agentpay.core.errors import CardDeclined
    with pytest.raises(CardDeclined):
        prov.authorize(AuthorizeRequest("pi", 100, "USD", f"escrow:{stranger}", True, "k-broke", merchant_id="m"))
