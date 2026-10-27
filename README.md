# Agent_repost-news

Multi-topic AI social listening and Thai audio briefing agent, built one phase at a time.

## Phase 1: configuration

Requires Python 3.11+ (tested with 3.12). From this repository:

Linux/macOS:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests/test_config.py -v
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest tests/test_config.py -v
```

`load_config()` validates YAML and topic settings without credentials or Internet. `validate_live_config()` separately checks the required environment variables. Copy `.env.example` to `.env` and enter credentials locally before a future live run. The two topics in `config.yaml` are disabled examples; replace their RSS URLs and Telegram thread IDs with verified values before enabling them. No bot runner or live service connection exists in Phase 1.

The current `seen_posts.json` is an empty versioned starting document. Phase 2 implements its state format and writes.

## Phase 2: RSS and state

Install the updated requirements, then run:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests/test_scraper.py tests/test_state.py -v
```

PowerShell uses `.venv\Scripts\python.exe` in the same commands. `src.scraper.fetch_topic` fetches RSS/Atom sequentially and returns posts plus per-feed errors. Dates missing from a feed use the fetch time and are marked `date_inferred`; a configured age limit cannot reject these on date alone. `src.state` stores posts and briefings per topic, keeps delivery status separate, and writes JSON atomically. Call `save_state(..., dry_run=True)` to avoid writes in dry runs. No Gemini or Telegram call is made in this phase.

## Phase 3: Gemini analysis

`src.brain.analyze_topic` accepts one topic and its posts, requests structured JSON from `google-genai`, and rejects malformed results or source URLs absent from the input. It returns only relevant results meeting the configured threshold. Its unit tests inject a mock client and use no API key or network:

```bash
.venv/bin/python -m pytest tests/test_brain.py -v
```

On PowerShell use `.venv\Scripts\python.exe`. Live Gemini calls require `GEMINI_API_KEY` and may consume quota; they have not been run. The pipeline comes in Phase 5.

## Phase 4: Telegram and Thai audio

`src.notifier.TelegramNotifier` sends text and MP3 to the explicit forum thread and checks Telegram's `ok` response and message ID. `deliver()` returns separate text and audio outcomes, so a failed audio send does not erase a successful text result. `src.sanitizer.spoken_thai` prepares a separate TTS string; it does not replace Telegram's display text. Audio is written to a temporary directory that is removed after delivery.

```bash
.venv/bin/python -m pytest tests/test_sanitizer.py tests/test_notifier.py -v
```

PowerShell uses `.venv\Scripts\python.exe`. These tests mock Telegram and edge-tts. No live Telegram message or live TTS request has been made; the orchestrator comes in Phase 5.

## Phase 5: sequential pipeline

