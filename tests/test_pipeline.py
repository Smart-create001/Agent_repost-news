"""End-to-end pipeline tests with all external operations injected."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from src.config import load_config
from src.main import main, run
from src.notifier import DeliveryResult, TelegramNotifier
from src.scraper import fetch_topic
from src.state import load_state


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def settings(two=True):
    config = load_config(ROOT / "config.yaml")
    topics = [topic.model_copy(update={
        "enabled": True,
        "telegram": topic.telegram.model_copy(update={"message_thread_id": thread_id}),
    }) for topic, thread_id in zip(config.topics, (101, 102))]
    return config.model_copy(update={"topics": topics if two else topics[:1]})


def post(topic):
    return SimpleNamespace(id=topic.id + "-post", url=f"https://news.example.org/{topic.id}",
                           title=topic.name, published_at=NOW)


def analysis(topic):
    return SimpleNamespace(summary_th="ข่าวสำคัญ", source_urls=[post(topic).url])


def test_tc5_01_sequential_and_tc5_10_isolation(tmp_path, monkeypatch):
    events = []
    def fetch(topic, **kwargs):
        events.append((topic.id, "fetch"))
        return [post(topic)], []
    def analyze(topic, posts, **kwargs):
        assert posts == [post(topic)]
        events.append((topic.id, "analyze"))
        return [analysis(topic)]
    def send(notifier, thread_id, text, **kwargs):
        events.append((thread_id, "send"))
        return DeliveryResult(text_message_ids=[thread_id])
    monkeypatch.setattr("src.main.deliver", send)
    assert run(settings(), mode="alert", state_path=tmp_path / "state.json",
               fetch=fetch, analyze=analyze, notifier=object(), now=NOW) == 0
    assert events == [("ai_tech", "fetch"), ("ai_tech", "analyze"), (101, "send"),
                      ("gold_price", "fetch"), ("gold_price", "analyze"), (102, "send")]


def test_tc5_02_failure_isolated(tmp_path, monkeypatch):
    seen = []
    def fetch(topic, **kwargs):
        seen.append(topic.id)
        if topic.id == "ai_tech":
            raise RuntimeError("feed unavailable")
        return [post(topic)], []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: DeliveryResult(text_message_ids=[1]))
    assert run(settings(), mode="alert", state_path=tmp_path / "state.json", fetch=fetch,
               analyze=lambda topic, posts, **kw: [analysis(topic)], notifier=object(), now=NOW) == 1
    assert seen == ["ai_tech", "gold_price"]


def test_tc5_03_no_data_no_ai_or_tts(tmp_path):
    path = tmp_path / "state.json"
    assert run(settings(False), mode="alert", state_path=path,
               fetch=lambda *a, **k: ([], []),
               analyze=lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI called")), now=NOW) == 0
    assert not path.exists()


def test_tc5_04_alert_relevance_and_tc5_06_repeat(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: (sent.append(1) or DeliveryResult(text_message_ids=[88])))
    path = tmp_path / "state.json"
    cfg = settings(False)
    fetch = lambda topic, **kw: ([post(topic)], [])
    analyze = lambda topic, posts, **kw: [analysis(topic)]
    assert run(cfg, mode="alert", state_path=path, fetch=fetch, analyze=analyze, notifier=object(), now=NOW) == 0
    assert run(cfg, mode="alert", state_path=path, fetch=fetch, analyze=analyze, notifier=object(), now=NOW) == 0
    assert sent == [1]


@pytest.mark.skip(reason="Audio delivery is disabled in the text-only release; preserve test for future audio work")
def test_tc5_05_briefing_text_audio(tmp_path, monkeypatch):
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: DeliveryResult(text_message_ids=[10]))
    monkeypatch.setattr("src.main.deliver_audio_only", lambda *a, **k: DeliveryResult(audio_message_id=11))
    cfg = settings(False)
    path = tmp_path / "state.json"
    assert run(cfg, mode="briefing", state_path=path,
               fetch=lambda topic, **kw: ([post(topic)], []),
               analyze=lambda topic, posts, **kw: [analysis(topic)], notifier=object(), now=NOW) == 0
    briefing = next(iter(load_state(path)["topics"]["ai_tech"]["briefings"].values()))
    assert briefing["text"]["message_id"] == 10 and briefing["audio"]["message_id"] == 11


@pytest.mark.skip(reason="Audio recovery is disabled in the text-only release; preserve test for future audio work")
def test_tc5_07_audio_retry_without_text(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    cfg = settings(False)
    sends = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: (sends.append("text") or DeliveryResult(text_message_ids=[10])))
    monkeypatch.setattr("src.main.deliver_audio_only", lambda *a, **k: (sends.append("audio-fail") or DeliveryResult(audio_error="TTS failed")))
    kwargs = dict(mode="briefing", state_path=path, fetch=lambda topic, **kw: ([post(topic)], []),
                  analyze=lambda topic, posts, **kw: [analysis(topic)], notifier=object(), now=NOW)
    assert run(cfg, **kwargs) == 1
    monkeypatch.setattr("src.main.deliver_audio_only", lambda *a, **k: (sends.append("audio") or DeliveryResult(audio_message_id=11)))
    assert run(cfg, **kwargs) == 0
    assert sends == ["text", "audio-fail", "audio"]


def test_tc5_08_unknown_delivery_not_retried(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: (sent.append(1) or DeliveryResult(text_error="timeout", text_uncertain=True)))
    kwargs = dict(mode="alert", state_path=path, fetch=lambda topic, **kw: ([post(topic)], []),
                  analyze=lambda topic, posts, **kw: [analysis(topic)], notifier=object(), now=NOW)
    assert run(settings(False), **kwargs) == 1
    assert run(settings(False), **kwargs) == 0
    assert sent == [1]


def test_partial_text_delivery_preserves_confirmed_chunk_ids_and_does_not_retry(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    sent = []
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: (sent.append(1) or
                        DeliveryResult(text_message_ids=[31], text_error="later chunk failed")))
    kwargs = dict(mode="alert", state_path=path, fetch=lambda topic, **kw: ([post(topic)], []),
                  analyze=lambda topic, posts, **kw: [analysis(topic)], notifier=object(), now=NOW)
    assert run(settings(False), **kwargs) == 1
    alert = load_state(path)["topics"]["ai_tech"]["posts"]["ai_tech-post"]["alert"]
    assert alert["uncertain"] and alert["message_ids"] == [31]
    assert run(settings(False), **kwargs) == 0
    assert sent == [1]


def test_complete_multichunk_delivery_preserves_all_message_ids(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: DeliveryResult(text_message_ids=[31, 32]))
    assert run(settings(False), mode="alert", state_path=path, now=NOW, notifier=object(),
               fetch=lambda topic, **kw: ([post(topic)], []),
               analyze=lambda topic, posts, **kw: [analysis(topic)]) == 0
    alert = load_state(path)["topics"]["ai_tech"]["posts"]["ai_tech-post"]["alert"]
    assert alert["message_id"] == 32 and alert["message_ids"] == [31, 32]


def test_crash_between_chunks_keeps_confirmed_id_and_does_not_resend(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 2:
            raise SystemExit("simulated process interruption")
        return httpx.Response(200, json={"ok": True, "result": {
            "message_id": 31, "message_thread_id": 101, "chat": {"id": -42}}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=client)
    path = tmp_path / "state.json"
    kwargs = dict(mode="alert", state_path=path, now=NOW, notifier=notifier,
                  fetch=lambda topic, **kw: ([post(topic)], []),
                  analyze=lambda topic, posts, **kw: [SimpleNamespace(
                      summary_th="ข่าว" * 2000, source_urls=[posts[0].url])])
    with pytest.raises(SystemExit):
        run(settings(False), **kwargs)
    alert = load_state(path)["topics"]["ai_tech"]["posts"]["ai_tech-post"]["alert"]
    assert alert["uncertain"] and alert["message_ids"] == [31]
    assert run(settings(False), **kwargs) == 0
    assert len(requests) == 2
    client.close()


def test_tc5_09_dry_run_does_not_write_or_deliver(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr("src.main.deliver", lambda *a, **k: (_ for _ in ()).throw(AssertionError("delivered")))
    assert run(settings(False), mode="alert", state_path=path, dry_run=True,
               fetch=lambda topic, **kw: ([post(topic)], []),
               analyze=lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI called")), now=NOW) == 0
    assert not path.exists()


def test_cli_dry_run(tmp_path):
    config = load_config(ROOT / "config.yaml")
    offline = config.model_copy(update={
        "topics": [topic.model_copy(update={"enabled": False}) for topic in config.topics]
    })
    offline_path = tmp_path / "offline.yaml"
    offline_path.write_text(yaml.safe_dump(offline.model_dump(mode="json")), encoding="utf-8")
    assert main(["--mode", "alert", "--dry-run", "--config", str(offline_path),
                 "--state", str(tmp_path / "state.json")]) == 0


def test_cli_does_not_log_telegram_token(monkeypatch, caplog):
    import logging
    token = "test-token-must-not-be-logged"
    caplog.set_level(logging.INFO, logger="httpx")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-42")
    monkeypatch.setattr("src.main.validate_live_config", lambda config: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "ok": True, "result": {"message_id": 1, "message_thread_id": 101, "chat": {"id": -42}}})))
    notifier = TelegramNotifier(token=token, chat_id="-42", client=client)
    monkeypatch.setattr("src.main.TelegramNotifier", lambda **kwargs: notifier)
    monkeypatch.setattr("src.main.run", lambda config, **kwargs: (notifier.send_text(101, "ข่าว") and 0))
    try:
        assert main(["--mode", "alert"]) == 0
        assert token not in caplog.text
    finally:
        client.close()


def test_audio_alert_configuration_is_rejected_before_delivery(tmp_path):
    cfg = settings(False)
    cfg = cfg.model_copy(update={"alert": cfg.alert.model_copy(update={"generate_audio": True})})
    with pytest.raises(ValueError, match="audio alerts require delivery state"):
        run(cfg, mode="alert", state_path=tmp_path / "state.json")
    assert not (tmp_path / "state.json").exists()


def test_crash_after_send_request_is_not_blindly_retried(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    attempts = []
    def crash(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("process interrupted after request")
    monkeypatch.setattr("src.main.deliver", crash)
    kwargs = dict(mode="alert", state_path=path, fetch=lambda topic, **kw: ([post(topic)], []),
                  analyze=lambda topic, posts, **kw: [analysis(topic)], notifier=object(), now=NOW)
    assert run(settings(False), **kwargs) == 1
    item = load_state(path)["topics"]["ai_tech"]["posts"]["ai_tech-post"]
    assert item["alert"]["uncertain"]
    assert run(settings(False), **kwargs) == 0
    assert attempts == [1]


def test_real_rss_parser_and_telegram_http_in_pipeline(tmp_path):
    rss = b'''<rss version="2.0"><channel><title>Local</title><item><title>Story</title>
    <link>https://news.example.org/real-story</link><pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate>
    </item></channel></rss>'''
    feed_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=rss)))
    requests = []
    def telegram_handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {
            "message_id": 123, "message_thread_id": 101, "chat": {"id": -42}}})
    telegram_client = httpx.Client(transport=httpx.MockTransport(telegram_handler))
    notifier = TelegramNotifier(token="test-token", chat_id="-42", client=telegram_client)
    cfg = settings(False)
    assert run(cfg, mode="alert", state_path=tmp_path / "state.json", now=NOW, notifier=notifier,
               fetch=lambda topic, **kw: fetch_topic(topic, client=feed_client, **kw),
               analyze=lambda topic, posts, **kw: [SimpleNamespace(summary_th="ข่าวจริง",
                                                  source_urls=[posts[0].url])]) == 0
    assert len(requests) == 1 and b"message_thread_id=101" in requests[0].content
    state = load_state(tmp_path / "state.json")
    item = next(iter(state["topics"]["ai_tech"]["posts"].values()))
    assert item["alert"]["message_id"] == 123
