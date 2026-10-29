"""Telegram topic delivery and temporary Thai MP3 generation."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
import time

import httpx

from src.sanitizer import headline_entities, split_telegram, spoken_thai


MAX_AUDIO_BYTES = 50 * 1024 * 1024


class DeliveryError(RuntimeError):
    """Telegram or audio delivery failed without changing another delivery state."""

    def __init__(self, message: str, *, sent_ids: list[int] | None = None,
                 uncertain: bool = False):
        super().__init__(message)
        self.sent_ids = sent_ids or []
        self.uncertain = uncertain


@dataclass
class DeliveryResult:
    text_message_ids: list[int] = field(default_factory=list)
    audio_message_id: int | None = None
    text_error: str | None = None
    audio_error: str | None = None
    text_uncertain: bool = False
    audio_uncertain: bool = False

    @property
    def text_delivered(self) -> bool:
        return bool(self.text_message_ids) and self.text_error is None

    @property
    def audio_delivered(self) -> bool:
        return self.audio_message_id is not None


class TelegramNotifier:
    def __init__(self, *, token: str, chat_id: str, timeout: float = 20,
                 max_retries: int = 2, client: httpx.Client | None = None):
        if not token or not chat_id or not chat_id.lstrip("-").isdigit():
            raise ValueError("Valid Telegram token and numeric chat ID are required")
        if timeout <= 0 or max_retries < 0:
            raise ValueError("Invalid timeout or retry limit")
        self._token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _send(self, method: str, thread_id: int, *, data: dict, files=None) -> int:
        if type(thread_id) is not int or thread_id <= 0:
            raise DeliveryError("Valid message_thread_id required; no fallback")
        payload = {"chat_id": self.chat_id, "message_thread_id": thread_id, **data}
        for attempt in range(self.max_retries + 1):
            try:
                if files is not None:
                    for _, file_info in files.items():
                        file_info[1].seek(0)
                response = self._client.post(
                    f"https://api.telegram.org/bot{self._token}/{method}",
                    data=payload, files=files, timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()
                if body.get("ok") is not True or type(body.get("result", {}).get("message_id")) is not int:
                    raise DeliveryError("Telegram did not confirm a message ID")
                returned = body["result"]
                if returned.get("message_thread_id") != thread_id:
                    raise DeliveryError("Telegram did not confirm the requested thread ID")
                if str(returned.get("chat", {}).get("id")) != self.chat_id:
                    raise DeliveryError("Telegram did not confirm the requested chat ID")
                return returned["message_id"]
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                # A timeout or transport break may follow accepted delivery.
                # Retrying that request can duplicate a message.
                if attempt < self.max_retries and status in (429, 500, 502, 503, 504):
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise DeliveryError(f"Telegram request failed: {type(exc).__name__}",
                                    uncertain=status is None) from exc
            except (ValueError, TypeError) as exc:
                raise DeliveryError("Invalid Telegram response") from exc
        raise DeliveryError("Telegram retry limit exceeded")

    def send_text(self, thread_id: int, text: str, *, on_chunk=None) -> list[int]:
        ids = []
        for chunk in split_telegram(text):
            try:
                data = {"text": chunk}
                entities = headline_entities(chunk)
                if entities:
                    data["entities"] = json.dumps(entities, ensure_ascii=False)
                ids.append(self._send("sendMessage", thread_id, data=data))
                if on_chunk is not None:
                    on_chunk(list(ids))
            except DeliveryError as exc:
                raise DeliveryError(str(exc), sent_ids=ids, uncertain=exc.uncertain) from exc
        return ids

    def send_audio(self, thread_id: int, path: str | Path) -> int:
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > MAX_AUDIO_BYTES:
            raise DeliveryError("MP3 missing, empty, or exceeds Telegram size limit")
        with path.open("rb") as stream:
            return self._send("sendAudio", thread_id, data={}, files={"audio": (path.name, stream, "audio/mpeg")})


async def make_audio(text: str, path: str | Path, *, voice: str = "th-TH-PremwadeeNeural",
                     rate: str = "+0%", tts_factory=None) -> None:
    spoken = spoken_thai(text)
    if tts_factory is None:
        import edge_tts
        factory = edge_tts.Communicate
    else:
        factory = tts_factory
    try:
        await factory(spoken, voice=voice, rate=rate).save(str(path))
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise DeliveryError("TTS produced no audio")
    except Exception as exc:
        Path(path).unlink(missing_ok=True)
        raise DeliveryError("Thai TTS generation failed") from exc


def deliver(notifier: TelegramNotifier, thread_id: int, text: str, *, audio: bool = False,
            voice: str = "th-TH-PremwadeeNeural", rate: str = "+0%", tts_factory=None,
            on_text_chunk=None) -> DeliveryResult:
    result = DeliveryResult()
    try:
        if on_text_chunk is None:
            result.text_message_ids = notifier.send_text(thread_id, text)
        else:
            result.text_message_ids = notifier.send_text(thread_id, text, on_chunk=on_text_chunk)
    except DeliveryError as exc:
        result.text_message_ids = exc.sent_ids
        result.text_error = str(exc)
        result.text_uncertain = exc.uncertain
        return result
    if audio:
        with tempfile.TemporaryDirectory(prefix="thai-briefing-") as directory:
            path = Path(directory) / "briefing.mp3"
            try:
                asyncio.run(make_audio(text, path, voice=voice, rate=rate, tts_factory=tts_factory))
                result.audio_message_id = notifier.send_audio(thread_id, path)
            except DeliveryError as exc:
                result.audio_error = str(exc)
                result.audio_uncertain = exc.uncertain
    return result


def deliver_audio_only(notifier: TelegramNotifier, thread_id: int, text: str, *,
                       voice: str = "th-TH-PremwadeeNeural", rate: str = "+0%",
                       tts_factory=None) -> DeliveryResult:
    result = DeliveryResult()
    with tempfile.TemporaryDirectory(prefix="thai-briefing-") as directory:
        path = Path(directory) / "briefing.mp3"
        try:
            asyncio.run(make_audio(text, path, voice=voice, rate=rate, tts_factory=tts_factory))
            result.audio_message_id = notifier.send_audio(thread_id, path)
        except DeliveryError as exc:
            result.audio_error = str(exc)
            result.audio_uncertain = exc.uncertain
    return result
