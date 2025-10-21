from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
from telethon.sessions import StringSession

from ..config import TelegramAPICredentials

logger = logging.getLogger(__name__)


@dataclass
class APIStatus:
    """Status tracking untuk single API credentials."""

    credentials: TelegramAPICredentials
    is_available: bool = True
    flood_wait_until: Optional[datetime] = None
    last_success: Optional[datetime] = None
    total_requests: int = 0
    total_failures: int = 0
    session_string: Optional[str] = None
    client: Optional[TelegramClient] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_requests == 0:
            return 100.0
        return (self.total_requests - self.total_failures) / self.total_requests * 100

    def is_in_flood_wait(self) -> bool:
        """Check if this API is currently in FloodWait."""
        if not self.flood_wait_until:
            return False
        return datetime.utcnow() < self.flood_wait_until

    def mark_flood_wait(self, seconds: int) -> None:
        """Mark API as in FloodWait."""
        self.flood_wait_until = datetime.utcnow() + timedelta(seconds=seconds)
        self.is_available = False
        logger.warning(
            "API %s marked as FloodWait until %s (%d seconds)",
            self.credentials.name,
            self.flood_wait_until.strftime("%H:%M:%S"),
            seconds,
        )

    def mark_success(self) -> None:
        """Mark successful request."""
        self.last_success = datetime.utcnow()
        self.total_requests += 1
        self.is_available = True
        # Clear flood wait if it was set
        if self.flood_wait_until:
            if datetime.utcnow() >= self.flood_wait_until:
                self.flood_wait_until = None
                logger.info("API %s recovered from FloodWait", self.credentials.name)

    def mark_failure(self) -> None:
        """Mark failed request."""
        self.total_requests += 1
        self.total_failures += 1

    def can_use(self) -> bool:
        """Check if this API can be used right now."""
        if self.is_in_flood_wait():
            return False
        return self.is_available


