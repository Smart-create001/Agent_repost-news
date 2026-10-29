from unittest.mock import patch

import pytest

from scripts.phase6_test import BoundedNotifier
from src.notifier import DeliveryError, TelegramNotifier


def test_bounded_notifier_refuses_second_text_and_audio(tmp_path):
    notifier = BoundedNotifier(token="dummy", chat_id="-1", max_retries=0)
    try:
        with patch.object(TelegramNotifier, "send_text", return_value=[123]) as send:
            assert notifier.send_text(5, "ข่าวทดสอบ") == [123]
            with pytest.raises(DeliveryError, match="limit"):
                notifier.send_text(5, "ข่าวซ้ำ")
            assert send.call_count == 1
        with patch.object(TelegramNotifier, "send_audio", return_value=124) as send:
            assert notifier.send_audio(5, tmp_path / "x.mp3") == 124
            with pytest.raises(DeliveryError, match="limit"):
                notifier.send_audio(5, tmp_path / "x.mp3")
            assert send.call_count == 1
    finally:
        notifier.close()


def test_bounded_notifier_rejects_long_message_without_sending():
    notifier = BoundedNotifier(token="dummy", chat_id="-1", max_retries=0)
    try:
        with patch.object(TelegramNotifier, "send_text") as send:
            with pytest.raises(DeliveryError, match="limit"):
                notifier.send_text(6, "ข่าว" * 2000)
            send.assert_not_called()
    finally:
        notifier.close()


def test_text_only_uses_alert_and_limits_ai_without_audio(monkeypatch):
    import sys
    from pathlib import Path
    from scripts import phase6_test
    from src.config import load_config

    config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    monkeypatch.setattr(sys, "argv", ["phase6_test", "--text-only"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1")
    monkeypatch.setattr(phase6_test, "load_config", lambda: config)
    monkeypatch.setattr(phase6_test, "validate_live_config", lambda _: None)

    def check_run(settings, *, mode, state_path, notifier):
        assert mode == "alert"
        assert settings.app.max_posts_per_topic == 1
        assert [t.id for t in settings.topics if t.enabled] == ["ai_tech"]
        assert not settings.alert.generate_audio
        return 0

    monkeypatch.setattr(phase6_test, "run", check_run)
    assert phase6_test.main() == 0
