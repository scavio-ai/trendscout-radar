import httpx
import pytest

from trendscout.client import ScavioClient, ScavioError


def make(handler, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return ScavioClient("k", transport=httpx.MockTransport(handler), sleep=sleeps.append), sleeps


def test_sends_attribution_header_and_tracks_credits():
    seen = {}

    def h(req):
        seen["src"] = req.headers["x-client-source"]
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"data": {}, "credits_used": 2, "credits_remaining": 98})

    c, _ = make(h)
    c.post("/x", {})
    c.post("/x", {})
    assert seen == {"src": "trendscout", "auth": "Bearer k"}
    assert c.credits_used == 4 and c.credits_remaining == 98


def test_retries_429_honoring_retry_after():
    responses = iter([httpx.Response(429, headers={"retry-after": "3"}, json={"error": "busy"}), httpx.Response(200, json={"ok": 1})])
    c, sleeps = make(lambda req: next(responses))
    assert c.post("/x", {}) == {"ok": 1}
    assert sleeps == [3.0]


def test_gives_up_after_max_retries():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(502, json={"error": "upstream"})

    c, sleeps = make(h)
    with pytest.raises(ScavioError) as e:
        c.post("/x", {})
    assert e.value.status == 502 and not e.value.is_auth
    assert len(calls) == 4 and len(sleeps) == 3


@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_and_credit_errors_do_not_retry(status):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(status, json={"error": "Insufficient credits"})

    c, _ = make(h)
    with pytest.raises(ScavioError) as e:
        c.post("/x", {})
    assert e.value.is_auth and "Insufficient credits" in str(e.value)
    assert len(calls) == 1


def test_usage_is_a_get():
    c, _ = make(lambda req: httpx.Response(200, json={"plan": "free", "credit_balance": 50}) if req.method == "GET" else httpx.Response(405))
    assert c.usage()["credit_balance"] == 50
