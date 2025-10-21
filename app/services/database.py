"""Database service for storing transcription history and metadata."""

import sqlite3
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict


logger = logging.getLogger(__name__)


@dataclass
class TranscriptionRecord:
    """Model for transcription record."""

    id: Optional[int] = None
    user_id: int = None
    chat_id: int = None
    file_id: str = None
    file_name: str = None
    file_size: int = None
    duration: Optional[float] = None
    transcript: str = None
    detected_language: Optional[str] = None
    provider: str = None
    model: Optional[str] = None
    timestamp: Optional[str] = None
    processing_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {k: v for k, v in asdict(self).items() if v is not None}


class TranscriptionDatabase:
    """SQLite database for transcription history."""

    def __init__(self, db_path: str = "transcriptions.db"):
        """Initialize database connection."""
        self.db_path = db_path
        self._init_db()
        logger.info(f"Database initialized at {db_path}")

    def _init_db(self):
        """Create database tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Main transcriptions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT,
                    file_size INTEGER,
                    duration REAL,
                    transcript TEXT NOT NULL,
                    detected_language TEXT,
                    provider TEXT NOT NULL,
                    model TEXT,
                    timestamp TEXT NOT NULL,
                    processing_time REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Translations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transcription_id INTEGER NOT NULL,
                    source_language TEXT,
                    target_language TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (transcription_id) REFERENCES transcriptions(id)
                )
            """)

            # Create indexes for faster searches
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_id
                ON transcriptions(user_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_id
                ON transcriptions(chat_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_file_id
                ON transcriptions(file_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON transcriptions(timestamp)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_transcript_fts
                ON transcriptions(transcript)
            """)

            conn.commit()
            logger.info("Database tables and indexes created successfully")

    def add_transcription(self, record: TranscriptionRecord) -> int:
        """
        Add a new transcription record.

        Args:
            record: TranscriptionRecord to add

        Returns:
            ID of the inserted record
        """
        if not record.timestamp:
            record.timestamp = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO transcriptions
                (user_id, chat_id, file_id, file_name, file_size, duration,
                 transcript, detected_language, provider, model, timestamp, processing_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    record.user_id,
                    record.chat_id,
                    record.file_id,
                    record.file_name,
                    record.file_size,
                    record.duration,
                    record.transcript,
                    record.detected_language,
                    record.provider,
                    record.model,
                    record.timestamp,
                    record.processing_time,
                ),
            )
            conn.commit()
            record_id = cursor.lastrowid
            logger.info(
                f"Added transcription record {record_id} for user {record.user_id}"
            )
            return record_id

    def get_history(self, user_id: int, limit: int = 20) -> List[TranscriptionRecord]:
        """
        Get transcription history for a user.

        Args:
            user_id: User ID to get history for
            limit: Maximum number of records to return

        Returns:
            List of TranscriptionRecord objects
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM transcriptions
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (user_id, limit),
            )

            records = []
            for row in cursor.fetchall():
                records.append(
                    TranscriptionRecord(
                        id=row["id"],
                        user_id=row["user_id"],
                        chat_id=row["chat_id"],
                        file_id=row["file_id"],
                        file_name=row["file_name"],
                        file_size=row["file_size"],
                        duration=row["duration"],
                        transcript=row["transcript"],
                        detected_language=row["detected_language"],
                        provider=row["provider"],
                        model=row["model"],
                        timestamp=row["timestamp"],
                        processing_time=row["processing_time"],
                    )
                )

            return records

    def search_transcripts(
        self, user_id: int, keyword: str, limit: int = 20
    ) -> List[TranscriptionRecord]:
        """
        Search transcripts by keyword.

        Args:
            user_id: User ID to search for
            keyword: Keyword to search in transcripts
            limit: Maximum number of records to return

        Returns:
            List of TranscriptionRecord objects
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            search_pattern = f"%{keyword}%"
            cursor.execute(
                """
                SELECT * FROM transcriptions
                WHERE user_id = ? AND transcript LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (user_id, search_pattern, limit),
            )

            records = []
            for row in cursor.fetchall():
                records.append(
                    TranscriptionRecord(
                        id=row["id"],
                        user_id=row["user_id"],
                        chat_id=row["chat_id"],
                        file_id=row["file_id"],
                        file_name=row["file_name"],
                        file_size=row["file_size"],
                        duration=row["duration"],
                        transcript=row["transcript"],
                        detected_language=row["detected_language"],
                        provider=row["provider"],
                        model=row["model"],
                        timestamp=row["timestamp"],
                        processing_time=row["processing_time"],
                    )
                )

            return records

    def get_last_transcription(self, user_id: int) -> Optional[TranscriptionRecord]:
        """
        Get the last transcription for a user.

        Args:
            user_id: User ID

        Returns:
            TranscriptionRecord or None
        """
        records = self.get_history(user_id, limit=1)
        return records[0] if records else None

    def add_translation(
        self,
        transcription_id: int,
        target_language: str,
        translated_text: str,
        source_language: Optional[str] = None,
    ) -> int:
        """
        Add a translation for a transcription.

        Args:
            transcription_id: ID of the transcription
            target_language: Target language code
            translated_text: Translated text
            source_language: Source language code (optional)

        Returns:
            ID of the inserted translation
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO translations
                (transcription_id, source_language, target_language, translated_text)
                VALUES (?, ?, ?, ?)
            """,
                (transcription_id, source_language, target_language, translated_text),
            )
            conn.commit()
            translation_id = cursor.lastrowid
            logger.info(
                f"Added translation {translation_id} for transcription {transcription_id}"
            )
            return translation_id

    def get_translations(self, transcription_id: int) -> List[Dict[str, Any]]:
        """
        Get all translations for a transcription.

        Args:
            transcription_id: ID of the transcription

        Returns:
            List of translation dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM translations
                WHERE transcription_id = ?
                ORDER BY created_at DESC
            """,
                (transcription_id,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def export_history_json(self, user_id: int) -> str:
        """
        Export user's transcription history as JSON.

        Args:
            user_id: User ID

        Returns:
            JSON string
        """
        records = self.get_history(user_id, limit=1000)
        data = [r.to_dict() for r in records]
        return json.dumps(data, indent=2, ensure_ascii=False)

    def export_history_csv(self, user_id: int) -> str:
        """
        Export user's transcription history as CSV.

        Args:
            user_id: User ID

        Returns:
            CSV string
        """
        records = self.get_history(user_id, limit=1000)
        if not records:
            return "No data"

        # CSV header
        csv_lines = [
            "id,user_id,chat_id,file_name,duration,detected_language,provider,timestamp,transcript"
        ]

        # CSV rows
        for record in records:
            # Escape commas and quotes in transcript
            transcript_escaped = (
                record.transcript.replace('"', '""') if record.transcript else ""
            )
            file_name_escaped = (
                record.file_name.replace('"', '""') if record.file_name else ""
            )

            csv_lines.append(
                f"{record.id},{record.user_id},{record.chat_id},"
                f'"{file_name_escaped}",{record.duration or ""},'
                f"{record.detected_language or ''},{record.provider},{record.timestamp},"
                f'"{transcript_escaped}"'
            )

        return "\n".join(csv_lines)

    def get_statistics(self, user_id: int) -> Dict[str, Any]:
        """
        Get statistics for a user.

        Args:
            user_id: User ID

        Returns:
            Dictionary with statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Total transcriptions
            cursor.execute(
                """
                SELECT COUNT(*) FROM transcriptions WHERE user_id = ?
            """,
                (user_id,),
            )
            total_count = cursor.fetchone()[0]

            # Total duration
            cursor.execute(
                """
                SELECT SUM(duration) FROM transcriptions WHERE user_id = ?
            """,
                (user_id,),
            )
            total_duration = cursor.fetchone()[0] or 0

            # Provider breakdown
            cursor.execute(
                """
                SELECT provider, COUNT(*) as count
                FROM transcriptions
                WHERE user_id = ?
                GROUP BY provider
            """,
                (user_id,),
            )
            providers = {row[0]: row[1] for row in cursor.fetchall()}

            # Language breakdown
            cursor.execute(
                """
                SELECT detected_language, COUNT(*) as count
                FROM transcriptions
                WHERE user_id = ? AND detected_language IS NOT NULL
                GROUP BY detected_language
            """,
                (user_id,),
            )
            languages = {row[0]: row[1] for row in cursor.fetchall()}

            return {
                "total_transcriptions": total_count,
                "total_duration_seconds": total_duration,
                "providers": providers,
                "languages": languages,
            }

    def cleanup_old_records(self, days: int = 30) -> int:
        """
        Delete records older than specified days.

        Args:
            days: Number of days to keep

        Returns:
            Number of deleted records
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM transcriptions
                WHERE created_at < datetime('now', '-' || ? || ' days')
            """,
                (days,),
            )
            deleted_count = cursor.rowcount
            conn.commit()
            logger.info(
                f"Cleaned up {deleted_count} old records (older than {days} days)"
            )
            return deleted_count
