"""Direct REST diagnostics using synthetic input; never log request headers."""

import json
import os
import re
import time

import httpx

MODEL = os.environ.get("DIAGNOSTIC_MODEL", "gemini-3.8-flash")
if MODEL not in {"gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"}:
    raise ValueError("Unsupported diagnostic model")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def safe_text(value, key):
    text = str(value).replace(key, "[REDACTED]") if key else str(value)
    text = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"https?://\S+", "[URL]", text)
    return " ".join(text.split())[:2500]


def probe(client, key):
    started = time.monotonic()
    try:
        response = client.post(URL, headers={"x-goog-api-key": key},
                               json={"contents": [{"parts": [{"text": "Reply with OK."}]}]},
                               timeout=45)
    except httpx.TransportError as exc:
        return {"result": "TRANSPORT_ERROR", "exception_type": type(exc).__name__,
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    result = {"http_status": response.status_code,
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "retry_after": safe_text(response.headers.get("retry-after", "ABSENT"), key),
              "request_identifiers": {
                  name: safe_text(response.headers[name], key)
                  for name in ("x-request-id", "x-goog-request-id", "x-guploader-uploadid",
                               "traceparent", "x-cloud-trace-context") if name in response.headers}}
    try:
        body = response.json()
    except ValueError:
        result["result"] = "INVALID_JSON"
        return result
    if response.is_success:
        candidates = body.get("candidates", []) if isinstance(body, dict) else []
        text = "".join(part.get("text", "") for candidate in candidates
                       for part in candidate.get("content", {}).get("parts", []))
        result.update(result="PASS" if text.strip() else "EMPTY_RESPONSE",
                      text_present=bool(text.strip()),
                      reply_is_ok=text.strip().strip(".!").upper() == "OK")
    else:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        result.update(result="API_ERROR", provider_error=safe_text(json.dumps(error, ensure_ascii=False), key))
    return result


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("BLOCKED: GEMINI_API_KEY is missing", flush=True)
        return 1
    print(f"REST diagnostic model={MODEL}; no SDK, schema, Telegram, or state writes", flush=True)
    with httpx.Client(follow_redirects=False) as client:
        for attempt in range(1, 3):
            result = probe(client, key)
            print(json.dumps({"attempt": attempt, **result}, ensure_ascii=False), flush=True)
            if result.get("result") == "PASS":
                return 0
            if attempt == 2 or result.get("http_status") not in (503, 504):
                return 1
            retry_after = result.get("retry_after", "ABSENT")
            if retry_after != "ABSENT" and (not retry_after.isdigit() or int(retry_after) > 10):
                print("Retry deferred to respect provider Retry-After", flush=True)
                return 1
            time.sleep(max(3, int(retry_after)) if retry_after.isdigit() else 3)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
