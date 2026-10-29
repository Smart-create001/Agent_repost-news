"""Probe TTS on GitHub Actions; no Telegram delivery or production state."""
import asyncio
import tempfile
from pathlib import Path

import edge_tts


async def main():
    with tempfile.TemporaryDirectory(prefix="phase6-tts-") as directory:
        path = Path(directory) / "probe.mp3"
        try:
            await asyncio.wait_for(edge_tts.Communicate(
                "ทดสอบเสียงภาษาไทย", voice="th-TH-PremwadeeNeural").save(str(path)), timeout=45)
            print(f"TTS_PASS bytes={path.stat().st_size}", flush=True)
        except Exception as exc:
            print(f"TTS_FAIL type={type(exc).__name__} detail={str(exc)[:400]}", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
