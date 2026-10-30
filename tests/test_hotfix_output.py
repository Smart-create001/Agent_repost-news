"""Offline regression for truncated Gemini responses and batch recovery."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.brain import AnalysisResults, BrainError, analyze_topic
from src.config import ConfigError, load_config
from src.main import run
from src.notifier import DeliveryResult
from src.state import load_state
from tests.test_brain import POST, result, topic
from tests.test_pipeline import settings


class Models:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.replies)


def response(reason, text):
    return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=reason)], text=text)


def test_truncation_is_not_parsed_and_retry_is_bounded(caplog):
    class NoText:
        candidates = [SimpleNamespace(finish_reason='MAX_TOKENS')]
        @property
        def text(self):
            raise AssertionError('partial response must not be read')
    models = Models([NoText(), NoText()])
    with pytest.raises(BrainError, match='truncated'):
        analyze_topic(topic(), [POST], client=SimpleNamespace(models=models), max_output_retries=1)
    assert len(models.calls) == 2
    assert 'event=ai_output_truncation' in caplog.text


def test_complete_configured_output_passes_validation():
    models = Models([response('STOP', json.dumps([result()]))])
    assert len(analyze_topic(topic(), [POST], client=SimpleNamespace(models=models), max_output_tokens=8192)) == 1
    assert models.calls[0]['config'].max_output_tokens == 8192


@pytest.mark.parametrize('reply, message', [
    (response('STOP', None), 'text missing'),
    (response('STOP', '{bad'), 'Malformed'),
    (response('SAFETY', '{bad'), 'without completion'),
    (SimpleNamespace(candidates=[]), 'missing candidate'),
])
def test_incomplete_or_blocked_response_fails(reply, message):
    with pytest.raises(BrainError, match=message):
        analyze_topic(topic(), [POST], client=SimpleNamespace(models=Models([reply])))


def test_truncated_response_can_recover():
    models = Models([response('MAX_TOKENS', '{'), response('STOP', json.dumps([result()]))])
    assert len(analyze_topic(topic(), [POST], client=SimpleNamespace(models=models), max_output_retries=1)) == 1
    assert len(models.calls) == 2


def test_batch_failures_preserve_successes_and_retry_only_failed(tmp_path, monkeypatch):
    cfg = settings(False)
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    posts = [SimpleNamespace(id=str(i), url=f'https://news.example.org/{i}', title='ข่าว', published_at=now) for i in range(12)]
    batches, sent = [], []
    fail = [True]
    def analyze(topic, batch, **kwargs):
        batches.append([p.id for p in batch])
        assert kwargs['max_output_tokens'] == 8192
        assert kwargs['max_output_retries'] == 1
        if batch[0].id == '4' and fail[0]:
            raise BrainError('truncated')
        return [SimpleNamespace(summary_th='ข่าว', source_urls=[p.url]) for p in batch]
    monkeypatch.setattr('src.main.deliver', lambda notifier, thread, text, **kw: (sent.append(text) or DeliveryResult(text_message_ids=[len(sent)])))
    path = tmp_path / 'state.json'
    args = dict(mode='alert', state_path=path, fetch=lambda *a, **k: (posts, []), analyze=analyze, notifier=object(), now=now)
    assert run(cfg, **args) == 1
    assert batches == [[str(i) for i in range(j, j + 4)] for j in (0, 4, 8)]
    saved = load_state(path)['topics']['ai_tech']['posts']
    assert len(saved) == 8 and '4' not in saved
    fail[0] = False
    assert run(cfg, **args) == 0
    assert batches[-1] == ['4', '5', '6', '7']
    assert len(sent) == 12


def test_briefing_batch_failure_never_delivers_partial(tmp_path, monkeypatch):
    cfg = settings(False)
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    posts = [SimpleNamespace(id=str(i), url=f'https://news.example.org/{i}', title='ข่าว', published_at=now) for i in range(5)]
    def analyze(topic, batch, **kwargs):
        if batch[0].id == '4':
            raise BrainError('truncated')
        return [SimpleNamespace(summary_th='ข่าว', source_urls=[batch[0].url])]
    monkeypatch.setattr('src.main.deliver', lambda *a, **k: pytest.fail('partial briefing delivered'))
    path = tmp_path / 'state.json'
    assert run(cfg, mode='briefing', state_path=path, fetch=lambda *a, **k: (posts, []), analyze=analyze, now=now) == 1
    assert not path.exists()


def test_quarantined_item_is_reported_and_success_is_preserved(tmp_path, monkeypatch):
    cfg = settings(False)
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    posts = [SimpleNamespace(id=str(i), url=f'https://news.example.org/{i}', title='ข่าว', published_at=now) for i in range(2)]
    sent = []
    def analyze(topic, batch, **kwargs):
        return AnalysisResults([SimpleNamespace(summary_th='ข่าว', source_urls=[batch[0].url])], rejected_count=1)
    monkeypatch.setattr('src.main.deliver', lambda *a, **kw: (sent.append(1) or DeliveryResult(text_message_ids=[1])))
    path = tmp_path / 'state.json'
    assert run(cfg, mode='alert', state_path=path, fetch=lambda *a, **kw: (posts, []),
               analyze=analyze, notifier=object(), now=now) == 1
    assert sent == [1]
    saved = load_state(path)['topics']['ai_tech']['posts']
    assert '0' in saved and '1' not in saved


@pytest.mark.parametrize('ai', [
    'max_output_tokens: 0', 'max_output_tokens: 16385', 'batch_size: 21', 'max_output_retries: 4',
])
def test_invalid_ai_limits_rejected(tmp_path, ai):
    path = tmp_path / 'config.yaml'
    path.write_text(f'schema_version: 1\nai: {{{ai}}}\ntopics:\n- id: ai\n  name: AI\n  telegram: {{message_thread_id: 5}}\n  rss_urls: [https://example.org/rss]\n')
    with pytest.raises(ConfigError):
        load_config(path)
