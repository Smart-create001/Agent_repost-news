"""Render a secret-free status report for one Actions run."""

import argparse
from datetime import datetime
import html
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def read_log(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def summarize(log, state):
    counts = {"feed": 0, "gemini": 0, "quarantined": 0, "telegram": 0,
              "expired": 0}
    if log is not None:
        for line in log.splitlines():
            match = re.search(r'\bfeed_errors=(\d+)', line)
            if match:
                counts["feed"] += int(match.group(1))
            match = re.search(r'\bexpired_untracked=(\d+)', line)
            if match:
                counts["expired"] += int(match.group(1))
            if "batch_start=" in line and " failed=" in line:
                counts["gemini"] += 1
            match = re.search(r'\bquarantined=(\d+)', line)
            if match:
                counts["quarantined"] += int(match.group(1))
            if "delivery_failed=1" in line:
                counts["telegram"] += 1
    try:
        data = json.loads(Path(state).read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("topics"), dict):
            raise ValueError("invalid state")
        uncertain = 0
        for topic in data["topics"].values():
            for post in topic["posts"].values():
                uncertain += bool(post.get("alert", {}).get("uncertain"))
            for briefing in topic["briefings"].values():
                uncertain += bool(briefing.get("text", {}).get("uncertain"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        uncertain = None
    return counts, uncertain


def build_report(log, state, env):
    counts, uncertain = summarize(log, state)
    exit_code = env.get("AGENT_EXIT", "")
    agent = "ผ่าน" if exit_code == "0" else "ล้มเหลว" if exit_code else "ไม่มีข้อมูล"
    persist = {"success": "ผ่าน", "failure": "ล้มเหลว", "skipped": "ไม่ได้ทำ"}.get(env.get("PERSIST_OUTCOME", ""), "ไม่มีข้อมูล")
    mode = {"alert": "Alert", "briefing": "Briefing", "verify_state": "ตรวจ state"}.get(env.get("RUN_MODE", ""), "ไม่ทราบ")
    def count_value(name):
        if log is None:
            return "ไม่มีข้อมูล"
        if mode == "ตรวจ state":
            return "ไม่ได้ตรวจ"
        return str(counts[name])
    return {
        "run": env.get("GITHUB_RUN_NUMBER", "-") if env.get("GITHUB_RUN_NUMBER", "").isdigit() else "-",
        "time": datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%d/%m/%Y %H:%M น."),
        "mode": mode, "agent": agent, "persist": persist,
        "feed": count_value("feed"), "gemini": count_value("gemini"),
        "quarantined": count_value("quarantined"), "telegram": count_value("telegram"),
        "expired": count_value("expired"),
        "uncertain": str(uncertain) if uncertain is not None else "ไม่มีข้อมูล",
    }


def render(report):
    e = lambda value: html.escape(str(value), quote=True)
    labels = [("feed", "Feed ที่มีปัญหา"), ("gemini", "Gemini ล้มเหลว"),
              ("quarantined", "รายการที่ถูกกัก"), ("telegram", "ส่ง Telegram ไม่สำเร็จ"),
              ("expired", "RSS เกินช่วงเก็บตกและไม่มีใน state"),
              ("uncertain", "รอตรวจสอบใน state")]
    cards = "".join(f'<article><span>{e(label)}</span><strong>{e(report[key])}</strong></article>' for key, label in labels)
    rows = "\n".join(f'| {label} | {report[key]} |' for key, label in labels)
    note = "ตัวเลข Feed, Gemini, Telegram และรายการเกินช่วงค้นมาจาก log ของรอบนี้; รายการเกินช่วงค้นอาจเป็นข่าวเก่าก่อนเริ่มระบบ ไม่ได้ยืนยันว่าพลาดการส่ง; state รวม uncertain ที่ค้างจากทุกรอบ"
    summary = f'## สถานะบอท · รอบ {report["run"]}\n\n{report["time"]} (ไทย) · {report["mode"]}  \nAgent: **{report["agent"]}** · บันทึก state: **{report["persist"]}**\n\n| รายการ | จำนวน |\n|---|---:|\n{rows}\n\n{note}\n\nดาวน์โหลด dashboard HTML จาก Artifacts ของ run นี้ได้ (เก็บ 30 วัน)\n'
    page = f'''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>สถานะบอทข่าว</title><style>body{{font-family:system-ui,sans-serif;background:#f2f5fa;color:#17253b;margin:0}}main{{max-width:1000px;margin:48px auto;padding:0 24px}}header{{background:#122849;color:white;padding:32px;border-radius:18px}}h1{{margin:0 0 8px;font-size:30px}}.meta{{opacity:.85}}.status{{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}}.status div,article{{background:white;border:1px solid #dfe6f0;border-radius:14px;padding:20px}}.status div{{flex:1;min-width:180px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}article span{{display:block;color:#506079}}strong{{display:block;font-size:32px;margin-top:8px}}p{{line-height:1.7;color:#506079}}@media(max-width:600px){{main{{margin:20px auto}}header{{padding:22px}}}}</style></head><body><main><header><h1>สถานะบอทข่าว</h1><div class="meta">รอบ {e(report['run'])} · {e(report['time'])} เวลาไทย · {e(report['mode'])}</div></header><section class="status"><div>Agent <strong>{e(report['agent'])}</strong></div><div>บันทึก state <strong>{e(report['persist'])}</strong></div></section><section class="grid">{cards}</section><p>{e(note)}</p><p>หากมีรายการ uncertain ให้ตรวจ Telegram และ reconcile state ก่อนส่งซ้ำ</p></main></body></html>'''
    return summary, page


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()
    summary, page = render(build_report(read_log(args.log), args.state, os.environ))
    Path(args.html).write_text(page, encoding="utf-8")
    with open(args.summary, "a", encoding="utf-8") as stream:
        stream.write(summary)


if __name__ == "__main__":
    main()
