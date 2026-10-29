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
