from __future__ import annotations

import asyncio
import io
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiogram import Router
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.chat_action import ChatActionSender
from requests import HTTPError
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from ..services import (
    DeepgramModelPreferences,
    ProviderPreferences,
    TranscriberRegistry,
    TelethonDownloadService,
    TranscriptionResult,
    TranscriptionDatabase,
    TranscriptionRecord,
)
from ..services.audio_optimizer import AudioOptimizer, TranscriptCache
from ..services.queue_service import TaskQueue, TranscriptionTask

logger = logging.getLogger(__name__)

router = Router()

TELEGRAM_FILE_DOWNLOAD_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB via MTProto.
TELEGRAM_MESSAGE_LIMIT = 4000
PROGRESS_BAR_THRESHOLD = 50 * 1024 * 1024  # Show progress bar for downloads >= 50MB.
DEFAULT_PAYLOAD_LIMIT = 200 * 1024 * 1024  # Fallback payload limit (~200MB).


@dataclass
class MediaMeta:
    display_name: str
    suffix: str
    file_size: Optional[int]


@router.message()
async def handle_media(
    message: Message,
    telethon_downloader: TelethonDownloadService,
    transcriber_registry: TranscriberRegistry,
    provider_preferences: ProviderPreferences,
    deepgram_model_preferences: DeepgramModelPreferences,
    audio_optimizer: AudioOptimizer,
    transcript_cache: Optional[TranscriptCache],
    task_queue: TaskQueue,
    transcription_db: Optional[TranscriptionDatabase] = None,
    compression_threshold_mb: int = 30,
) -> None:
    meta = _pick_media(message)
    if not meta:
        return

    if meta.file_size and meta.file_size > TELEGRAM_FILE_DOWNLOAD_LIMIT:
        await message.answer(
            "Ukuran file melebihi 2GB sehingga tidak bisa diunduh. "
            "Silakan kompres atau bagi menjadi beberapa bagian terlebih dahulu."
        )
        return

    download_path = _build_download_path(meta)
    cleanup_paths = {download_path}
    prepared_path: Path = download_path

    requested_provider = provider_preferences.get(message.chat.id)
    transcriber = transcriber_registry.get(requested_provider)
    if not transcriber:
        fallback = transcriber_registry.default_provider
        transcriber = transcriber_registry.get(fallback)
        provider_preferences.set(message.chat.id, fallback)
        requested_provider = fallback

    if not transcriber:
        await message.answer("Tidak ada provider transkripsi yang tersedia saat ini.")
        return

    provider_key = getattr(transcriber, "provider_name", requested_provider)
    provider_display = provider_key

    if provider_key == "deepgram":
        model = deepgram_model_preferences.get(message.chat.id)
        if hasattr(transcriber, "with_model"):
            transcriber = transcriber.with_model(model)
        provider_display = f"deepgram ({model})"

    payload_limit = getattr(transcriber, "max_payload_bytes", DEFAULT_PAYLOAD_LIMIT)

    # Submit to queue for async processing
    try:
        task_id = await task_queue.submit(
            chat_id=message.chat.id,
            message_id=message.message_id,
            file_path=download_path,
            provider=requested_provider,
            priority=0,
            processor=lambda task: _process_transcription_task(
                task=task,
                message=message,
                telethon_downloader=telethon_downloader,
                transcriber_registry=transcriber_registry,
                provider_preferences=provider_preferences,
                deepgram_model_preferences=deepgram_model_preferences,
                audio_optimizer=audio_optimizer,
                transcript_cache=transcript_cache,
                transcription_db=transcription_db,
                compression_threshold_mb=compression_threshold_mb,
                meta=meta,
            ),
        )

        queue_stats = await task_queue.get_stats()
        await message.answer(
            f"🎵 Audio Anda dalam antrian pemrosesan!\n\n"
            f"📋 Task ID: `{task_id[:8]}`\n"
            f"⏳ Posisi antrian: {queue_stats['queue_size']}\n"
            f"👷 Worker aktif: {queue_stats['active_workers']}/{task_queue.max_workers}\n\n"
            f"Hasil akan dikirim otomatis saat selesai."
        )
        logger.info(
            "Task %s submitted to queue for chat %s", task_id[:8], message.chat.id
        )

    except RuntimeError as rate_err:
        logger.warning("Rate limit exceeded for user %s: %s", message.chat.id, rate_err)
        await message.answer(
            "⚠️ Anda memiliki terlalu banyak task yang sedang diproses.\n"
            "Silakan tunggu task sebelumnya selesai terlebih dahulu."
        )


