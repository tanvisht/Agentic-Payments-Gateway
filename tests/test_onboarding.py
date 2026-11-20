from agentpay.core.crypto import KeyPair


def test_principal_card_onboarding_flow(admin, gw):
    p = admin.post("/v1/principals", json={"name": "P", "public_key": KeyPair.generate().public_b64, "email": "p@x.co"}).json()
    assert p["default_payment_method"] is None
    s = admin.post(f"/v1/principals/{p['id']}/payment_method_setup", json={}).json()
    assert s["client_secret"].startswith("seti_") and s["customer"].startswith("cus_")
    early = admin.post(f"/v1/principals/{p['id']}/payment_method_setup/complete", json={})
    assert early.status_code == 422  # browser hasn't confirmed yet
    gw.router.providers["mock"].complete_setup(s["setup_intent"], "pm_card_visa")
    done = admin.post(f"/v1/principals/{p['id']}/payment_method_setup/complete", json={}).json()
    assert done["default_payment_method"] == "pm_card_visa"
    assert done["provider_customers"]["mock"] == s["customer"]


def test_customer_passed_to_provider(world, gw):
    from tests.conftest import pay
    p = gw.get_principal(world["principal"].id)
    p.provider_customers = {"mock": "cus_mock_9"}
    gw.store.put("principal", p)
    captured = {}
    prov = gw.router.providers["mock"]
    orig = prov.authorize
    prov.authorize = lambda req: (captured.setdefault("meta", req.metadata), orig(req))[1]
    pay(world)
    assert captured["meta"]["stripe_customer"] == "cus_mock_9"
