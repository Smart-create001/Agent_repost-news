import asyncio
from pathlib import Path

import httpx
import pytest

from src.notifier import DeliveryError, TelegramNotifier, deliver, make_audio


def fake_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(request):
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 77,
                                                       "message_thread_id": int(request.content.decode().split("message_thread_id=")[1].split("&")[0]),
                                                       "chat": {"id": -42}}})


def test_tc4_03_and_06_correct_routing_and_message_id():
    requests = []
    def handler(request):
        requests.append(request)
        return ok(request)
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(handler))
    assert notifier.send_text(101, "ข่าว") == [77]
    assert b"message_thread_id=101" in requests[0].content


def test_tc4_04_wrong_thread_no_fallback():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": False, "description": "thread invalid"})
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(handler))
    with pytest.raises(DeliveryError):
        notifier.send_text(101, "ข่าว")
    assert len(calls) == 1 and b"message_thread_id=101" in calls[0].content


def test_tc4_05_api_failure_not_success():
    notifier = TelegramNotifier(token="test-token", chat_id="-42",
                                client=fake_client(lambda _: httpx.Response(200, json={"ok": False})))
    outcome = deliver(notifier, 101, "ข่าว")
    assert not outcome.text_delivered and outcome.text_message_ids == []


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, text="not json"),
                                      httpx.Response(200, json={"ok": True, "result": {}})])
def test_unconfirmed_delivery_is_uncertain_and_not_retried(response):
    calls = []
    def handler(request):
        calls.append(request)
        return response
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(handler), max_retries=2)
    result = deliver(notifier, 101, "ข่าว")
    assert len(calls) == 1 and result.text_uncertain and not result.text_delivered


def test_rate_limit_respects_retry_after_and_is_bounded(monkeypatch):
    calls, waits = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"ok": False}) if len(calls) == 1 else ok(request)
    monkeypatch.setattr("src.notifier.time.sleep", waits.append)
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(handler), max_retries=1)
    assert notifier.send_text(101, "ข่าว") == [77]
    assert len(calls) == 2 and waits == [7]


def test_large_rate_limit_does_not_retry_early(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 120}})
    monkeypatch.setattr("src.notifier.time.sleep", lambda _: pytest.fail("should not sleep"))
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(handler), max_retries=2)
    with pytest.raises(DeliveryError, match="later retry"):
        notifier.send_text(101, "ข่าว")
    assert len(calls) == 1


class FakeTTS:
    def __init__(self, text, *, voice, rate, fail=False):
        self.fail = fail

    async def save(self, path):
        Path(path).write_bytes(b"mp3-data")
        if self.fail:
            raise RuntimeError("tts failed")


def test_tc4_07_tts_failure_cleans_partial_file(tmp_path):
    path = tmp_path / "brief.mp3"
    with pytest.raises(DeliveryError):
        asyncio.run(make_audio("ข่าว", path, tts_factory=lambda *a, **k: FakeTTS(*a, **k, fail=True)))
    assert not path.exists()


def test_tc4_08_and_09_partial_delivery_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr("src.notifier.tempfile.tempdir", str(tmp_path))
    def handler(request):
        if "sendAudio" in str(request.url):
            return httpx.Response(200, json={"ok": False})
        return ok(request)
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(handler))
    outcome = deliver(notifier, 101, "ข่าว", audio=True, tts_factory=FakeTTS)
    assert outcome.text_delivered and outcome.text_message_ids == [77]
    assert not outcome.audio_delivered and outcome.audio_error
    assert list(tmp_path.iterdir()) == []


def test_audio_size_check(tmp_path):
    path = tmp_path / "empty.mp3"
    path.touch()
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=fake_client(ok))
    with pytest.raises(DeliveryError, match="empty"):
        notifier.send_audio(101, path)