class TelegramAPIRotator:
    """
    Manages multiple Telegram API credentials with automatic rotation.

    Features:
    - Automatic failover when FloodWait occurs
    - Session persistence for each API
    - Health tracking and stats
    - Intelligent API selection (least used, best success rate)
    """

    def __init__(
        self,
        credentials_list: List[TelegramAPICredentials],
        bot_token: str,
        session_dir: Optional[Path] = None,
    ) -> None:
        self.bot_token = bot_token
        self.session_dir = session_dir or Path.home() / ".transhades_sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Initialize API status tracking
        self.apis: Dict[str, APIStatus] = {}
        for creds in credentials_list:
            self.apis[creds.name] = APIStatus(credentials=creds)

        self._current_api_name: Optional[str] = None
        self._lock = asyncio.Lock()

        logger.info(
            "API Rotator initialized with %d API credentials: %s",
            len(self.apis),
            ", ".join(self.apis.keys()),
        )

    async def get_client(self) -> tuple[TelegramClient, str]:
        """
        Get available Telegram client with automatic rotation.

        Returns:
            tuple: (TelegramClient, api_name)

        Raises:
            RuntimeError: If no API is available
        """
        async with self._lock:
            # Try to get available API
            api_name = await self._select_best_api()
            if not api_name:
                raise RuntimeError(
                    "❌ Semua API sedang dalam FloodWait!\n\n"
                    "Bot akan otomatis recover saat FloodWait selesai.\n"
                    "Sementara itu, file duplikat masih bisa diproses dari cache! ✨"
                )

            api_status = self.apis[api_name]

            # Get or create client
            if not api_status.client or not api_status.client.is_connected():
                await self._create_client(api_status)

            return api_status.client, api_name

    async def _select_best_api(self) -> Optional[str]:
        """
        Select best available API based on:
        1. Not in FloodWait
        2. Best success rate
        3. Least recently used
        """
        available_apis = []

        for name, api_status in self.apis.items():
            if api_status.can_use():
                available_apis.append((name, api_status))

        if not available_apis:
            logger.warning("No available APIs - all in FloodWait")
            return None

        # Sort by success rate (descending) and last_success (ascending)
        available_apis.sort(
            key=lambda x: (
                -x[1].success_rate,
                x[1].last_success or datetime.min,
            )
        )

        selected = available_apis[0][0]
        logger.info(
            "Selected API: %s (success rate: %.1f%%)",
            selected,
            self.apis[selected].success_rate,
        )
        return selected

    async def _create_client(self, api_status: APIStatus) -> None:
        """Create and authorize Telegram client for API."""
        creds = api_status.credentials

        # Load session from file if exists
        session_file = self.session_dir / f"{creds.name}.session"
        if session_file.exists():
            try:
                api_status.session_string = session_file.read_text().strip()
                logger.info("Loaded session for API %s", creds.name)
            except Exception as e:
                logger.warning("Failed to load session for %s: %s", creds.name, e)
                api_status.session_string = None

        # Create client
        session = (
            StringSession(api_status.session_string)
            if api_status.session_string
            else StringSession()
        )
        api_status.client = TelegramClient(
            session=session,
            api_id=creds.api_id,
            api_hash=creds.api_hash,
        )

        await api_status.client.connect()

        # Authorize if needed
        if not await api_status.client.is_user_authorized():
            logger.info("Authorizing API %s with Telegram...", creds.name)
            try:
                await api_status.client.sign_in(bot_token=self.bot_token)

                # Save session
                session_str = api_status.client.session.save()
                session_file.write_text(session_str)
                session_file.chmod(0o600)
                logger.info("API %s authorized and session saved", creds.name)

            except SessionPasswordNeededError as exc:
                await api_status.client.disconnect()
                api_status.is_available = False
                raise RuntimeError(
                    f"API {creds.name} membutuhkan password tambahan."
                ) from exc
            except FloodWaitError as flood_err:
                await api_status.client.disconnect()
                api_status.mark_flood_wait(flood_err.seconds)
                raise RuntimeError(
                    f"API {creds.name} kena FloodWait saat authorization."
                ) from flood_err

    async def mark_request_result(
        self, api_name: str, success: bool, flood_wait_seconds: Optional[int] = None
    ) -> None:
        """
        Mark request result for API tracking.

        Args:
            api_name: API name
            success: Whether request was successful
            flood_wait_seconds: If FloodWait occurred, how many seconds
        """
        async with self._lock:
            if api_name not in self.apis:
                return

            api_status = self.apis[api_name]

            if success:
                api_status.mark_success()
            else:
                api_status.mark_failure()

            if flood_wait_seconds:
                api_status.mark_flood_wait(flood_wait_seconds)
                logger.warning(
                    "API %s will rotate to next available API",
                    api_name,
                )

    async def get_stats(self) -> Dict[str, dict]:
        """Get statistics for all APIs."""
        stats = {}
        for name, api_status in self.apis.items():
            stats[name] = {
                "available": api_status.can_use(),
                "in_flood_wait": api_status.is_in_flood_wait(),
                "flood_wait_until": (
                    api_status.flood_wait_until.strftime("%H:%M:%S")
                    if api_status.flood_wait_until
                    else None
                ),
                "success_rate": f"{api_status.success_rate:.1f}%",
                "total_requests": api_status.total_requests,
                "total_failures": api_status.total_failures,
                "last_success": (
                    api_status.last_success.strftime("%Y-%m-%d %H:%M:%S")
                    if api_status.last_success
                    else "Never"
                ),
            }
        return stats

    async def close_all(self) -> None:
        """Close all Telegram client connections."""
        for name, api_status in self.apis.items():
            if api_status.client and api_status.client.is_connected():
                await api_status.client.disconnect()
                logger.info("Disconnected API %s", name)

    async def force_rotate(self) -> Optional[str]:
        """
        Force rotation to next available API.

        Returns:
            Name of new API or None if no API available
        """
        async with self._lock:
            return await self._select_best_api()

    def get_available_count(self) -> int:
        """Get count of currently available APIs."""
        return sum(1 for api in self.apis.values() if api.can_use())

    def get_total_count(self) -> int:
        """Get total count of APIs."""
        return len(self.apis)
