"""Live editorial probe: public RSS + Gemini, no Telegram or production state."""
import json
import os
from pathlib import Path
from src.brain import analyze_topic
from src.config import load_config
from src.scraper import fetch_topic

config = load_config()
results = []
for topic in config.topics:
    if not topic.enabled:
        continue
    try:
        posts, errors = fetch_topic(topic, timeout=20, max_retries=0)
        if not posts:
            results.append({"topic": topic.id, "status": "NO_FEED_POSTS", "feed_error_count": len(errors)})
            continue
        post = next((p for p in posts if p.description), posts[0])
        articles = analyze_topic(topic, [post], api_key=os.environ.get("GEMINI_API_KEY"),
                                 model=config.app.default_model, threshold=0,
                                 max_retries=0, timeout_seconds=60, max_output_tokens=4096)
        results.append({"topic": topic.id, "status": "VALIDATED" if articles else "NOT_RELEVANT",
                        "source": {"title": post.title, "description": post.description,
                                   "url": post.url, "date_inferred": post.date_inferred},
                        "articles": [a.model_dump(mode="json") for a in articles],
                        "feed_error_count": len(errors)})
    except Exception as exc:
        results.append({"topic": topic.id, "status": "FAIL", "error_type": type(exc).__name__,
                        "error": str(exc) if type(exc).__name__ == "BrainError" else "redacted"})
Path("editorial-review").mkdir(exist_ok=True)
Path("editorial-review/results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
for result in results:
    print(f"topic={result['topic']} status={result['status']} feed_errors={result.get('feed_error_count', 'unknown')}")
if any(result["status"] == "FAIL" for result in results):
    raise SystemExit(1)
