from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AudioOptimizer:
    """
    Optimasi audio untuk transcription dengan streaming compression,
    parallel processing, dan caching berdasarkan file hash.
    """

    def __init__(
        self,
        *,
        target_bitrate: str = "96k",
        target_sample_rate: int = 16000,
        target_channels: int = 1,
        use_streaming: bool = True,
    ) -> None:
        self.target_bitrate = target_bitrate
        self.target_sample_rate = target_sample_rate
        self.target_channels = target_channels
        self.use_streaming = use_streaming

    async def optimize_audio(
        self,
        source_path: Path,
        *,
        force_conversion: bool = False,
    ) -> tuple[Path | io.BytesIO, str]:
        """
        Optimasi audio file untuk transcription.

        Returns:
            tuple: (output_path_or_buffer, file_hash)
        """
        file_hash = await self._compute_file_hash(source_path)

        if not force_conversion and source_path.suffix.lower() == ".mp3":
            file_size = source_path.stat().st_size
            if file_size < 15 * 1024 * 1024:  # < 15MB
                logger.info("Audio sudah optimal, skip conversion")
                return source_path, file_hash

        if self.use_streaming:
            output_buffer = await self._convert_to_buffer(source_path)
            return output_buffer, file_hash
        else:
            output_path = await self._convert_to_file(source_path)
            return output_path, file_hash

    async def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash untuk file caching."""

        def _hash_file() -> str:
            sha256 = hashlib.sha256()
            with file_path.open("rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest()

        return await asyncio.to_thread(_hash_file)

    async def _convert_to_buffer(self, source_path: Path) -> io.BytesIO:
        """Convert audio ke BytesIO buffer tanpa save ke disk."""

        def _convert() -> bytes:
            command = [
                "ffmpeg",
                "-i",
                str(source_path),
                "-vn",  # No video
                "-ac",
                str(self.target_channels),
                "-ar",
                str(self.target_sample_rate),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                self.target_bitrate,
                "-f",
                "mp3",  # Force output format
                "pipe:1",  # Output to stdout
            ]

            logger.info("Converting audio to buffer with ffmpeg")
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            if result.stderr:
                logger.debug(
                    "ffmpeg stderr: %s", result.stderr.decode("utf-8", errors="ignore")
                )

            return result.stdout

        audio_bytes = await asyncio.to_thread(_convert)
        buffer = io.BytesIO(audio_bytes)
        buffer.name = f"{source_path.stem}.mp3"
        logger.info("Audio converted to buffer (%d bytes)", len(audio_bytes))
        return buffer

    async def _convert_to_file(self, source_path: Path) -> Path:
        """Convert audio ke file (fallback method)."""

        def _convert() -> Path:
            target_path = source_path.with_suffix(".mp3")
            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                str(self.target_channels),
                "-ar",
                str(self.target_sample_rate),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                self.target_bitrate,
                str(target_path),
            ]

            logger.info("Converting audio to file: %s", target_path)
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            if result.stderr:
                logger.debug(
                    "ffmpeg stderr: %s", result.stderr.decode("utf-8", errors="ignore")
                )

            return target_path

        return await asyncio.to_thread(_convert)

    async def estimate_compression_ratio(
        self,
        source_path: Path,
    ) -> float:
        """
        Estimasi compression ratio untuk prediksi ukuran output.
        Berguna untuk cek apakah hasil akan melebihi API limit.
        """

        def _estimate() -> float:
            source_size = source_path.stat().st_size

            # Probe duration dengan ffprobe
            command = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,bit_rate",
                "-of",
                "default=noprint_wrappers=1",
                str(source_path),
            ]

            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=True,
                )

                duration = None
                bitrate = None
                for line in result.stdout.splitlines():
                    if line.startswith("duration="):
                        duration = float(line.split("=")[1])
                    elif line.startswith("bit_rate="):
                        bitrate = int(line.split("=")[1])

                if duration:
                    # Estimasi ukuran output: duration * target_bitrate
                    target_bitrate_bps = int(self.target_bitrate.rstrip("k")) * 1000
                    estimated_size = (duration * target_bitrate_bps) / 8
                    ratio = estimated_size / source_size
                    return ratio

            except (subprocess.CalledProcessError, ValueError) as e:
                logger.warning("Failed to estimate compression ratio: %s", e)

            # Fallback: assume 0.5 ratio untuk mp3 96kbps
            return 0.5

        return await asyncio.to_thread(_estimate)

    def get_optimal_settings_for_size(
        self,
        target_size_mb: float,
        duration_seconds: float,
    ) -> dict[str, str]:
        """
        Kalkulasi optimal bitrate untuk target file size.
        Berguna untuk auto-adjust agar tidak melebihi API limit.
        """
        target_bytes = target_size_mb * 1024 * 1024
        # target_bytes = (duration * bitrate_bps) / 8
        # bitrate_bps = (target_bytes * 8) / duration
        optimal_bitrate_bps = int((target_bytes * 8) / duration_seconds)
        optimal_bitrate_kbps = optimal_bitrate_bps // 1000

        # Clamp between 32k and 128k
        optimal_bitrate_kbps = max(32, min(128, optimal_bitrate_kbps))

        return {
            "bitrate": f"{optimal_bitrate_kbps}k",
            "sample_rate": str(self.target_sample_rate),
            "channels": str(self.target_channels),
        }


class ParallelAudioProcessor:
    """
    Process multiple audio files secara parallel untuk batch transcription.
    Berguna untuk handle multiple uploads sekaligus.
    """

    def __init__(
        self,
        optimizer: AudioOptimizer,
        *,
        max_concurrent: int = 3,
    ) -> None:
        self.optimizer = optimizer
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_batch(
        self,
        file_paths: list[Path],
    ) -> list[tuple[Path | io.BytesIO, str]]:
        """Process multiple files concurrently."""
        tasks = [self._process_single(path) for path in file_paths]
        return await asyncio.gather(*tasks)

    async def _process_single(
        self,
        path: Path,
    ) -> tuple[Path | io.BytesIO, str]:
        """Process single file with semaphore limit."""
        async with self.semaphore:
            return await self.optimizer.optimize_audio(path)


class AudioStreamUploader:
    """
    Upload audio ke transcription service secara streaming
    tanpa save intermediate file ke disk.
    """

    @staticmethod
    async def stream_to_api(
        audio_buffer: io.BytesIO,
        api_url: str,
        headers: dict[str, str],
        params: dict[str, str],
        timeout: int = 300,
    ) -> dict:
        """
        Upload audio buffer ke API secara streaming.
        Menggunakan aiohttp untuk async streaming upload.
        """
        import aiohttp

        audio_buffer.seek(0)

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                audio_buffer,
                filename=getattr(audio_buffer, "name", "audio.mp3"),
                content_type="audio/mpeg",
            )

            # Add other form fields
            for key, value in params.items():
                data.add_field(key, value)

            async with session.post(
                api_url,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                return await response.json()


class TranscriptCache:
    """
    Simple in-memory cache untuk hasil transkrip.
    Untuk production, gunakan Redis.
    """

    def __init__(self, max_size: int = 100) -> None:
        self.cache: dict[str, tuple[str, list]] = {}
        self.max_size = max_size
        self._lock = asyncio.Lock()

    async def get(self, file_hash: str) -> Optional[tuple[str, list]]:
        """Get cached transcript by file hash."""
        async with self._lock:
            return self.cache.get(file_hash)

    async def set(
        self,
        file_hash: str,
        text: str,
        segments: Optional[list] = None,
    ) -> None:
        """Cache transcript result."""
        async with self._lock:
            if len(self.cache) >= self.max_size:
                # Simple LRU: remove first item
                self.cache.pop(next(iter(self.cache)))

            self.cache[file_hash] = (text, segments or [])
            logger.info("Cached transcript for hash %s", file_hash[:8])

    async def clear(self) -> None:
        """Clear all cache."""
        async with self._lock:
            self.cache.clear()
            logger.info("Transcript cache cleared")

    def __len__(self) -> int:
        return len(self.cache)
