"""Export service for generating transcript files in multiple formats."""

import logging
from typing import Optional, List
from dataclasses import dataclass
from datetime import timedelta

logger = logging.getLogger(__name__)


@dataclass
class SubtitleSegment:
    """Subtitle segment with timing information."""

    index: int
    start_time: float
    end_time: float
    text: str


class ExportService:
    """Service for exporting transcripts in various formats."""

    @staticmethod
    def to_txt(
        transcript: str,
        metadata: Optional[dict] = None,
        include_metadata: bool = True,
    ) -> str:
        """
        Export transcript as plain text.

        Args:
            transcript: The transcript text
            metadata: Optional metadata (file_name, duration, language, etc.)
            include_metadata: Whether to include metadata header

        Returns:
            Plain text string
        """
        lines = []

        if include_metadata and metadata:
            lines.append("=" * 60)
            lines.append("TRANSCRIPT METADATA")
            lines.append("=" * 60)

            if "file_name" in metadata:
                lines.append(f"File: {metadata['file_name']}")

            if "duration" in metadata and metadata["duration"]:
                duration = ExportService._format_duration(metadata["duration"])
                lines.append(f"Duration: {duration}")

            if "detected_language" in metadata and metadata["detected_language"]:
                lines.append(f"Language: {metadata['detected_language']}")

            if "provider" in metadata:
                lines.append(f"Provider: {metadata['provider']}")

            if "model" in metadata and metadata["model"]:
                lines.append(f"Model: {metadata['model']}")

            if "timestamp" in metadata:
                lines.append(f"Processed: {metadata['timestamp']}")

            lines.append("=" * 60)
            lines.append("")

        lines.append(transcript)

        return "\n".join(lines)

    @staticmethod
    def to_srt(
        transcript: str,
        duration: Optional[float] = None,
        words_per_segment: int = 10,
    ) -> str:
        """
        Export transcript as SRT subtitle format.

        Args:
            transcript: The transcript text
            duration: Total duration in seconds (optional)
            words_per_segment: Number of words per subtitle segment

        Returns:
            SRT formatted string
        """
        # Split transcript into words
        words = transcript.split()

        if not words:
            return ""

        # Calculate timing for each segment
        segments = []
        total_words = len(words)

        # If no duration provided, estimate 2 seconds per segment
        if duration is None or duration <= 0:
            segment_duration = 2.0
        else:
            segment_duration = duration / (total_words / words_per_segment)

        current_time = 0.0
        segment_index = 1

        for i in range(0, total_words, words_per_segment):
            chunk_words = words[i : i + words_per_segment]
            text = " ".join(chunk_words)

            start_time = current_time
            end_time = current_time + segment_duration

            segments.append(
                SubtitleSegment(
                    index=segment_index,
                    start_time=start_time,
                    end_time=end_time,
                    text=text,
                )
            )

            current_time = end_time
            segment_index += 1

        # Generate SRT content
        srt_lines = []
        for segment in segments:
            srt_lines.append(str(segment.index))
            srt_lines.append(
                f"{ExportService._format_srt_time(segment.start_time)} --> "
                f"{ExportService._format_srt_time(segment.end_time)}"
            )
            srt_lines.append(segment.text)
            srt_lines.append("")

        return "\n".join(srt_lines)

    @staticmethod
    def to_markdown(
        transcript: str,
        metadata: Optional[dict] = None,
        include_toc: bool = False,
    ) -> str:
        """
        Export transcript as Markdown format.

        Args:
            transcript: The transcript text
            metadata: Optional metadata
            include_toc: Whether to include table of contents

        Returns:
            Markdown formatted string
        """
        lines = []

        # Title
        file_name = (
            metadata.get("file_name", "Transcript") if metadata else "Transcript"
        )
        lines.append(f"# {file_name}")
        lines.append("")

        # Metadata section
        if metadata:
            lines.append("## 📋 Information")
            lines.append("")

            if "duration" in metadata and metadata["duration"]:
                duration = ExportService._format_duration(metadata["duration"])
                lines.append(f"- **Duration:** {duration}")

            if "detected_language" in metadata and metadata["detected_language"]:
                lines.append(f"- **Language:** {metadata['detected_language']}")

            if "provider" in metadata:
                provider = metadata["provider"].title()
                lines.append(f"- **Provider:** {provider}")

            if "model" in metadata and metadata["model"]:
                lines.append(f"- **Model:** {metadata['model']}")

            if "timestamp" in metadata:
                lines.append(f"- **Processed:** {metadata['timestamp']}")

            if "file_size" in metadata and metadata["file_size"]:
                size_mb = metadata["file_size"] / (1024 * 1024)
                lines.append(f"- **File Size:** {size_mb:.2f} MB")

            lines.append("")

        # Table of contents (if requested and transcript has paragraphs)
        if include_toc:
            paragraphs = [p.strip() for p in transcript.split("\n\n") if p.strip()]
            if len(paragraphs) > 3:
                lines.append("## 📑 Table of Contents")
                lines.append("")
                for i, para in enumerate(paragraphs[:10], 1):
                    preview = para[:60] + "..." if len(para) > 60 else para
                    lines.append(f"{i}. [{preview}](#section-{i})")
                lines.append("")

        # Transcript content
        lines.append("## 📝 Transcript")
        lines.append("")

        # Format transcript with proper paragraphs
        if include_toc:
            paragraphs = [p.strip() for p in transcript.split("\n\n") if p.strip()]
            for i, para in enumerate(paragraphs, 1):
                lines.append(f"### Section {i}")
                lines.append("")
                lines.append(para)
                lines.append("")
        else:
            lines.append(transcript)

        lines.append("")
        lines.append("---")
        lines.append("*Generated by Transhades Transcription Bot*")

        return "\n".join(lines)

    @staticmethod
    def to_vtt(
        transcript: str,
        duration: Optional[float] = None,
        words_per_segment: int = 10,
    ) -> str:
        """
        Export transcript as WebVTT subtitle format.

        Args:
            transcript: The transcript text
            duration: Total duration in seconds (optional)
            words_per_segment: Number of words per subtitle segment

        Returns:
            WebVTT formatted string
        """
        # Start with WebVTT header
        lines = ["WEBVTT", ""]

        # Split transcript into words
        words = transcript.split()

        if not words:
            return "\n".join(lines)

        # Calculate timing for each segment
        total_words = len(words)

        # If no duration provided, estimate 2 seconds per segment
        if duration is None or duration <= 0:
            segment_duration = 2.0
        else:
            segment_duration = duration / (total_words / words_per_segment)

        current_time = 0.0
        segment_index = 1

        for i in range(0, total_words, words_per_segment):
            chunk_words = words[i : i + words_per_segment]
            text = " ".join(chunk_words)

            start_time = current_time
            end_time = current_time + segment_duration

            lines.append(str(segment_index))
            lines.append(
                f"{ExportService._format_vtt_time(start_time)} --> "
                f"{ExportService._format_vtt_time(end_time)}"
            )
            lines.append(text)
            lines.append("")

            current_time = end_time
            segment_index += 1

        return "\n".join(lines)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """
        Format duration in seconds to human-readable string.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted string (e.g., "1h 23m 45s")
        """
        if seconds < 60:
            return f"{int(seconds)}s"

        minutes = int(seconds // 60)
        remaining_seconds = int(seconds % 60)

        if minutes < 60:
            return f"{minutes}m {remaining_seconds}s"

        hours = minutes // 60
        remaining_minutes = minutes % 60

        return f"{hours}h {remaining_minutes}m {remaining_seconds}s"

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """
        Format time for SRT subtitle format.

        Args:
            seconds: Time in seconds

        Returns:
            Formatted time string (HH:MM:SS,mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def _format_vtt_time(seconds: float) -> str:
        """
        Format time for WebVTT subtitle format.

        Args:
            seconds: Time in seconds

        Returns:
            Formatted time string (HH:MM:SS.mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)

        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    @staticmethod
    def get_filename(base_name: str, format: str) -> str:
        """
        Get filename with proper extension for format.

        Args:
            base_name: Base filename (without extension)
            format: Format type ('txt', 'srt', 'md', 'vtt')

        Returns:
            Filename with extension
        """
        # Remove existing extension if present
        if "." in base_name:
            base_name = base_name.rsplit(".", 1)[0]

        extensions = {
            "txt": ".txt",
            "srt": ".srt",
            "md": ".md",
            "markdown": ".md",
            "vtt": ".vtt",
            "json": ".json",
            "csv": ".csv",
        }

        extension = extensions.get(format.lower(), ".txt")
        return f"{base_name}{extension}"
