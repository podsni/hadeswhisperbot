from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


@dataclass
class TranscriptionResult:
    """Normalized representation of a Groq Whisper transcription response."""

    text: str
    segments: Optional[List[dict]] = None

    def strip_text(self) -> str:
        return (self.text or "").strip()

    def to_plain_text(self) -> str:
        """Return the transcription text without timestamps."""
        return self.strip_text()

    def to_srt(self) -> str:
        """Create an SRT caption file from the segment metadata, if available."""
        if not self.segments:
            raise ValueError("Segments are required to build SRT output.")

        lines = []
        for idx, segment in enumerate(self.segments, start=1):
            start = self._format_timestamp(segment.get("start"))
            end = self._format_timestamp(segment.get("end"))
            text = segment.get("text", "").strip()
            if not text:
                continue
            lines.append(str(idx))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")  # Blank line between captions
        return "\n".join(lines).strip()

    @staticmethod
    def _format_timestamp(seconds: Optional[float]) -> str:
        if seconds is None:
            return "00:00:00,000"
        micro = int(round(seconds * 1_000_000))
        delta = timedelta(microseconds=micro)
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        milliseconds = int(delta.microseconds / 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


class GroqTranscriber:
    """Wrapper around Groq Whisper transcription API."""

    provider_name = "groq"
    max_payload_bytes = 200 * 1024 * 1024  # Groq Whisper handles payloads up to 200MB.

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-large-v3",
        timeout: int = 300,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def transcribe(self, file_path: Path) -> TranscriptionResult:
        logger.info("Submitting %s to Groq Whisper model %s", file_path.name, self.model)
        with file_path.open("rb") as audio_fp:
            response = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={
                    "model": self.model,
                    "temperature": "0",
                    "response_format": "verbose_json",
                },
                files={"file": (file_path.name, audio_fp)},
                timeout=self.timeout,
            )

        response.raise_for_status()
        payload = response.json()

        text = payload.get("text")
        segments = payload.get("segments")

        if not text and not segments:
            raise ValueError("Groq API response missing transcription text.")

        if not text and isinstance(segments, Iterable):
            text = " ".join(
                segment.get("text", "").strip()
                for segment in segments
                if isinstance(segment, dict) and segment.get("text")
            )

        if text is None:
            raise ValueError("Unable to parse Groq transcription response.")

        normalized_segments = list(segments) if isinstance(segments, list) else None
        return TranscriptionResult(text=text, segments=normalized_segments)