async def _process_transcription_task(
    task: TranscriptionTask,
    message: Message,
    telethon_downloader: TelethonDownloadService,
    transcriber_registry: TranscriberRegistry,
    provider_preferences: ProviderPreferences,
    deepgram_model_preferences: DeepgramModelPreferences,
    audio_optimizer: AudioOptimizer,
    transcript_cache: Optional[TranscriptCache],
    transcription_db: Optional[TranscriptionDatabase],
    compression_threshold_mb: int,
    meta: MediaMeta,
) -> None:
    """Process transcription task with caching and optimization."""
    download_path = task.file_path
    cleanup_paths = {download_path}

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            # Check cache BEFORE download using Telegram file_id
            file_unique_id = None
            cached_result = None

            if transcript_cache:
                try:
                    # Get Telegram unique file ID without downloading
                    file_unique_id = await telethon_downloader.get_file_unique_id(
                        message.chat.id, message.message_id
                    )

                    if file_unique_id:
                        # Check cache using Telegram file ID
                        cached_result = await transcript_cache.get(
                            f"tg_{file_unique_id}"
                        )

                        if cached_result:
                            logger.info(
                                "✨✨ Cache HIT by Telegram file_id! Skipping download."
                            )
                            text, segments = cached_result
                            result = TranscriptionResult(text=text, segments=segments)
                            await message.answer(
                                f"✨ **Hasil dari cache** (file sudah pernah diproses)!\n\n"
                                f"📁 File: {meta.display_name}\n"
                                f"⚡ Proses: Instant dari cache"
                            )
                            await _deliver_transcription(message, result)
                            return
                        else:
                            logger.info(
                                "Cache miss for file_id %s, proceeding with download",
                                file_unique_id[:16],
                            )
                except Exception as e:
                    logger.warning(
                        "Failed to check cache by file_id: %s. Continuing with download.",
                        e,
                    )

            # Download media
            logger.info(
                "Starting download for %s (%s bytes) in chat %s",
                meta.display_name,
                meta.file_size,
                message.chat.id,
            )
            await _download_media(telethon_downloader, message, download_path, meta)
            logger.info(
                "Download complete: %s (%s bytes)",
                download_path,
                download_path.stat().st_size if download_path.exists() else "unknown",
            )

            # Double-check cache with file hash (in case file_id check failed)
            file_hash = None
            if transcript_cache and not cached_result:
                file_hash = await audio_optimizer._compute_file_hash(download_path)
                cached_result = await transcript_cache.get(file_hash)
                if cached_result:
                    logger.info("✨ Cache hit for file hash %s", file_hash[:8])
                    text, segments = cached_result
                    result = TranscriptionResult(text=text, segments=segments)
                    await message.answer(
                        f"✨ Hasil dari cache (file sudah pernah diproses)!\n\n"
                        f"Provider: {task.provider}"
                    )
                    await _deliver_transcription(message, result)
                    return

            # Optimize audio
            compression_threshold_bytes = compression_threshold_mb * 1024 * 1024
            prepared_path = await asyncio.to_thread(
                _prepare_audio_for_transcription_optimized,
                download_path,
                meta.file_size,
                audio_optimizer,
                compression_threshold_bytes,
            )
            cleanup_paths.add(prepared_path)

            try:
                payload_size = prepared_path.stat().st_size
            except OSError:
                payload_size = None

            # Get transcriber again for this task
            requested_provider = task.provider
            transcriber = transcriber_registry.get(requested_provider)
            if not transcriber:
                fallback = transcriber_registry.default_provider
                transcriber = transcriber_registry.get(fallback)
                requested_provider = fallback

            provider_key = getattr(transcriber, "provider_name", requested_provider)
            provider_display = provider_key

            if provider_key == "deepgram":
                model = deepgram_model_preferences.get(message.chat.id)
                if hasattr(transcriber, "with_model"):
                    transcriber = transcriber.with_model(model)
                provider_display = f"deepgram ({model})"

            payload_limit = getattr(
                transcriber, "max_payload_bytes", DEFAULT_PAYLOAD_LIMIT
            )

            if payload_limit and payload_size and payload_size > payload_limit:
                logger.warning(
                    "Prepared audio %s is %s bytes, exceeds payload limit for provider %s.",
                    prepared_path,
                    payload_size,
                    provider_display,
                )
                limit_mb = payload_limit / (1024 * 1024)
                await message.answer(
                    "File sudah dikonversi, tetapi masih terlalu besar untuk "
                    f"provider {provider_display} (maks sekitar {limit_mb:.1f}MB). "
                    "Silakan kompres lagi atau kirim bagian yang lebih pendek."
                )
                return

            logger.info(
                "Starting transcription via %s for %s", provider_display, prepared_path
            )
            start_time = datetime.utcnow()
            result = await asyncio.to_thread(transcriber.transcribe, prepared_path)
            processing_time = (datetime.utcnow() - start_time).total_seconds()

            # Save to cache with BOTH file_id and file_hash
            if transcript_cache:
                # Save with file hash
                if not file_hash:
                    file_hash = await audio_optimizer._compute_file_hash(
                        download_path if download_path.exists() else prepared_path
                    )
                await transcript_cache.set(file_hash, result.text, result.segments)
                logger.info("💾 Cached transcript for hash %s", file_hash[:8])

                # Also save with Telegram file_id for faster future lookups
                if file_unique_id:
                    await transcript_cache.set(
                        f"tg_{file_unique_id}", result.text, result.segments
                    )
                    logger.info(
                        "💾 Cached transcript for file_id %s", file_unique_id[:16]
                    )

            # Save to database
            if transcription_db:
                try:
                    # Detect language from result if available
                    detected_language = None
                    if hasattr(result, "language"):
                        detected_language = result.language

                    # Get model info
                    model_name = None
                    if provider_key == "deepgram":
                        model_name = deepgram_model_preferences.get(message.chat.id)

                    record = TranscriptionRecord(
                        user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        file_id=file_unique_id or "unknown",
                        file_name=meta.display_name,
                        file_size=meta.file_size,
                        duration=None,  # Duration not available from current metadata
                        transcript=result.text,
                        detected_language=detected_language,
                        provider=provider_key,
                        model=model_name,
                        timestamp=datetime.utcnow().isoformat(),
                        processing_time=processing_time,
                        segments=result.segments,
                    )
                    record_id = transcription_db.add_transcription(record)
                    logger.info(
                        "💾 Saved transcription to database (ID: %d) for user %d",
                        record_id,
                        message.from_user.id,
                    )
                except Exception as e:
                    logger.error("Failed to save to database: %s", e, exc_info=True)

            await _deliver_transcription(message, result)
        except RuntimeError as runtime_err:
            error_msg = str(runtime_err)
            if "FloodWait" in error_msg or "tunggu" in error_msg.lower():
                # FloodWait error from Telegram
                logger.warning("FloodWait error: %s", error_msg)
                await message.answer(
                    "⏳ **Telegram Rate Limit**\n\n"
                    "Bot sedang dibatasi oleh Telegram karena terlalu banyak request.\n\n"
                    f"ℹ️ {error_msg}\n\n"
                    "💡 **Solusi:**\n"
                    "• Tunggu beberapa saat\n"
                    "• Coba kirim file lagi nanti\n"
                    "• File duplikat akan otomatis diambil dari cache"
                )
            else:
                # Other runtime errors
                logger.exception("Runtime error during processing")
                await message.answer(f"❌ Error: {error_msg}")
        except ValueError as val_err:
            logger.exception("%s gagal menghasilkan transkrip", provider_display)
            await message.answer(
                f"{provider_display.capitalize()} tidak mengembalikan teks: {val_err}. "
                "Silakan periksa kualitas audio atau coba model/provider lain."
            )
        except HTTPError as http_err:
            logger.exception(
                "%s API error during transcription", provider_display.capitalize()
            )
            status_code = (
                http_err.response.status_code if http_err.response is not None else None
            )
            if status_code == 413:
                await message.answer(
                    f"{provider_display.capitalize()} menolak file karena terlalu besar (HTTP 413). "
                    "Silakan kompres ulang sebelum mencoba lagi."
                )
            else:
                await message.answer(
                    f"{provider_display.capitalize()} API mengembalikan kesalahan: "
                    f"{status_code or http_err}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error while processing media")
            error_msg = str(exc)
            if "FloodWait" in error_msg or "rate limit" in error_msg.lower():
                await message.answer(
                    "⏳ Bot sedang dibatasi oleh Telegram.\n"
                    "Silakan coba lagi dalam beberapa menit.\n\n"
                    "File yang sama akan otomatis diambil dari cache! ✨"
                )
            else:
                await message.answer(
                    f"❌ Gagal memproses file: {exc}\n\n"
                    "💡 Tips:\n"
                    "• Pastikan file adalah audio/video yang valid\n"
                    "• Coba file dengan ukuran lebih kecil\n"
                    "• Gunakan command /start untuk info bot"
                )
        finally:
            for path in cleanup_paths:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    logger.warning(
                        "Gagal menghapus file sementara %s", path, exc_info=True
                    )


