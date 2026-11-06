import json

from scripts.dashboard import build_report, render


def test_dashboard_summarizes_errors_without_including_raw_logs(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'schema_version': 1, 'topics': {'ai': {'posts': {'x': {'alert': {'uncertain': True}}}, 'briefings': {}}}}))
    log = ('WARNING topic=ai feed_errors=2\nERROR topic=ai batch_start=0 failed=BrainError\n'
           'ERROR topic=ai batch_start=4 quarantined=1\nERROR topic=ai delivery_failed=1 uncertain=True\n'
           'WARNING topic=ai expired_feed_entries=7 expired_untracked=3\n'
           'secret-do-not-copy https://example.org/private\n')
    report = build_report(log, path, {'AGENT_EXIT': '1', 'PERSIST_OUTCOME': 'success', 'RUN_MODE': 'alert', 'GITHUB_RUN_NUMBER': '42'})
    assert [report[x] for x in ('feed', 'gemini', 'quarantined', 'telegram', 'uncertain')] == ['2', '1', '1', '1', '1']
    assert report['expired'] == '3'
    assert report['agent'] == 'ล้มเหลว' and report['persist'] == 'ผ่าน'
    summary, page = render(report)
    assert 'secret-do-not-copy' not in summary + page and 'example.org/private' not in summary + page


def test_dashboard_marks_missing_inputs_unknown_and_escapes_html(tmp_path):
    report = build_report(None, tmp_path / 'missing.json', {})
    assert report['feed'] == report['uncertain'] == report['agent'] == 'ไม่มีข้อมูล'
    report['mode'] = '<script>alert(1)</script>'
    _, page = render(report)
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;' in page


def test_verify_state_run_does_not_claim_to_check_providers(tmp_path):
    report = build_report('state verified', tmp_path / 'missing.json', {'RUN_MODE': 'verify_state', 'AGENT_EXIT': '0'})
    assert report['agent'] == 'ผ่าน'
    assert report['feed'] == report['gemini'] == report['telegram'] == 'ไม่ได้ตรวจ'


def test_capture_failure_and_pending_metrics_are_visible_without_raw_news(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'schema_version': 1, 'topics': {
        'ai': {'posts': {}, 'briefings': {}, 'pending_posts': {'x': {}},
               'pending_metrics': {'overflow_total': 1, 'expired_total': 2,
                                   'overflow_unresolved': True}}}}))
    log = 'INFO topic=ai pending_capture=300 overflow=1 feed_errors=0\nWARNING topic=ai pending_expired=2\nsecret-news-text'
    report = build_report(log, path, {'RUN_MODE': 'alert', 'CAPTURE_OUTCOME': 'failure',
                                      'PARTIAL_PERSIST_OUTCOME': 'success', 'PERSIST_OUTCOME': 'skipped'})
    assert report['capture'] == 'ล้มเหลว'
    assert report['partial_persist'] == 'ผ่าน' and report['agent'] == 'ไม่มีข้อมูล'
    assert report['overflow'] == '1' and report['pending_expired'] == '2'
    assert report['pending'] == '1' and report['overflow_unresolved'] == '1'
    summary, page = render(report)
    assert 'secret-news-text' not in summary + page
