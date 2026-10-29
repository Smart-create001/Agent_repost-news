"""Text-only release: never invoke audio and retain historical delivery state."""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.config import ConfigError, load_config
from src.main import run
from src.notifier import DeliveryResult
from src.state import empty_state, load_state, record_briefing, record_delivery, save_state

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def test_default_flag_and_explicit_true_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    base = "schema_version: 1\ntopics:\n  - id: a\n    name: A\n    telegram: {message_thread_id: 1}\n    rss_urls: [https://example.org/feed]\n"
    path.write_text(base)
    assert load_config(path).features.audio_enabled is False
    path.write_text(base + "features:\n  audio_enabled: true\n")
    with pytest.raises(ConfigError, match="audio_enabled=true"):
        load_config(path)


def test_text_execution_does_not_import_tts():
    result = subprocess.run([sys.executable, "-c", "import sys; import src.main; assert 'edge_tts' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_runtime_rejects_bypassed_true_flag(tmp_path):
    cfg = load_config().model_copy(deep=True)
    cfg.features = cfg.features.model_copy(update={"audio_enabled": True})
    with pytest.raises(ValueError, match="audio_enabled=true"):
        run(cfg, mode="briefing", state_path=tmp_path / "state.json")
    assert not (tmp_path / "state.json").exists()


def test_briefing_text_no_audio_or_audio_record(tmp_path, monkeypatch):
    cfg = load_config().model_copy(deep=True)
    cfg.topics = cfg.topics[:1]
    topic = cfg.topics[0]
    post = SimpleNamespace(id="p", url="https://example.org/a", title="A", published_at=NOW)
    sends = []
    monkeypatch.setattr("src.main.deliver", lambda notifier, thread, text, **kw:
                        (sends.append((thread, kw.get("audio"))) or DeliveryResult(text_message_ids=[42])))
    monkeypatch.setattr("src.notifier.make_audio", lambda *a, **k: pytest.fail("TTS called"))
    monkeypatch.setattr("src.notifier.TelegramNotifier.send_audio", lambda *a, **k: pytest.fail("sendAudio called"))
    path = tmp_path / "state.json"
    kwargs = dict(mode="briefing", state_path=path, notifier=object(), now=NOW,
                  fetch=lambda *a, **k: ([post], []),
                  analyze=lambda *a, **k: [SimpleNamespace(summary_th="ข่าว", source_urls=[post.url])])
    assert run(cfg, **kwargs) == 0
    briefing = next(iter(load_state(path)["topics"][topic.id]["briefings"].values()))
    assert briefing["text"]["delivered"] and "audio" not in briefing and not briefing["audio_required"]
    assert run(cfg, **kwargs) == 0
    assert sends == [(topic.telegram.message_thread_id, False)]
    assert not list(tmp_path.glob("*.mp3"))


def test_existing_delivered_text_and_audio_state_not_retried(tmp_path, monkeypatch):
    cfg = load_config().model_copy(deep=True)
    cfg.topics = cfg.topics[:1]
    topic = cfg.topics[0]
    start = datetime(2026, 10, 26, tzinfo=timezone.utc)
    bid = f"{topic.id}:briefing:{start.isoformat()}"
    state = empty_state()
    record_briefing(state, topic.id, bid, start, start + timedelta(days=1), ["p"])
    record_delivery(state, topic.id, bid, mode="briefing", part="text", success=True, message_id=17)
    path = tmp_path / "state.json"
    save_state(path, state)
    original = path.read_bytes()
    monkeypatch.setattr("src.main.deliver", lambda *a, **kw: pytest.fail("text resent"))
    monkeypatch.setattr("src.notifier.make_audio", lambda *a, **kw: pytest.fail("TTS called"))
    assert run(cfg, mode="briefing", state_path=path, notifier=object(), now=NOW) == 0
    assert path.read_bytes() == original