def _pick_media(message: Message) -> Optional[MediaMeta]:
    if message.voice:
        return MediaMeta(
            display_name="voice_note.ogg",
            suffix=".ogg",
            file_size=message.voice.file_size,
        )
    if message.audio:
        suffix = Path(message.audio.file_name or "audio.mp3").suffix or ".mp3"
        return MediaMeta(
            display_name=message.audio.file_name or f"audio{suffix}",
            suffix=suffix,
            file_size=message.audio.file_size,
        )
    if message.video:
        suffix = Path(message.video.file_name or "video.mp4").suffix or ".mp4"
        return MediaMeta(
            display_name=message.video.file_name or f"video{suffix}",
            suffix=suffix,
            file_size=message.video.file_size,
        )
    if message.video_note:
        return MediaMeta(
            display_name="video_note.mp4",
            suffix=".mp4",
            file_size=message.video_note.file_size,
        )
    if message.document and message.document.mime_type:
        mime = message.document.mime_type
        if mime.startswith("audio") or mime.startswith("video"):
            suffix = Path(message.document.file_name or "media").suffix
            fallback_suffix = ".mp3" if mime.startswith("audio") else ".mp4"
            return MediaMeta(
                display_name=message.document.file_name or f"media{fallback_suffix}",
                suffix=suffix or fallback_suffix,
                file_size=message.document.file_size,
            )
    return None


