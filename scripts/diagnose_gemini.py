"""Probe Gemini without Telegram, state changes, or raw error logging."""
import logging
import os
import re
from itertools import islice
from types import SimpleNamespace
from google import genai
from google.genai import types
from src.brain import Analysis, _error_category, analyze_topic
from src.config import load_config

logging.basicConfig(level=logging.WARNING)
key = os.environ.get("GEMINI_API_KEY")
if not key:
    raise SystemExit("BLOCKED: GEMINI_API_KEY is missing")
model = load_config().app.default_model
print(f"Model: {model}", flush=True)
with genai.Client(api_key=key, http_options=types.HttpOptions(timeout=20000)) as client:
    try:
        available = []
        for item in islice(client.models.list(config={"page_size": 100}), 100):
            name = item.name or ""
            if re.fullmatch(r"models/[A-Za-z0-9._-]+", name):
                actions = item.supported_actions or []
                if "generateContent" in actions:
                    available.append(name)
        print("list_models: HTTP_SUCCESS", flush=True)
        print(f"configured_model_available={('models/' + model) in available}", flush=True)
        print("generate_content_models=" + ",".join(available), flush=True)
    except Exception as exc:
        code = getattr(exc, "code", None)
        code = code if isinstance(code, int) else "unknown"
        print(f"list_models: FAIL http_status={code} category={_error_category(exc)}", flush=True)
    for label, schema in (("minimal_no_schema", None), ("original_schema", list[Analysis])):
        try:
            response = client.models.generate_content(
                model=model, contents="Return an empty JSON array: []",
                config=types.GenerateContentConfig(response_mime_type="application/json",
                    response_schema=schema, max_output_tokens=1024, temperature=0))
            print(f"{label}: HTTP_SUCCESS text_present={bool(response.text)}", flush=True)
        except Exception as exc:
            code = getattr(exc, "code", None)
            code = code if isinstance(code, int) else "unknown"
            print(f"{label}: FAIL http_status={code} category={_error_category(exc)}", flush=True)
            message = str(getattr(exc, "message", "") or "").lower()
            if "additional_properties" in message or "additionalproperties" in message:
                print(f"{label}: provider_mentions_additional_properties=true", flush=True)
    def probe(**kwargs):
        response = client.models.generate_content(**kwargs)
        print("fixed_production_schema: HTTP_SUCCESS", flush=True)
        return response
    try:
        analyze_topic(SimpleNamespace(id="diagnostic", name="Diagnostic", context="ทดสอบการเชื่อมต่อ", keywords=[]),
            [SimpleNamespace(title="ข่าวทดสอบ", url="https://example.org/diagnostic")],
            client=SimpleNamespace(models=SimpleNamespace(generate_content=probe)), model=model, max_retries=0)
        print("fixed_production_schema: LOCAL_VALIDATION_PASS", flush=True)
    except Exception as exc:
        print(f"fixed_production_schema: LOCAL_OR_API_FAILURE type={type(exc).__name__}", flush=True)
