"""Live editorial probe: public RSS + Gemini, no Telegram or production state."""
import json
import os
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from google import genai
from google.genai import types
from src.brain import analyze_topic
from src.config import load_config
from src.scraper import fetch_topic

config = load_config()
results = []
for topic in config.topics:
    if not topic.enabled:
        continue
    source = None
    captured = []
    try:
        posts, errors = fetch_topic(topic, timeout=20, max_retries=0, max_age=timedelta(days=2))
        if not posts:
            results.append({"topic": topic.id, "status": "NO_FEED_POSTS", "feed_error_count": len(errors)})
            continue
        selected = posts[:5]
        source = [{"title": post.title, "description": post.description,
                   "url": post.url, "date_inferred": post.date_inferred,
                   "published_at": None if post.date_inferred else post.published_at.isoformat()}
                  for post in selected]
        with genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                          http_options=types.HttpOptions(timeout=60000)) as sdk:
            def capture(**kwargs):
                response = sdk.models.generate_content(**kwargs)
                captured.append((response.text or "")[:30000])
                return response
            client = SimpleNamespace(models=SimpleNamespace(generate_content=capture))
            articles = analyze_topic(topic, selected, client=client,
                                     model=config.app.default_model, threshold=0,
                                     max_retries=1, timeout_seconds=60,
                                     max_output_tokens=config.app.max_output_tokens)
        results.append({"topic": topic.id, "status": "VALIDATED" if articles else "NOT_RELEVANT",
                        "source": source,
                        "articles": [a.model_dump(mode="json") for a in articles],
                        "feed_error_count": len(errors)})
    except Exception as exc:
        results.append({"topic": topic.id, "status": "FAIL", "error_type": type(exc).__name__,
                        "error": str(exc) if type(exc).__name__ == "BrainError" else "redacted",
                        "source": source, "rejected_response": captured[-1] if captured else None})
Path("editorial-review").mkdir(exist_ok=True)
Path("editorial-review/results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
for result in results:
    print(f"topic={result['topic']} status={result['status']} feed_errors={result.get('feed_error_count', 'unknown')}")
if any(result["status"] != "VALIDATED" for result in results):
    raise SystemExit(1)
