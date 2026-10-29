"""Bounded live Phase 6 briefing and read-only production state verification."""
import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path

from src.config import load_config, validate_live_config
from src.main import run
from src.notifier import DeliveryError, TelegramNotifier
from src.sanitizer import split_telegram
from src.state import load_state


class BoundedNotifier(TelegramNotifier):
    """Refuse repeated requests and messages requiring multiple Telegram chunks."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text_attempted = set()
        self.audio_attempted = set()

    def send_text(self, thread_id, text):
        text = "[TEST — ทดสอบ Phase 6]\n" + text
        if thread_id in self.text_attempted or len(split_telegram(text)) != 1:
            raise DeliveryError("Phase 6 test text delivery limit exceeded")
        self.text_attempted.add(thread_id)
        return super().send_text(thread_id, text)

    def send_audio(self, thread_id, path):
        if thread_id in self.audio_attempted:
            raise DeliveryError("Phase 6 test audio delivery limit exceeded")
        self.audio_attempted.add(thread_id)
        return super().send_audio(thread_id, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-state", action="store_true")
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args()
    state_path = Path("seen_posts.json")
    if args.verify_state:
        config = load_config()
        if config.features.audio_enabled or "edge_tts" in sys.modules:
            raise RuntimeError("Text-only execution unexpectedly enabled or imported audio")
        state = load_state(state_path)
        digest = hashlib.sha256(state_path.read_bytes()).hexdigest()
        print(f"state_sha256={digest} topics={len(state['topics'])} audio_enabled=false edge_tts_imported=false")
        return 0
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config().model_copy(deep=True)
    config.app.max_posts_per_topic = 1
    if args.text_only:
        for topic in config.topics:
            topic.enabled = topic.enabled and topic.id == "ai_tech"
    validate_live_config(config)
    notifier = BoundedNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                               chat_id=os.environ["TELEGRAM_CHAT_ID"], max_retries=0)
    try:
        return run(config, mode="alert" if args.text_only else "briefing", state_path=state_path, notifier=notifier)
    finally:
        notifier.close()


if __name__ == "__main__":
    raise SystemExit(main())
