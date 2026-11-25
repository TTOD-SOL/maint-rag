from tests.conftest import FakeLLM


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["index_loaded"] is True and body["chunks"] == 2
    assert "X-Request-ID" in r.headers


def test_health_degraded_without_index():
    from fastapi.testclient import TestClient

    from app.main import create_app

    # no `with` block, so lifespan (which would load the real index) doesn't run
    r = TestClient(create_app(retriever=None, llm=FakeLLM())).get("/health")
    assert r.status_code == 503 and r.json()["index_loaded"] is False


def test_ask_requires_key(client):
    assert client.post("/ask", json={"question": "zero energy?"}).status_code == 401
    r = client.post("/ask", json={"question": "zero energy?"}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_ask_with_key(client):
    r = client.post("/ask", json={"question": "How do I verify zero energy?"}, headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["citations"][0]["n"] == 1
    assert body["grounded"] is True
    for f in ("answer", "retrieval_ms", "llm_ms", "input_tokens", "output_tokens", "est_cost_usd", "request_id"):
        assert f in body


def test_ask_503_when_key_not_configured(client, monkeypatch):
    monkeypatch.delenv("APP_API_KEY")
    assert client.post("/ask", json={"question": "zero energy?"}, headers={"X-API-Key": "x"}).status_code == 503


def test_input_validation(client):
    h = {"X-API-Key": "test-key"}
    assert client.post("/ask", json={"question": "x" * 501}, headers=h).status_code == 422
    assert client.post("/ask", json={"question": "  "}, headers=h).status_code == 422
    assert client.post("/ask", json={}, headers=h).status_code == 422
    assert client.post("/ask", json={"question": "valid question", "k": 0}, headers=h).status_code == 422
    assert client.post("/ask", json={"question": "valid question", "k": 11}, headers=h).status_code == 422
    assert client.post("/demo/ask", json={"question": "x" * 501}).status_code == 422


def test_demo_rate_limit_per_ip(client):
    ip = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(10):
        assert client.post("/demo/ask", json={"question": "zero energy?"}, headers=ip).status_code == 200
    r = client.post("/demo/ask", json={"question": "zero energy?"}, headers=ip)
    assert r.status_code == 429
    # a different IP is unaffected
    assert client.post("/demo/ask", json={"question": "zero energy?"},
                       headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 200


def test_demo_daily_cap(make_client):
    with make_client(daily_cap=2) as c:
        for i in range(2):
            assert c.post("/demo/ask", json={"question": "zero energy?"},
                          headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code == 200
        r = c.post("/demo/ask", json={"question": "zero energy?"}, headers={"X-Forwarded-For": "198.51.100.9"})
        assert r.status_code == 429 and "daily" in r.json()["detail"]


def test_llm_error_maps_to_502(make_client):
    from rag.llm import LLMError

    class Failing(FakeLLM):
        def complete(self, *a, **kw):
            raise LLMError("boom", status=500)

    with make_client(llm=Failing()) as c:
        r = c.post("/ask", json={"question": "zero energy?"}, headers={"X-API-Key": "test-key"})
        assert r.status_code == 502


def test_metrics_exposed(client):
    client.post("/ask", json={"question": "zero energy?"}, headers={"X-API-Key": "test-key"})
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "rag_llm_tokens_total" in r.text and "http_requests_total" in r.text


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_api_key_not_logged(client, capsys):
    client.post("/ask", json={"question": "zero energy?"}, headers={"X-API-Key": "test-key"})
    out = capsys.readouterr().out
    assert "test-key" not in out