def _build_download_path(meta: MediaMeta) -> Path:
    downloads_dir = Path.home() / "Downloads" / "transhades"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    sanitized = _sanitize_filename(meta.display_name)
    suffix = meta.suffix or ".bin"
    filename = sanitized if sanitized.endswith(suffix) else f"{sanitized}{suffix}"
    return downloads_dir / f"{timestamp}_{filename}"


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "media")
    return cleaned.strip("_") or "media"


async def _download_media(
    downloader: TelethonDownloadService,
    message: Message,
    target_path: Path,
    meta: MediaMeta,
) -> None:
    progress: Progress | None = None
    task_id: int | None = None

    def progress_callback(current: int, total: int) -> None:
        if progress and task_id is not None:
            progress.update(
                task_id, completed=current, total=total or meta.file_size or 0
            )

    if (meta.file_size or 0) >= PROGRESS_BAR_THRESHOLD:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            transient=True,
        )
        progress.start()
        total_size = meta.file_size if (meta.file_size and meta.file_size > 0) else None
        task_id = progress.add_task(
            description=f"Mendownload {meta.display_name}",
            total=total_size,
        )
        logger.info("Progress bar diaktifkan untuk unduhan besar.")

    try:
        await downloader.download_media(
            chat_id=message.chat.id,
            message_id=message.message_id,
            file_path=str(target_path),
            progress_callback=progress_callback if progress else None,
        )
    finally:
        if progress:
            progress.stop()


def _prepare_audio_for_transcription(
    source_path: Path, file_size: Optional[int]
) -> Path:
    """Legacy function - kept for compatibility."""
    if not source_path.exists():
        logger.warning("Source path %s tidak ditemukan.", source_path)
        return source_path

    actual_size = file_size or source_path.stat().st_size
    suffix = source_path.suffix.lower()

    if suffix == ".mp3":
        logger.info(
            "Skipping re-encoding for %s (already mp3).",
            source_path,
        )
        logger.info(
            "Menggunakan file mp3 original tanpa konversi ulang untuk menjaga kualitas.",
        )
        return source_path

    target_path: Path
    command: list[str]

    target_path = source_path.with_suffix(".mp3")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "96k",
        str(target_path),
    ]

    logger.info(
        "Converting %s (%s bytes) to %s at %s",
        source_path,
        actual_size,
        target_path.suffix,
        target_path,
    )

    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.stderr:
            logger.debug(
                "ffmpeg stderr: %s", result.stderr.decode("utf-8", errors="ignore")
            )
        try:
            new_size = target_path.stat().st_size
        except OSError:
            new_size = "unknown"
        logger.info("Conversion complete for %s (%s bytes).", target_path, new_size)
        return target_path
    except subprocess.CalledProcessError as err:
        logger.error(
            "ffmpeg conversion failed for %s: %s",
            source_path,
            err.stderr.decode("utf-8", errors="ignore"),
        )
        return source_path


