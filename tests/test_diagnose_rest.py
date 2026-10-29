import json

import httpx

from scripts.diagnose_gemini_rest import probe


def test_rest_probe_reports_error_and_redacts_key():
    key = "test-secret-must-not-appear"

    def handler(request):
        assert request.headers["x-goog-api-key"] == key
        assert "key" not in request.url.params
        assert set(json.loads(request.content)) == {"contents"}
        return httpx.Response(503, headers={"retry-after": "5", "x-request-id": "request-123"},
                              json={"error": {"code": 503, "message": "Overloaded " + key}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe(client, key)
    assert result["http_status"] == 503
    assert result["retry_after"] == "5"
    assert result["request_identifiers"] == {"x-request-id": "request-123"}
    assert key not in json.dumps(result)
    assert "Overloaded" in result["provider_error"]


def test_rest_probe_success_requires_text():
    for body, expected in (({"candidates": []}, "EMPTY_RESPONSE"),
                           ({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}, "PASS")):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
            assert probe(client, "dummy")["result"] == expected


def test_rest_probe_distinguishes_local_timeout_from_http_504():
    def handler(request):
        raise httpx.ReadTimeout("sensitive text", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = probe(client, "dummy")
    assert result["result"] == "TRANSPORT_ERROR"
    assert result["exception_type"] == "ReadTimeout"
    assert "http_status" not in result
    assert "sensitive text" not in json.dumps(result)
