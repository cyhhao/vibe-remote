"""Audio transcription helpers for inbound file attachments."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import aiohttp

from config.v2_config import AudioAsrConfig, V2Config
from modules.im.base import FileAttachment

logger = logging.getLogger(__name__)

_SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


@dataclass
class AudioTranscript:
    attachment_name: str
    local_path: str
    text: str
    duration_ms: int | None = None


@dataclass
class AudioAsrRuntimeConfig:
    base_url: str
    instance_id: str
    device_secret: str


class AudioAsrService:
    """Transcribe downloaded audio attachments through AVIBE ASR."""

    def __init__(self, config: V2Config):
        self.config = config

    def _get_audio_asr_config(self) -> AudioAsrConfig:
        return getattr(self.config, "audio_asr", None) or AudioAsrConfig()

    def _runtime_config(self) -> AudioAsrRuntimeConfig | None:
        cloud = getattr(getattr(self.config, "remote_access", None), "vibe_cloud", None)
        if not cloud:
            return None
        if not getattr(cloud, "enabled", False):
            return None
        base_url = (getattr(cloud, "backend_url", "") or "").strip().rstrip("/")
        instance_id = (getattr(cloud, "instance_id", "") or "").strip()
        device_secret = (getattr(cloud, "instance_secret", "") or "").strip()
        if not base_url or not instance_id or not device_secret:
            return None
        return AudioAsrRuntimeConfig(base_url=base_url, instance_id=instance_id, device_secret=device_secret)

    def _endpoint_url(self, runtime: AudioAsrRuntimeConfig) -> str:
        asr_config = self._get_audio_asr_config()
        endpoint_path = (asr_config.endpoint_path or "/v1/audio/transcriptions").strip()
        if not endpoint_path.startswith("/"):
            endpoint_path = f"/{endpoint_path}"
        return urljoin(f"{runtime.base_url}/", endpoint_path.lstrip("/"))

    def is_available(self) -> bool:
        asr_config = self._get_audio_asr_config()
        return bool(asr_config.enabled and self._runtime_config())

    def is_audio_attachment(self, attachment: FileAttachment) -> bool:
        mimetype = (attachment.mimetype or "").lower()
        if mimetype.startswith("audio/"):
            if mimetype == "audio/silk":
                return False
            return True
        name = attachment.name or attachment.local_path or ""
        suffix = Path(name).suffix.lower()
        return suffix in _SUPPORTED_AUDIO_EXTENSIONS

    def eligible_attachments(self, attachments: Iterable[FileAttachment]) -> list[FileAttachment]:
        asr_config = self._get_audio_asr_config()
        eligible: list[FileAttachment] = []
        for attachment in attachments:
            if not attachment.local_path or not self.is_audio_attachment(attachment):
                continue
            if asr_config.max_file_bytes is not None and attachment.size and attachment.size > asr_config.max_file_bytes:
                logger.info(
                    "Skipping audio ASR for %s: file size %s exceeds configured max %s",
                    attachment.name,
                    attachment.size,
                    asr_config.max_file_bytes,
                )
                continue
            eligible.append(attachment)
        return eligible

    async def transcribe_attachments(self, attachments: list[FileAttachment]) -> list[AudioTranscript]:
        asr_config = self._get_audio_asr_config()
        if not asr_config.enabled:
            return []
        runtime = self._runtime_config()
        if not runtime:
            logger.info("Skipping audio ASR: Vibe Cloud pairing credentials are unavailable")
            return []
        eligible = self.eligible_attachments(attachments)
        if not eligible:
            return []

        timeout_seconds = max(0.1, float(asr_config.timeout_seconds or 60.0))
        deadline = time.monotonic() + timeout_seconds
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [self._transcribe_one(session, runtime, attachment, deadline) for attachment in eligible]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        transcripts: list[AudioTranscript] = []
        for result in results:
            if isinstance(result, AudioTranscript):
                transcripts.append(result)
            elif isinstance(result, Exception):
                logger.warning("Audio ASR skipped after error: %s", result)
        return transcripts

    async def _transcribe_one(
        self,
        session: aiohttp.ClientSession,
        runtime: AudioAsrRuntimeConfig,
        attachment: FileAttachment,
        deadline: float,
    ) -> AudioTranscript | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        path = Path(attachment.local_path or "")
        if not path.is_file():
            logger.warning("Skipping audio ASR for %s: local file is unavailable", attachment.name)
            return None

        asr_config = self._get_audio_asr_config()
        mimetype = attachment.mimetype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        start = time.monotonic()
        form = aiohttp.FormData()
        form.add_field("model", asr_config.model)
        form.add_field("response_format", "json")

        with path.open("rb") as handle:
            form.add_field(
                "file",
                handle,
                filename=attachment.name or path.name,
                content_type=mimetype,
            )
            try:
                async with session.post(
                    self._endpoint_url(runtime),
                    data=form,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Vibe Remote/dev",
                        "X-Vibe-Instance-Id": runtime.instance_id,
                        "X-Vibe-Device-Secret": runtime.device_secret,
                    },
                    timeout=aiohttp.ClientTimeout(total=max(0.1, remaining)),
                ) as response:
                    payload: dict[str, Any] = {}
                    try:
                        payload = await response.json(content_type=None)
                    except Exception:
                        text = await response.text()
                        payload = {"error": text[:200]}
                    duration_ms = int((time.monotonic() - start) * 1000)
                    if response.status < 200 or response.status >= 300:
                        logger.warning(
                            "Audio ASR failed for %s: status=%s mimetype=%s duration_ms=%s",
                            attachment.name,
                            response.status,
                            mimetype,
                            duration_ms,
                        )
                        return None
            except asyncio.TimeoutError:
                logger.warning("Audio ASR timed out for %s", attachment.name)
                return None
            except Exception as exc:
                logger.warning("Audio ASR request failed for %s: %s", attachment.name, exc)
                return None

        text = str(payload.get("text") or "").strip()
        if not text:
            logger.warning("Audio ASR returned empty transcript for %s", attachment.name)
            return None
        return AudioTranscript(
            attachment_name=attachment.name or path.name,
            local_path=str(path),
            text=text,
            duration_ms=duration_ms,
        )


def format_audio_transcripts_block(transcripts: list[AudioTranscript]) -> str:
    if not transcripts:
        return ""
    lines = ["[Audio Transcripts]"]
    for transcript in transcripts:
        name = transcript.attachment_name or Path(transcript.local_path).name
        text = transcript.text.replace("\r", " ").strip()
        lines.append(f"- {name}: {text}")
    return "\n".join(lines)


def append_audio_transcripts_to_message(message: str, transcripts: list[AudioTranscript]) -> str:
    block = format_audio_transcripts_block(transcripts)
    if not block:
        return message
    if not message or not message.strip():
        return block
    return f"{message}\n\n{block}"


def format_audio_transcript_echo(transcripts: list[AudioTranscript]) -> str:
    if not transcripts:
        return ""
    if len(transcripts) == 1:
        return f"Voice transcript:\n{transcripts[0].text.strip()}"
    lines = ["Voice transcripts:"]
    for transcript in transcripts:
        name = transcript.attachment_name or Path(transcript.local_path).name
        lines.append(f"- {name}: {transcript.text.strip()}")
    return "\n".join(lines)
