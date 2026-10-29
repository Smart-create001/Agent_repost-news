"""Authorized live smoke test with separate state and labeled Telegram messages."""

import hashlib
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from src.brain import analyze_topic
from src.config import load_config, validate_live_config
from src.main import run
from src.notifier import TelegramNotifier


def main():
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config().model_copy(deep=True)
    config.app.default_model = "gemini-3.1-flash-lite"
    config.app.request_timeout_seconds = 60
    config.app.max_retries = 0
    config.app.max_posts_per_topic = 1
    validate_live_config(config)
    original = Path("seen_posts.json").read_bytes()
    state_path = Path("diagnostic-output/state.json")
    state_path.parent.mkdir(exist_ok=True)
    if state_path.exists():
        raise ValueError("Diagnostic state already exists; refusing a blind repeat")

    def analyze(topic, posts, **kwargs):
        return analyze_topic(topic, posts, max_output_tokens=4096, **kwargs)

    print("STAGE SDK_SCHEMA: starting synthetic input, no Telegram", flush=True)
    result = analyze(
        SimpleNamespace(id="diagnostic", name="ทดสอบ", keywords=["ทดสอบ"],
                        context="สรุปข้อมูลทดสอบที่ได้รับเป็นภาษาไทย โดยระบุว่าเป็นข้อมูลสมมติ"),
        [SimpleNamespace(title="ข้อมูลสมมติสำหรับทดสอบ: ระบบส่งข่าวภาษาไทยทำงานผ่าน Gemini API",
                         url="https://example.org/diagnostic")],
        api_key=os.environ["GEMINI_API_KEY"], model=config.app.default_model,
        threshold=0, max_retries=0, timeout_seconds=60)
    print(f"STAGE SDK_SCHEMA: PASS validated_results={len(result)}", flush=True)

    class LabeledNotifier(TelegramNotifier):
        sent_threads = set()

        def send_text(self, thread_id, text):
            ids = super().send_text(thread_id, "[TEST — ทดสอบระบบ ไม่ใช่การเผยแพร่ production]\n" + text)
            self.sent_threads.add(thread_id)
            print(f"TELEGRAM_CONFIRMED thread={thread_id} message_ids={ids}", flush=True)
            return ids

    notifier = LabeledNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                               chat_id=os.environ["TELEGRAM_CHAT_ID"], max_retries=0)
    try:
        print("STAGE PIPELINE: real RSS, max 1 candidate/topic, separate state", flush=True)
        exit_code = run(config, mode="alert", state_path=state_path,
                        analyze=analyze, notifier=notifier)
        print(f"STAGE PIPELINE: exit_code={exit_code}", flush=True)
        # A no-news run makes no delivery. Verify routing explicitly only for
        # topics without any attempted pipeline send (including uncertain ones).
        import json
        state = json.loads(state_path.read_text()) if state_path.exists() else {"topics": {}}
        routing_failed = False
        for topic in config.topics:
            if not topic.enabled or topic.telegram.message_thread_id in notifier.sent_threads:
                continue
            if state["topics"].get(topic.id, {}).get("posts"):
                print(f"ROUTING_CHECK topic={topic.id}: SKIPPED prior delivery attempt requires inspection", flush=True)
                continue
            try:
                notifier.send_text(topic.telegram.message_thread_id,
                                   f"ทดสอบการเชื่อมต่อ Telegram สำหรับ {topic.id}\n"
                                   "รอบนี้ไม่มีข่าวที่ผ่านการคัดกรอง จึงส่งเฉพาะข้อความทดสอบปลายทาง")
            except Exception as exc:
                routing_failed = True
                print(f"ROUTING_CHECK topic={topic.id}: FAIL type={type(exc).__name__}", flush=True)
        return 1 if exit_code or routing_failed else 0
    finally:
        notifier.close()
        unchanged = hashlib.sha256(original).digest() == hashlib.sha256(Path("seen_posts.json").read_bytes()).digest()
        print(f"PRODUCTION_STATE_UNCHANGED={unchanged}", flush=True)
        if not unchanged:
            raise RuntimeError("Production state unexpectedly changed")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LIVE_TEST_FAILED type={type(exc).__name__}", flush=True)
        raise SystemExit(1)
