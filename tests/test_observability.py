from agentpay.observability.ratelimit import RateLimiter


def test_token_bucket():
    t = [0.0]
    rl = RateLimiter(rate_per_s=1, burst=2, clock=lambda: t[0])
    assert rl.take("a")[0] and rl.take("a")[0]
    ok, retry = rl.take("a")
    assert not ok and 0 < retry <= 1
    assert rl.take("b")[0]  # keys are isolated
    t[0] = 1.0
    assert rl.take("a")[0]


def test_request_id_echo_and_error_body(admin):
    r = admin.get("/v1/payment_intents/pi_missing", headers={"X-Request-Id": "trace-123"})
    assert r.headers["X-Request-Id"] == "trace-123"
    assert r.json()["error"]["request_id"] == "trace-123"


def test_rate_limit_returns_429(gw, app):
    from fastapi.testclient import TestClient
    gw_app = app
    c = TestClient(gw_app)
    gw_app.user_middleware  # noqa: B018 - app built
    codes = [c.get("/healthz", headers={"Authorization": "Bearer flood"}).status_code for _ in range(80)]
    assert 429 in codes
    r = c.get("/healthz", headers={"Authorization": "Bearer flood"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
