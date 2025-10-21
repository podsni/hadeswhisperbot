from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import requests

from .groq_service import TranscriptionResult

logger = logging.getLogger(__name__)

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class DeepgramTranscriber:
    """Wrapper around Deepgram's transcription API."""

    provider_name = "deepgram"
    max_payload_bytes = 50 * 1024 * 1024  # Deepgram streaming uploads support up to 50MB per request.
    available_models = ("whisper", "nova-3")

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper",
        language: str = "en",
        smart_format: bool = True,
        detect_language: bool = True,
        timeout: int = 300,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.language = language
        self.smart_format = smart_format
        self.detect_language = detect_language
        self.timeout = timeout

    def transcribe(self, file_path: Path) -> TranscriptionResult:
        logger.info("Submitting %s to Deepgram model %s", file_path.name, self.model)
        params = {
            "model": self.model,
            "language": self.language,
            "smart_format": "true" if self.smart_format else "false",
        }

        if self.detect_language:
            params.pop("language", None)
            params["detect_language"] = "true"
        elif self.language:
            params["language"] = self.language

        with file_path.open("rb") as audio_fp:
            response = requests.post(
                DEEPGRAM_URL,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "application/octet-stream",
                },
                params=params,
                data=audio_fp,
                timeout=self.timeout,
            )

        response.raise_for_status()
        payload = response.json()
        text, segments = self._parse_response(payload)
        if not text:
            raise ValueError("Deepgram API response missing transcription text.")
        return TranscriptionResult(text=text, segments=segments)

    def with_model(self, model: str) -> "DeepgramTranscriber":
        selected = model if model in self.available_models else self.model
        return DeepgramTranscriber(
            api_key=self.api_key,
            model=selected,
            language=self.language,
            smart_format=self.smart_format,
            detect_language=self.detect_language,
            timeout=self.timeout,
        )

    def _parse_response(self, payload: dict) -> tuple[str, Optional[List[dict]]]:
        results = payload.get("results", {})
        channels = results.get("channels") or []
        if not channels:
            return "", None

        alternatives = channels[0].get("alternatives") or []
        if not alternatives:
            return "", None

        best = alternatives[0]
        transcript = best.get("transcript", "").strip()
        words = best.get("words") or []
        if not isinstance(words, list):
            words = []

        segments = self._build_segments(words)
        return transcript, segments if segments else None

    def _build_segments(self, words: List[dict]) -> List[dict]:
        if not words:
            return []

        segments: List[dict] = []
        current_words: List[str] = []
        start_time: Optional[float] = None
        last_end: Optional[float] = None

        def flush_segment() -> None:
            nonlocal current_words, start_time, last_end
            if not current_words:
                return
            text = " ".join(current_words).strip()
            if text:
                segments.append(
                    {
                        "start": start_time or 0.0,
                        "end": last_end or (start_time or 0.0),
                        "text": text,
                    }
                )
            current_words = []
            start_time = None
            last_end = None

        for word_info in words:
            word = word_info.get("punctuated_word") or word_info.get("word") or ""
            if not word:
                continue

            word_start = word_info.get("start")
            word_end = word_info.get("end")

            if start_time is None and word_start is not None:
                start_time = float(word_start)

            if start_time is not None and word_start is not None and last_end is not None:
                if float(word_start) - float(last_end) > 2.0:
                    flush_segment()
                    start_time = float(word_start)

            current_words.append(word)
            if word_end is not None:
                last_end = float(word_end)

            if word.endswith((".", "?", "!")):
                flush_segment()

        flush_segment()
        return segments
