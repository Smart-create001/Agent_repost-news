"""Bounded live sample: one actual RSS alert per topic, isolated test state."""
import logging
import os
from pathlib import Path
from src.config import load_config, validate_live_config
from src.main import run
from src.notifier import DeliveryError, TelegramNotifier
from src.sanitizer import split_telegram

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
config = load_config()
config.app.max_posts_per_topic = 1
config.app.max_retries = 0
config.app.request_timeout_seconds = 60
validate_live_config(config)
state = Path('diagnostic-output/two-topic-state.json')
state.parent.mkdir(exist_ok=True)
if state.exists():
    raise SystemExit('Refusing to repeat an existing diagnostic run')

class BoundedNotifier(TelegramNotifier):
    sent = set()
    def send_text(self, thread_id, text):
        if thread_id in self.sent or len(split_telegram(text)) != 1:
            raise DeliveryError('sample limit exceeded')
        ids = super().send_text(thread_id, '[TEST — ตัวอย่างโครงสร้างข่าว]\n' + text)
        self.sent.add(thread_id)
        print(f'TELEGRAM_CONFIRMED thread={thread_id} message_ids={ids}', flush=True)
        return ids

notifier = BoundedNotifier(token=os.environ['TELEGRAM_BOT_TOKEN'], chat_id=os.environ['TELEGRAM_CHAT_ID'], max_retries=0)
try:
    code = run(config, mode='alert', state_path=state, notifier=notifier)
    expected = {t.telegram.message_thread_id for t in config.topics if t.enabled}
    print(f'SENT_THREADS={sorted(notifier.sent)} EXPECTED_THREADS={sorted(expected)} EXIT={code}', flush=True)
    if code or notifier.sent != expected:
        raise SystemExit(1)
finally:
    notifier.close()
