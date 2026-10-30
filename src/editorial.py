"""Render validated editorial fields for delivery."""


def render_news(analysis) -> str:
    if hasattr(analysis, "title"):
        return ("📰 พาดหัวจากแหล่งข่าว (ข้อความต้นฉบับ)\n" + analysis.title +
                "\n\nสรุปอัตโนมัติไม่ผ่านการตรวจสอบ กรุณาอ่านรายละเอียดจากต้นทาง\n" +
                "\n".join("🔗 แหล่งข่าว: " + str(url) for url in analysis.source_urls))
    paragraphs = ["📰 " + analysis.headline, analysis.lead, *analysis.body]
    if analysis.background:
        paragraphs.append(analysis.background)
    if analysis.uncertainties:
        paragraphs.append("ข้อจำกัด: " + "; ".join(analysis.uncertainties))
    paragraphs.extend("🔗 แหล่งข่าว: " + str(url) for url in analysis.source_urls)
    return "\n\n".join(paragraphs)