`python -m src.main --mode alert --dry-run` and `python -m src.main --mode briefing --dry-run` load configuration and inspect enabled topics without sending Telegram messages, calling Gemini, or changing production state. Live runs require verified feed URLs, enabled topics, and `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. The pipeline processes one topic at a time and stores confirmed text and audio delivery separately. Before each external delivery request, it persists an uncertain marker. A confirmed response clears that marker; if the process stops without a confirmed result, the next run does not blindly retry. Manual reconciliation is required before sending again.

```bash
.venv/bin/python -m pytest tests/test_pipeline.py -v
.venv/bin/python -m pytest tests/ -v
```

On PowerShell replace `.venv/bin/python` with `.venv\Scripts\python.exe`. The dry-run CLI currently validates the feed path only for enabled topics; the included example topics are disabled. No live workflow has been run.

## Phase 6: GitHub Actions state persistence

`.github/workflows/agent.yml` schedules alert every two hours at minute 15 UTC and briefing daily at 00:25 UTC (07:25 Asia/Bangkok); manual dispatch selects either mode. The workflow serializes runs, installs dependencies, validates `seen_posts.json`, runs the agent, and persists state even when the agent reports a topic error. `scripts/persist_state.sh` commits only changed state. It fetches `main` before pushing and fails on a moved remote instead of overwriting newer state. On workflow failure, the state file is uploaded for manual reconciliation. Local tests use a real temporary bare Git remote to verify push, reload, conflict refusal, and rejected push; they do not establish GitHub repository permissions.

For deployment, place verified feed URLs and forum thread IDs in `config.yaml`, enable the intended topics, configure the three named GitHub Actions secrets, and confirm Actions has permission to write to `main`. Branch protection may reject the state push. Git-based state works for this small single-writer workflow but can hit conflicts or repository policy limits; durable storage is preferable if update frequency or writers grow. GitHub schedule timing is approximate. The workflow has only been tested locally; it has not run on GitHub yet.

### ตั้งค่าสำหรับรันจริงเมื่อพร้อม

`config.yaml` ตั้งหัวข้อ `ai_tech` สำหรับข่าวเปิดตัวโมเดล นโยบาย และข่าวเกี่ยวกับ token/quota reset โดยติดตามชื่อ Tibo กับ Logan Kilpatrick และหัวข้อ `gold_price` สำหรับข่าวราคาทองโลก/ไทยแล้ว RSS ที่ใส่เป็น Google News search feed ซึ่งยังไม่ผ่านการตรวจ endpoint ใน cloud environment นี้เพราะ network policy บล็อก `news.google.com`. ทั้งสองหัวข้อยัง `enabled: false` และเลข thread เป็นตัวอย่าง จึงยังไม่ส่งจริง. ข้อมูลราคาทองจากข่าวอาจไม่ใช่ราคา ณ เวลาปัจจุบัน.

1. แก้ `config.yaml` ใน repository: สำหรับแต่ละ topic ที่ต้องการใช้ เปลี่ยน `rss_urls` เป็น HTTPS RSS/Atom endpoint ที่เปิดได้จริง, เปลี่ยน `telegram.message_thread_id` เป็นเลข forum topic ของปลายทาง และตั้ง `enabled: true` เฉพาะ topic นั้น ห้ามใช้ URL `example.com` และเลข thread ตัวอย่างสำหรับ live run. ตรวจ feed แต่ละ URL ว่าคืน RSS/Atom จริงก่อนเปิดใช้. Bot ต้องเป็นสมาชิกกลุ่มและส่งข้อความใน topic นั้นได้.
2. ที่ GitHub repository ไปที่ **Settings → Secrets and variables → Actions → Repository secrets → New repository secret** เพิ่ม `GEMINI_API_KEY` (จาก Google AI Studio), `TELEGRAM_BOT_TOKEN` (จาก BotFather) และ `TELEGRAM_CHAT_ID` (เลข chat ID ของกลุ่ม forum ซึ่งมักเป็นเลขลบ). `TELEGRAM_CHAT_ID` เป็นคนละค่ากับ `message_thread_id`; ใช้ chat ID เดียวใน secrets และกำหนด thread ID แยกใน `config.yaml`. อย่าใส่ค่าเหล่านี้ใน Git, README, issue หรือ chat.
3. ที่ **Settings → Actions → General → Workflow permissions** เลือก **Read and write permissions** เพื่อให้ `GITHUB_TOKEN` push `seen_posts.json` กลับ `main` ได้. ตรวจ **Settings → Rules → Rulesets** หรือ **Settings → Branches** ว่ากฎของ `main` อนุญาตให้ GitHub Actions bot push state; ถ้าบังคับ PR ทุกครั้ง workflow นี้จะ push ไม่ผ่าน. ตรวจสิทธิ์ขององค์กรด้วยถ้ามี.
4. นำไฟล์งานทั้งหมด รวม `.github/workflows/agent.yml`, `scripts/persist_state.sh` และ `seen_posts.json` ขึ้น `main` หลังตรวจทาน. GitHub จะเห็น workflow ในแท็บ **Actions → Social listening agent**. เลือก **Run workflow** และ `mode: alert` หรือ `briefing` เพื่อทดลอง; การกด Run จะส่งข้อมูลและอาจใช้ Gemini quota. เปิด run ที่จบแล้ว ตรวจขั้น **Run agent**, **Persist state** และ commit ใหม่ของ `seen_posts.json`. ถ้า push ขัดแย้งหรือถูกปฏิเสธ ให้ดาวน์โหลด artifact `state-for-reconciliation-<run_id>` จาก run แล้วเปรียบเทียบ state ก่อนแก้ด้วยมือ. อย่า force push.

CLI สำหรับตรวจแบบไม่ส่งข้อความก่อนเปิด workflow: Linux/macOS ใช้ `.venv/bin/python -m src.main --mode alert --dry-run` และ PowerShell ใช้ `.venv\Scripts\python.exe -m src.main --mode alert --dry-run`. เปลี่ยน `alert` เป็น `briefing` เพื่อทดสอบอีกโหมด. Dry run จะดึง feed ของ topic ที่เปิดใช้ แต่ไม่เรียก Gemini, ไม่ส่ง Telegram และไม่แก้ production state.
