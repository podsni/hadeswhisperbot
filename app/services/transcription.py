from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional


class TranscriberRegistry:
    """Registry of available transcription backends."""

    def __init__(self, default_provider: str, transcribers: Dict[str, object]) -> None:
        if not transcribers:
            raise ValueError("At least one transcriber must be configured.")
        if default_provider not in transcribers:
            raise ValueError("Default provider must exist within the transcribers mapping.")
        self._default = default_provider
        self._transcribers = transcribers

    @property
    def default_provider(self) -> str:
        return self._default

    def providers(self) -> Iterable[str]:
        return self._transcribers.keys()

    def get(self, name: str) -> Optional[object]:
        return self._transcribers.get(name)


@dataclass
class ProviderPreferences:
    """In-memory chat-level provider preferences."""

    default: str
    _preferences: Dict[int, str]

    def __init__(self, default: str) -> None:
        self.default = default
        self._preferences = {}

    def set(self, chat_id: int, provider: str) -> None:
        self._preferences[chat_id] = provider

    def get(self, chat_id: int) -> str:
        return self._preferences.get(chat_id, self.default)

    def clear(self, chat_id: int) -> None:
        self._preferences.pop(chat_id, None)


class DeepgramModelPreferences:
    """Store per-chat Deepgram model selections."""

    def __init__(self, default_model: str) -> None:
        self.default_model = default_model
        self._models: Dict[int, str] = {}

    def set(self, chat_id: int, model: str) -> None:
        self._models[chat_id] = model

    def get(self, chat_id: int) -> str:
        return self._models.get(chat_id, self.default_model)

    def clear(self, chat_id: int) -> None:
        self._models.pop(chat_id, None)