def _prepare_audio_for_transcription_optimized(
    source_path: Path,
    file_size: Optional[int],
    audio_optimizer: AudioOptimizer,
    compression_threshold_bytes: int,
) -> Path:
    """Optimized audio preparation with intelligent compression."""
    if not source_path.exists():
        logger.warning("Source path %s tidak ditemukan.", source_path)
        return source_path

    actual_size = file_size or source_path.stat().st_size
    suffix = source_path.suffix.lower()

    # Check if compression is needed
    if actual_size < compression_threshold_bytes:
        if suffix == ".mp3":
            logger.info(
                "✓ File %s already optimal (mp3, %s bytes < %s threshold)",
                source_path.name,
                actual_size,
                compression_threshold_bytes,
            )
            return source_path
        elif suffix in [".ogg", ".m4a"]:
            logger.info(
                "✓ File %s is small enough (%s bytes < %s threshold), minimal conversion",
                source_path.name,
                actual_size,
                compression_threshold_bytes,
            )

    # Need compression - use ffmpeg
    target_path = source_path.with_suffix(".mp3")

    # Determine optimal bitrate based on file size
    if actual_size > 100 * 1024 * 1024:  # >100MB
        bitrate = "64k"
        logger.info("Large file detected, using lower bitrate: %s", bitrate)
    elif actual_size > 50 * 1024 * 1024:  # >50MB
        bitrate = "80k"
    else:
        bitrate = audio_optimizer.target_bitrate

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        str(audio_optimizer.target_channels),
        "-ar",
        str(audio_optimizer.target_sample_rate),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(target_path),
    ]

    logger.info(
        "🎵 Optimizing audio: %s (%s bytes) → %s (bitrate: %s)",
        source_path.name,
        actual_size,
        target_path.name,
        bitrate,
    )

    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.stderr:
            logger.debug(
                "ffmpeg stderr: %s", result.stderr.decode("utf-8", errors="ignore")
            )

        new_size = target_path.stat().st_size
        compression_ratio = (1 - new_size / actual_size) * 100
        logger.info(
            "✓ Optimization complete: %s → %s bytes (%.1f%% compression)",
            target_path.name,
            new_size,
            compression_ratio,
        )
        return target_path
    except subprocess.CalledProcessError as err:
        logger.error(
            "ffmpeg conversion failed for %s: %s",
            source_path,
            err.stderr.decode("utf-8", errors="ignore"),
        )
        return source_path


async def _deliver_transcription(message: Message, result: TranscriptionResult) -> None:
    plain_text = result.to_plain_text()
    if not plain_text:
        await message.answer("Transkrip kosong diterima dari Groq.")
        return

    if len(plain_text) <= TELEGRAM_MESSAGE_LIMIT:
        await message.answer(plain_text)
    else:
        preview = plain_text[:TELEGRAM_MESSAGE_LIMIT]
        await message.answer(
            preview + "\n\n[Transkrip dipotong. Versi lengkap tersedia di lampiran.]"
        )

    await _send_transcript_files(message, result, plain_text)


async def _send_transcript_files(
    message: Message,
    result: TranscriptionResult,
    plain_text: str,
) -> None:
    base_name = _derive_base_name(message)

    txt_buffer = io.BytesIO(plain_text.encode("utf-8"))
    txt_file = BufferedInputFile(
        txt_buffer.getvalue(),
        filename=f"{base_name}.txt",
    )
    await message.answer_document(
        document=txt_file,
        caption="Transkrip teks tanpa timestamp.",
    )

    if result.segments:
        try:
            srt_content = result.to_srt()
        except ValueError:
            logger.info(
                "SRT output tidak tersedia karena segment informasi tidak lengkap."
            )
            return

        if srt_content:
            srt_buffer = io.BytesIO(srt_content.encode("utf-8"))
            srt_file = BufferedInputFile(
                srt_buffer.getvalue(),
                filename=f"{base_name}.srt",
            )
            await message.answer_document(
                document=srt_file,
                caption="Transkrip format SRT.",
            )


def _derive_base_name(message: Message) -> str:
    candidate = "transcript"
    if message.document and message.document.file_name:
        candidate = message.document.file_name
    elif message.audio and message.audio.file_name:
        candidate = message.audio.file_name
    elif message.video and message.video.file_name:
        candidate = message.video.file_name
    elif message.caption:
        candidate = message.caption

    sanitized = _sanitize_filename(Path(candidate).stem)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{sanitized}_{timestamp}"
