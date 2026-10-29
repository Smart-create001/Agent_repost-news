"""Editorial contract, grounding and presentation checks (offline)."""
import json
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from src.brain import BrainError, analyze_topic
from src.main import _text
from src.scraper import parse_feed


def item(**changes):
    result = {"relevant": True, "relevance_score": 0.9, "headline": "เปิดตัว AI รุ่นใหม่",
              "lead": "บริษัทเปิดตัว AI รุ่นใหม่", "body": [], "background": "",
              "uncertainties": ["ยังไม่มีรายละเอียดเพิ่มเติม"], "reason": "ตรงหัวข้อ",
              "source_urls": ["https://example.org/news"], "confidence": 0.8}
    result.update(changes)
    return result


def run_analysis(response, post=None):
    post = post or SimpleNamespace(title="บริษัทเปิดตัว AI รุ่นใหม่", description="",
                                   url="https://example.org/news")
    topic = SimpleNamespace(id="ai", name="AI", context="ติดตาม AI", keywords=["AI"])
    calls = []
    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps([response], ensure_ascii=False))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    result = analyze_topic(topic, [post], client=client)
    return result, calls


def test_headline_only_is_formatted_with_limitations_and_source():
    results, calls = run_analysis(item())
    text = _text(results[0])
    assert text.index("เปิดตัว AI รุ่นใหม่") < text.index("บริษัทเปิดตัว AI รุ่นใหม่")
    assert "ข้อจำกัด: ยังไม่มีรายละเอียดเพิ่มเติม" in text
    assert "🔗 แหล่งข่าว: https://example.org/news" in text
    assert "return body=[]" in calls[0]["config"].system_instruction
    assert json.loads(calls[0]["contents"])["articles"][0]["headline_only"] is True


@pytest.mark.parametrize("bad", [item(lead="เหตุการณ์เกิดขึ้นในปี 2028"),
                                  item(lead='เขากล่าวว่า “ข้อความที่ไม่อยู่ในแหล่งข่าว”')])
def test_invented_numbers_and_quotes_are_rejected(bad):
    with pytest.raises(BrainError, match="absent from cited input"):
        run_analysis(bad)


def test_known_numbers_and_quotes_can_be_used():
    post = SimpleNamespace(title="บริษัทเปิดตัว AI รุ่น 3", description='ระบุว่า “เปิดให้ใช้แล้ว”',
                           url="https://example.org/news")
    results, _ = run_analysis(item(headline="เปิดตัว AI รุ่น 3", lead="บริษัทระบุว่า “เปิดให้ใช้แล้ว”"), post)
    assert len(results) == 1


def test_missing_editorial_field_and_fabricated_url_are_rejected():
    bad = item()
    del bad["lead"]
    with pytest.raises(BrainError, match="Malformed"):
        run_analysis(bad)
    with pytest.raises(BrainError, match="absent from input"):
        run_analysis(item(source_urls=["https://example.org/other"]))


def test_headline_only_rejects_unsupported_expansion():
    with pytest.raises(BrainError, match="Headline-only"):
        run_analysis(item(body=["รายละเอียดที่ไม่มีหลักฐาน"]))


def test_description_repeating_title_does_not_count_as_extra_evidence():
    post = SimpleNamespace(title="บริษัทเปิดตัว AI รุ่นใหม่ - Reuters",
                           description="บริษัทเปิดตัว AI รุ่นใหม่ Reuters",
                           url="https://example.org/news")
    with pytest.raises(BrainError, match="Headline-only"):
        run_analysis(item(body=["รายละเอียดที่ไม่มีหลักฐาน"]), post)


def test_publication_timestamp_does_not_authorize_invented_event_date():
    post = SimpleNamespace(title="ข่าวทอง", description="ราคาทองปรับลด",
                           url="https://example.org/news", date_inferred=False,
                           published_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    with pytest.raises(BrainError, match="numbers absent"):
        run_analysis(item(lead="ราคาทองปรับลดเมื่อปี 2026"), post)


def test_citation_to_article_outside_prompt_is_rejected():
    posts = [SimpleNamespace(title="ข่าว", description="ข้อมูล",
                             url=f"https://example.org/{i}") for i in range(21)]
    response = item(source_urls=[posts[-1].url])
    topic = SimpleNamespace(id="ai", name="AI", context="", keywords=[])
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw:
                             SimpleNamespace(text=json.dumps([response], ensure_ascii=False))))
    with pytest.raises(BrainError, match="absent from input"):
        analyze_topic(topic, posts, client=client)


def test_inferred_date_is_not_presented_as_publication_time():
    post = SimpleNamespace(title="ข่าว", description="ข้อมูล", url="https://example.org/news",
                           date_inferred=True, published_at=SimpleNamespace(),
                           retrieved_at=datetime(2026, 10, 29, tzinfo=timezone.utc))
    _, calls = run_analysis(item(), post)
    article = json.loads(calls[0]["contents"])["articles"][0]
    assert article["published_at"] is None and article["retrieved_at"].startswith("2026-10-29")


def test_urls_in_editorial_fields_are_rejected():
    with pytest.raises(BrainError, match="URLs in editorial fields"):
        run_analysis(item(lead="อ่านเพิ่มเติม https://fake.example.org/news"))


def test_multiple_sources_in_single_news_item_are_rejected():
    with pytest.raises(BrainError, match="Malformed"):
        run_analysis(item(source_urls=["https://example.org/news", "https://example.org/other"]))


def test_rejects_false_currency_uncertainty_and_reversed_direction():
    post = SimpleNamespace(title="ราคาทองปรับลด 400 บาท", description="ราคาทองปรับลด 400 บาท",
                           url="https://example.org/news")
    with pytest.raises(BrainError, match="no currency"):
        run_analysis(item(headline="ราคาทองลด 400 บาท", lead="ราคาทองปรับลด 400 บาท",
                          uncertainties=["ไม่ระบุหน่วยเงินตราที่ชัดเจน"]), post)
    with pytest.raises(BrainError, match="source direction"):
        run_analysis(item(headline="ราคาทองปรับขึ้น 400 บาท", lead="ราคาทองปรับขึ้น 400 บาท"), post)


def test_feed_description_is_plain_text_and_script_is_not_input():
    xml = b'''<rss version="2.0"><channel><title>x</title><item><title>News</title>
    <link>https://example.org/news</link><description><![CDATA[<p>Fact <b>one</b></p><script>bad()</script>]]></description>
    </item></channel></rss>'''
    post = parse_feed(xml, "https://example.org/rss")[0]
    assert "Fact" in post.description and "one" in post.description
    assert "<" not in post.description and "bad()" not in post.description
    assert post.date_inferred


def test_provider_schema_excludes_unsupported_additional_properties():
    _, calls = run_analysis(item())
    schema = calls[0]["config"].response_schema
    assert schema.items.additional_properties is None
