"""Read-only production AI probe. Never send Telegram or persist state."""
from datetime import timedelta
import os
from types import SimpleNamespace
from google import genai
from google.genai import types
from src.brain import analyze_topic, BrainError
from src.config import load_config
from src.scraper import fetch_topic
from src.state import load_state

config = load_config()
topic = next(t for t in config.topics if t.id == 'ai_tech')
state = load_state('seen_posts.json')
posts, errors = fetch_topic(topic, timeout=config.app.request_timeout_seconds,
                            max_retries=config.app.max_retries, max_age=timedelta(days=2))
known = state['topics'].get(topic.id, {}).get('posts', {})
all_posts = posts
posts = [p for p in posts if not known.get(p.id, {}).get('alert', {}).get('delivered')
         and not known.get(p.id, {}).get('alert', {}).get('uncertain')]
if not posts:
    print('No pending posts; using current RSS items only for analysis, no delivery.', flush=True)
    posts = all_posts
posts = posts[:config.app.max_posts_per_topic]
print(f'candidate_count={len(posts)} feed_error_count={len(errors)}', flush=True)
if errors or not posts:
    raise SystemExit('No usable candidates or RSS error; test inconclusive')
client = genai.Client(api_key=os.environ['GEMINI_API_KEY'],
                      http_options=types.HttpOptions(timeout=config.app.request_timeout_seconds * 1000))
class Capture:
    def generate_content(self, **kwargs):
        response = client.models.generate_content(**kwargs)
        for candidate in response.candidates or []:
            print(f'finish_reason={candidate.finish_reason}', flush=True)
        usage = response.usage_metadata
        if usage:
            print(f'output_tokens={usage.candidates_token_count}', flush=True)
        return response

for budget in (1024, 8192):
    print(f'output_budget={budget}', flush=True)
    try:
        result = analyze_topic(topic, posts, client=SimpleNamespace(models=Capture()),
                               model=config.app.default_model, threshold=config.alert.relevance_threshold,
                               max_retries=0, max_output_tokens=budget)
        print(f'result=PASS relevant_count={len(result)}', flush=True)
        if budget == 1024:
            break
    except BrainError as exc:
        print(f'result=FAIL reason={exc} cause_type={type(exc.__cause__).__name__}', flush=True)
