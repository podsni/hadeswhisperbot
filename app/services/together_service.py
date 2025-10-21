from __future__ import annotations

import logging
from pathlib import Path

import requests

from .groq_service import TranscriptionResult

logger = logging.getLogger(__name__)

TOGETHER_URL = "https://api.together.xyz/v1/audio/transcriptions"


class TogetherTranscriber:
    """Wrapper around Together AI Whisper transcription API."""

    provider_name = "together"
    max_payload_bytes = 200 * 1024 * 1024  # Together AI supports up to 200MB

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "openai/whisper-large-v3",
        timeout: int = 300,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def transcribe(self, file_path: Path) -> TranscriptionResult:
        """
        Transcribe audio using Together AI Whisper API.

        Args:
            file_path: Path to audio file

        Returns:
            TranscriptionResult with text and segments

        Raises:
            ValueError: If API response is invalid
            HTTPError: If API request fails
        """
        logger.info("Submitting %s to Together AI model %s", file_path.name, self.model)

        with file_path.open("rb") as audio_fp:
            response = requests.post(
                TOGETHER_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (file_path.name, audio_fp, "audio/mpeg")},
                data={
                    "model": self.model,
                    "response_format": "verbose_json",
                },
                timeout=self.timeout,
            )

        response.raise_for_status()
        payload = response.json()

        # Parse response (similar to Groq format)
        text = payload.get("text")
        segments = payload.get("segments")

        if not text and not segments:
            raise ValueError("Together AI API response missing transcription text.")

        # Build text from segments if needed
        if not text and isinstance(segments, list):
            text = " ".join(
                segment.get("text", "").strip()
                for segment in segments
                if isinstance(segment, dict) and segment.get("text")
            )

        if text is None:
            raise ValueError("Unable to parse Together AI transcription response.")

        # Normalize segments to list
        normalized_segments = list(segments) if isinstance(segments, list) else None

        return TranscriptionResult(text=text, segments=normalized_segments)
