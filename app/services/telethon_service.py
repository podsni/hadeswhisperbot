from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

from telethon.errors import FloodWaitError, RPCError

from .api_rotator import TelegramAPIRotator

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[int, int], None]]


class TelethonDownloadService:
    """
    Acquire media via MTProto with automatic API rotation.

    Features:
    - Multi-API support with automatic failover
    - FloodWait handling with API rotation
    - Session persistence per API
    - Duplicate detection before download
    """

    def __init__(
        self,
        api_rotator: TelegramAPIRotator,
    ) -> None:
        self.api_rotator = api_rotator
        self._lock = asyncio.Lock()

    async def download_media(
        self,
        chat_id: int,
        message_id: int,
        file_path: str,
        progress_callback: ProgressCallback = None,
        max_retries: int = 3,
    ) -> None:
        """
        Download media with automatic API rotation on FloodWait.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID containing media
            file_path: Destination file path
            progress_callback: Optional progress callback
            max_retries: Maximum retry attempts across all APIs
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                # Get available client (will auto-rotate if needed)
                client, api_name = await self.api_rotator.get_client()

                logger.info(
                    "Downloading with API %s (attempt %d/%d)",
                    api_name,
                    attempt + 1,
                    max_retries,
                )

                entity = await client.get_entity(chat_id)
                telegram_message = await client.get_messages(entity, ids=message_id)

                if not telegram_message:
                    raise RuntimeError("Tidak menemukan media pada pesan tersebut.")

                result = await client.download_media(
                    telegram_message,
                    file=file_path,
                    progress_callback=progress_callback,
                )

                if not result:
                    raise RuntimeError("Download media melalui Telethon gagal.")

                # Mark success
                await self.api_rotator.mark_request_result(api_name, success=True)
                logger.info(
                    "✓ Media downloaded successfully with API %s to %s",
                    api_name,
                    file_path,
                )
                return

            except FloodWaitError as flood_err:
                wait_time = flood_err.seconds

                logger.warning(
                    "⚠️ FloodWait on API %s: %d seconds",
                    api_name if "api_name" in locals() else "unknown",
                    wait_time,
                )

                # Mark this API as in FloodWait
                if "api_name" in locals():
                    await self.api_rotator.mark_request_result(
                        api_name,
                        success=False,
                        flood_wait_seconds=wait_time,
                    )

                # Check if we have other APIs available
                available_count = self.api_rotator.get_available_count()
                total_count = self.api_rotator.get_total_count()

                if available_count > 0:
                    logger.info(
                        "🔄 Rotating to another API (%d/%d available)",
                        available_count,
                        total_count,
                    )
                    # Rotate will happen automatically on next get_client() call
                    await asyncio.sleep(1)  # Small delay before retry
                    continue
                elif wait_time <= 120:
                    # All APIs in FloodWait, but wait time is reasonable
                    logger.info(
                        "All APIs in FloodWait, waiting %d seconds...",
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # All APIs in FloodWait with long wait time
                    raise RuntimeError(
                        f"⏳ **Semua API sedang dalam FloodWait**\n\n"
                        f"API yang tersedia: {total_count}\n"
                        f"Waktu tunggu: ~{wait_time // 60} menit\n\n"
                        f"💡 **Bot tetap berjalan!**\n"
                        f"File duplikat masih bisa diproses dari cache ✨\n\n"
                        f"Untuk file baru, silakan:\n"
                        f"• Tunggu ~{wait_time // 60} menit, atau\n"
                        f"• Tambahkan API credentials lagi di .env"
                    ) from flood_err

            except RPCError as exc:
                if "api_name" in locals():
                    await self.api_rotator.mark_request_result(api_name, success=False)

                if attempt < max_retries - 1:
                    logger.warning(
                        "RPC error on attempt %d/%d: %s. Retrying with different API...",
                        attempt + 1,
                        max_retries,
                        exc,
                    )
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue
                else:
                    raise RuntimeError(
                        f"Gagal mengambil media setelah {max_retries} percobaan: {exc}"
                    ) from exc

            except RuntimeError as runtime_err:
                # Re-raise RuntimeError (like "no API available")
                raise

            except Exception as exc:
                if "api_name" in locals():
                    await self.api_rotator.mark_request_result(api_name, success=False)

                logger.exception("Unexpected error during media download")
                last_error = exc

                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                else:
                    raise RuntimeError(f"Error downloading media: {exc}") from exc

        # If we exhausted all retries
        if last_error:
            raise RuntimeError(
                f"Gagal download setelah {max_retries} percobaan"
            ) from last_error

    async def get_file_unique_id(self, chat_id: int, message_id: int) -> Optional[str]:
        """
        Get unique file ID from Telegram message without downloading.
        Useful for duplicate detection before download.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID containing media

        Returns:
            Unique file ID string or None if no media
        """
        try:
            client, api_name = await self.api_rotator.get_client()

            entity = await client.get_entity(chat_id)
            telegram_message = await client.get_messages(entity, ids=message_id)

            if not telegram_message or not telegram_message.media:
                return None

            # Get unique file ID from media
            if hasattr(telegram_message.media, "document"):
                # For documents, videos, audio
                doc = telegram_message.media.document
                file_id = f"{doc.id}_{doc.access_hash}"

                # Mark success
                await self.api_rotator.mark_request_result(api_name, success=True)
                return file_id

            elif hasattr(telegram_message.media, "photo"):
                # For photos
                photo = telegram_message.media.photo
                file_id = f"{photo.id}_{photo.access_hash}"

                await self.api_rotator.mark_request_result(api_name, success=True)
                return file_id

            return None

        except FloodWaitError as flood_err:
            # Mark FloodWait but don't fail - this is just for cache check
            if "api_name" in locals():
                await self.api_rotator.mark_request_result(
                    api_name,
                    success=False,
                    flood_wait_seconds=flood_err.seconds,
                )
            logger.warning(
                "FloodWait when getting file_id, will check cache only: %s",
                flood_err,
            )
            return None

        except Exception as e:
            if "api_name" in locals():
                await self.api_rotator.mark_request_result(api_name, success=False)
            logger.warning("Failed to get file unique ID: %s", e)
            return None

    async def close(self) -> None:
        """Close all Telegram client connections."""
        await self.api_rotator.close_all()
        logger.info("All Telegram clients disconnected")

    async def get_stats(self) -> dict:
        """Get statistics for all APIs."""
        return await self.api_rotator.get_stats()
