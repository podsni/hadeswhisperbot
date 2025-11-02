"""Translation service for multi-language support."""

import logging
from typing import Optional, Dict, Any, List
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_SEGMENT_TRANSLATIONS = 200


# Language code mapping
LANGUAGE_CODES = {
    "id": "Indonesian",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ar": "Arabic",
    "hi": "Hindi",
    "th": "Thai",
    "vi": "Vietnamese",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "cs": "Czech",
    "hu": "Hungarian",
    "ro": "Romanian",
}


@dataclass
class TranslationResult:
    """Result of a translation."""

    text: str
    source_language: str
    target_language: str
    provider: str
    segment_translations: Optional[List[str]] = None


class TranslationService:
    """
    Translation service with multiple provider support.

    Supports:
    - LibreTranslate (free, self-hosted or public)
    - Groq (using LLM for translation)
    - Together AI (using LLM for translation)
    """

    def __init__(
        self,
        groq_api_key: Optional[str] = None,
        together_api_key: Optional[str] = None,
        libretranslate_url: Optional[str] = None,
        libretranslate_api_key: Optional[str] = None,
    ):
        """Initialize translation service."""
        self.groq_api_key = groq_api_key
        self.together_api_key = together_api_key
        self.libretranslate_url = libretranslate_url or "https://libretranslate.com"
        self.libretranslate_api_key = libretranslate_api_key

        logger.info(
            f"Translation service initialized with providers: "
            f"Groq={bool(groq_api_key)}, Together={bool(together_api_key)}, "
            f"LibreTranslate={bool(libretranslate_url)}"
        )

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
        provider: Optional[str] = None,
        segments: Optional[List[Dict[str, Any]]] = None,
    ) -> TranslationResult:
        """
        Translate text to target language.

        Args:
            text: Text to translate
            target_language: Target language code (e.g., 'en', 'id')
            source_language: Source language code (auto-detect if None)
            provider: Preferred provider ('groq', 'together', 'libretranslate')
            segments: Optional segment metadata for subtitle alignment

        Returns:
            TranslationResult object
        """
        # Validate target language
        if target_language not in LANGUAGE_CODES:
            raise ValueError(
                f"Unsupported target language: {target_language}. "
                f"Supported: {', '.join(LANGUAGE_CODES.keys())}"
            )

        result: TranslationResult

        # Try providers in order
        if provider == "groq" and self.groq_api_key:
            result = await self._translate_with_groq(
                text, target_language, source_language
            )
        elif provider == "together" and self.together_api_key:
            result = await self._translate_with_together(
                text, target_language, source_language
            )
        elif provider == "libretranslate":
            result = await self._translate_with_libretranslate(
                text, target_language, source_language
            )
        else:
            # Auto-select available provider
            if self.groq_api_key:
                result = await self._translate_with_groq(
                    text, target_language, source_language
                )
            elif self.together_api_key:
                result = await self._translate_with_together(
                    text, target_language, source_language
                )
            else:
                result = await self._translate_with_libretranslate(
                    text, target_language, source_language
                )

        segment_translations = await self._maybe_translate_segments(
            base_result=result,
            target_language=target_language,
            fallback_source=source_language,
            segments=segments,
        )
        if segment_translations:
            result.segment_translations = segment_translations

        return result

    async def _translate_with_groq(
        self, text: str, target_language: str, source_language: Optional[str] = None
    ) -> TranslationResult:
        """Translate using Groq API."""
        target_lang_name = LANGUAGE_CODES.get(target_language, target_language)

        source_info = ""
        if source_language:
            source_lang_name = LANGUAGE_CODES.get(source_language, source_language)
            source_info = f" from {source_lang_name}"

        prompt = f"Translate the following text{source_info} to {target_lang_name}. Provide only the translation, no explanations:\n\n{text}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a professional translator. Provide only the translation without any explanations or notes.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
            )
            response.raise_for_status()
            data = response.json()

            translated_text = data["choices"][0]["message"]["content"].strip()

            # Detect source language if not provided
            if not source_language:
                source_language = await self._detect_language_groq(text)

            return TranslationResult(
                text=translated_text,
                source_language=source_language or "auto",
                target_language=target_language,
                provider="groq",
            )

    async def _translate_with_together(
        self, text: str, target_language: str, source_language: Optional[str] = None
    ) -> TranslationResult:
        """Translate using Together AI API."""
        target_lang_name = LANGUAGE_CODES.get(target_language, target_language)

        source_info = ""
        if source_language:
            source_lang_name = LANGUAGE_CODES.get(source_language, source_language)
            source_info = f" from {source_lang_name}"

        prompt = f"Translate the following text{source_info} to {target_lang_name}. Provide only the translation, no explanations:\n\n{text}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.together.xyz/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.together_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a professional translator. Provide only the translation without any explanations or notes.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
            )
            response.raise_for_status()
            data = response.json()

            translated_text = data["choices"][0]["message"]["content"].strip()

            # Detect source language if not provided
            if not source_language:
                source_language = "auto"

            return TranslationResult(
                text=translated_text,
                source_language=source_language,
                target_language=target_language,
                provider="together",
            )

    async def _translate_with_libretranslate(
        self, text: str, target_language: str, source_language: Optional[str] = None
    ) -> TranslationResult:
        """Translate using LibreTranslate API."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "q": text,
                "target": target_language,
                "format": "text",
            }

            if source_language:
                payload["source"] = source_language
            else:
                payload["source"] = "auto"

            if self.libretranslate_api_key:
                payload["api_key"] = self.libretranslate_api_key

            response = await client.post(
                f"{self.libretranslate_url}/translate",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            translated_text = data["translatedText"]
            detected_language = data.get("detectedLanguage", {})

            if isinstance(detected_language, dict):
                source_lang = detected_language.get(
                    "language", source_language or "auto"
                )
            else:
                source_lang = source_language or "auto"

            return TranslationResult(
                text=translated_text,
                source_language=source_lang,
                target_language=target_language,
                provider="libretranslate",
            )

    async def _maybe_translate_segments(
        self,
        base_result: TranslationResult,
        target_language: str,
        fallback_source: Optional[str],
        segments: Optional[List[Dict[str, Any]]],
    ) -> Optional[List[str]]:
        """
        Translate individual subtitle segments to preserve timing.

        Args:
            base_result: Primary translation result
            target_language: Target language code
            fallback_source: Optional source language hint
            segments: Original segment metadata with text/timestamps

        Returns:
            List of translated segment texts or None if unavailable
        """
        if not segments:
            return None

        if len(segments) > MAX_SEGMENT_TRANSLATIONS:
            logger.warning(
                "Skipping segment translations: %d segments exceed limit of %d.",
                len(segments),
                MAX_SEGMENT_TRANSLATIONS,
            )
            return None

        segment_texts: List[str] = []
        has_content = False
        for segment in segments:
            text = ""
            if isinstance(segment, dict):
                value = segment.get("text")
                if isinstance(value, str):
                    text = value.strip()
            if text:
                has_content = True
            segment_texts.append(text)

        if not has_content:
            return None

        provider = base_result.provider
        effective_source = (
            base_result.source_language
            if base_result.source_language and base_result.source_language != "auto"
            else fallback_source
        )

        translations: List[str] = []
        for text in segment_texts:
            if not text:
                translations.append("")
                continue

            try:
                if provider == "groq":
                    translated = await self._translate_with_groq(
                        text, target_language, effective_source
                    )
                elif provider == "together":
                    translated = await self._translate_with_together(
                        text, target_language, effective_source
                    )
                else:
                    translated = await self._translate_with_libretranslate(
                        text, target_language, effective_source
                    )
                translations.append(translated.text)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Segment translation failed via %s: %s", provider, exc, exc_info=True
                )
                translations.append("")

        return translations or None

    async def _detect_language_groq(self, text: str) -> str:
        """Detect language using Groq API."""
        # Take first 500 chars for detection
        sample = text[:500]

        prompt = f"Detect the language of this text and respond with only the ISO 639-1 language code (e.g., 'en', 'id', 'es'). Text: {sample}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 10,
                    },
                )
                response.raise_for_status()
                data = response.json()

                detected = data["choices"][0]["message"]["content"].strip().lower()
                # Extract just the language code
                detected = detected.replace("'", "").replace('"', "").strip()

                return detected if detected in LANGUAGE_CODES else "auto"
        except Exception as e:
            logger.warning(f"Language detection failed: {e}")
            return "auto"

    def get_supported_languages(self) -> Dict[str, str]:
        """Get dictionary of supported language codes and names."""
        return LANGUAGE_CODES.copy()

    def is_language_supported(self, language_code: str) -> bool:
        """Check if a language code is supported."""
        return language_code in LANGUAGE_CODES
