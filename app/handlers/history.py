"""Handlers for history, search, translate, and export commands."""

import logging
from io import BytesIO
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BufferedInputFile,
)

from ..services import (
    TranscriptionDatabase,
    TranslationService,
    LANGUAGE_CODES,
    ExportService,
)

logger = logging.getLogger(__name__)
router = Router()


def _format_transcript_preview(transcript: str, max_length: int = 100) -> str:
    """Format transcript for preview."""
    if len(transcript) <= max_length:
        return transcript
    return transcript[:max_length] + "..."


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


@router.message(Command("history"))
async def history_command(
    message: Message,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Show user's transcription history."""
    if not transcription_db:
        await message.answer("⚠️ Database service not available.")
        return

    user_id = message.from_user.id
    records = transcription_db.get_history(user_id, limit=20)

    if not records:
        await message.answer(
            "📭 **No History Found**\n\n"
            "You haven't transcribed any files yet.\n"
            "Send an audio or video file to start!"
        )
        return

    # Build history message
    lines = ["📚 **Your Transcription History**\n"]

    for i, record in enumerate(records[:10], 1):
        file_name = record.file_name or "Unknown"
        duration = (
            ExportService._format_duration(record.duration)
            if record.duration
            else "N/A"
        )
        lang = record.detected_language or "Unknown"
        preview = _format_transcript_preview(record.transcript, 80)

        lines.append(f"**{i}. {file_name}**")
        lines.append(f"   ⏱️ {duration} | 🌐 {lang} | 🔧 {record.provider}")
        lines.append(f"   📝 {preview}")
        lines.append(f"   🕐 {record.timestamp}")
        lines.append("")

    if len(records) > 10:
        lines.append(f"_...and {len(records) - 10} more_\n")

    lines.append("💡 **Commands:**")
    lines.append("• `/search <keyword>` - Search in transcripts")
    lines.append("• `/translate <lang>` - Translate last transcript")
    lines.append("• `/export` - Export history")
    lines.append("• `/stats` - View statistics")

    # Create keyboard for export options
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Export as JSON", callback_data="export:json"
                ),
                InlineKeyboardButton(
                    text="📥 Export as CSV", callback_data="export:csv"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 View Statistics", callback_data="history:stats"
                ),
            ],
        ]
    )

    await message.answer("\n".join(lines), reply_markup=keyboard)


@router.message(Command("search"))
async def search_command(
    message: Message,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Search transcripts by keyword."""
    if not transcription_db:
        await message.answer("⚠️ Database service not available.")
        return

    # Extract search keyword from command
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "🔍 **Search Transcripts**\n\n"
            "**Usage:** `/search <keyword>`\n\n"
            "**Example:**\n"
            "`/search meeting`\n"
            "`/search presentation`\n\n"
            "This will search through all your transcripts."
        )
        return

    keyword = parts[1].strip()
    user_id = message.from_user.id

    # Search transcripts
    results = transcription_db.search_transcripts(user_id, keyword, limit=20)

    if not results:
        await message.answer(
            f"🔍 **Search Results**\n\n"
            f'No transcripts found containing **"{keyword}"**\n\n'
            "Try a different keyword or check your history with `/history`"
        )
        return

    # Build results message
    lines = [f'🔍 **Search Results for "{keyword}"**\n']
    lines.append(f"Found {len(results)} result(s):\n")

    for i, record in enumerate(results[:10], 1):
        file_name = record.file_name or "Unknown"

        # Find keyword context in transcript
        transcript_lower = record.transcript.lower()
        keyword_lower = keyword.lower()
        idx = transcript_lower.find(keyword_lower)

        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(record.transcript), idx + len(keyword) + 40)
            context = record.transcript[start:end]
            if start > 0:
                context = "..." + context
            if end < len(record.transcript):
                context = context + "..."
        else:
            context = _format_transcript_preview(record.transcript, 80)

        lines.append(f"**{i}. {file_name}**")
        lines.append(f"   📝 {context}")
        lines.append(f"   🕐 {record.timestamp}")
        lines.append("")

    if len(results) > 10:
        lines.append(f"_...and {len(results) - 10} more results_\n")

    lines.append("💡 Use `/history` to see all transcripts")

    await message.answer("\n".join(lines))


@router.message(Command("translate"))
async def translate_command(
    message: Message,
    transcription_db: TranscriptionDatabase = None,
    translation_service: TranslationService = None,
) -> None:
    """Translate last transcript to another language."""
    if not transcription_db:
        await message.answer("⚠️ Database service not available.")
        return

    if not translation_service:
        await message.answer("⚠️ Translation service not available.")
        return

    # Extract target language from command
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        # Show available languages
        langs_text = ", ".join(
            [f"`{code}` ({name})" for code, name in list(LANGUAGE_CODES.items())[:10]]
        )
        await message.answer(
            "🌐 **Translate Last Transcript**\n\n"
            "**Usage:** `/translate <language_code>`\n\n"
            "**Examples:**\n"
            "`/translate en` - English\n"
            "`/translate id` - Indonesian\n"
            "`/translate es` - Spanish\n\n"
            f"**Popular languages:**\n{langs_text}\n\n"
            "Use `/languages` to see all supported languages."
        )
        return

    target_lang = parts[1].strip().lower()

    # Validate language code
    if target_lang not in LANGUAGE_CODES:
        await message.answer(
            f"❌ Unsupported language code: **{target_lang}**\n\n"
            f"Use `/languages` to see all supported languages."
        )
        return

    user_id = message.from_user.id

    # Get last transcription
    last_record = transcription_db.get_last_transcription(user_id)

    if not last_record:
        await message.answer(
            "📭 **No Transcripts Found**\n\n"
            "You need to transcribe a file first before translating.\n"
            "Send an audio or video file to start!"
        )
        return

    # Send processing message
    processing_msg = await message.answer(
        f"🌐 Translating to **{LANGUAGE_CODES[target_lang]}**...\nPlease wait..."
    )

    try:
        # Translate
        result = await translation_service.translate(
            text=last_record.transcript,
            target_language=target_lang,
            source_language=last_record.detected_language,
        )

        # Save translation to database
        transcription_db.add_translation(
            transcription_id=last_record.id,
            target_language=target_lang,
            translated_text=result.text,
            source_language=result.source_language,
        )

        # Prepare response
        file_name = last_record.file_name or "Unknown"
        source_lang = LANGUAGE_CODES.get(result.source_language, result.source_language)
        target_lang_name = LANGUAGE_CODES[target_lang]

        response = (
            f"✅ **Translation Complete**\n\n"
            f"📄 **File:** {file_name}\n"
            f"🌐 **{source_lang}** → **{target_lang_name}**\n"
            f"🔧 **Provider:** {result.provider}\n\n"
            f"**Translated Text:**\n{result.text}"
        )

        # Create keyboard for export options
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📥 Download TXT",
                        callback_data=f"translate_export:{last_record.id}:{target_lang}:txt",
                    ),
                    InlineKeyboardButton(
                        text="📥 Download MD",
                        callback_data=f"translate_export:{last_record.id}:{target_lang}:md",
                    ),
                ],
            ]
        )

        # Delete processing message
        await processing_msg.delete()

        # Send result (split if too long)
        if len(response) > 4000:
            await message.answer(response[:4000] + "...\n\n_(Message truncated)_")
            # Send full text as file
            file_content = ExportService.to_txt(
                result.text,
                metadata={
                    "file_name": file_name,
                    "source_language": source_lang,
                    "target_language": target_lang_name,
                    "provider": result.provider,
                },
            )
            file = BufferedInputFile(
                file_content.encode("utf-8"),
                filename=f"{file_name}_translated_{target_lang}.txt",
            )
            await message.answer_document(file, caption="📄 Full translated text")
        else:
            await message.answer(response, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Translation failed: {e}", exc_info=True)
        await processing_msg.edit_text(
            f"❌ **Translation Failed**\n\nError: {str(e)}\n\nPlease try again later."
        )


@router.message(Command("languages"))
async def languages_command(message: Message) -> None:
    """Show all supported languages."""
    lines = ["🌐 **Supported Languages**\n"]

    # Group languages by region (simplified)
    lines.append("**European:**")
    eu_langs = [
        "en",
        "es",
        "fr",
        "de",
        "it",
        "pt",
        "ru",
        "nl",
        "pl",
        "tr",
        "sv",
        "no",
        "da",
        "fi",
        "cs",
        "hu",
        "ro",
    ]
    for code in eu_langs:
        if code in LANGUAGE_CODES:
            lines.append(f"• `{code}` - {LANGUAGE_CODES[code]}")
    lines.append("")

    lines.append("**Asian:**")
    as_langs = ["id", "ja", "ko", "zh", "ar", "hi", "th", "vi"]
    for code in as_langs:
        if code in LANGUAGE_CODES:
            lines.append(f"• `{code}` - {LANGUAGE_CODES[code]}")
    lines.append("")

    lines.append("**Usage:**")
    lines.append("`/translate <code>` - Translate last transcript")
    lines.append("\n**Example:**")
    lines.append("`/translate en` - Translate to English")

    await message.answer("\n".join(lines))


@router.message(Command("export"))
async def export_command(
    message: Message,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Export last transcript in various formats."""
    if not transcription_db:
        await message.answer("⚠️ Database service not available.")
        return

    user_id = message.from_user.id
    last_record = transcription_db.get_last_transcription(user_id)

    if not last_record:
        await message.answer(
            "📭 **No Transcripts Found**\n\n"
            "You need to transcribe a file first.\n"
            "Send an audio or video file to start!"
        )
        return

    # Create keyboard for export format selection
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Plain Text (.txt)",
                    callback_data=f"export_transcript:{last_record.id}:txt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Markdown (.md)",
                    callback_data=f"export_transcript:{last_record.id}:md",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎬 Subtitles (.srt)",
                    callback_data=f"export_transcript:{last_record.id}:srt",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 WebVTT (.vtt)",
                    callback_data=f"export_transcript:{last_record.id}:vtt",
                ),
            ],
        ]
    )

    file_name = last_record.file_name or "transcript"
    await message.answer(
        f"📥 **Export Transcript**\n\n"
        f"**File:** {file_name}\n"
        f"**Language:** {last_record.detected_language or 'Unknown'}\n\n"
        f"Choose a format to download:",
        reply_markup=keyboard,
    )


@router.message(Command("stats"))
async def stats_command(
    message: Message,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Show user statistics."""
    if not transcription_db:
        await message.answer("⚠️ Database service not available.")
        return

    user_id = message.from_user.id
    stats = transcription_db.get_statistics(user_id)

    if stats["total_transcriptions"] == 0:
        await message.answer(
            "📊 **Your Statistics**\n\n"
            "No transcriptions yet. Send an audio or video file to start!"
        )
        return

    lines = ["📊 **Your Transcription Statistics**\n"]

    # Total stats
    lines.append("**Overview:**")
    lines.append(f"• Total transcriptions: **{stats['total_transcriptions']}**")

    if stats["total_duration_seconds"] > 0:
        duration = ExportService._format_duration(stats["total_duration_seconds"])
        lines.append(f"• Total audio processed: **{duration}**")

    lines.append("")

    # Provider breakdown
    if stats["providers"]:
        lines.append("**Providers Used:**")
        for provider, count in stats["providers"].items():
            lines.append(f"• {provider.title()}: {count}")
        lines.append("")

    # Language breakdown
    if stats["languages"]:
        lines.append("**Languages Detected:**")
        for lang, count in stats["languages"].items():
            lang_name = LANGUAGE_CODES.get(lang, lang)
            lines.append(f"• {lang_name}: {count}")
        lines.append("")

    lines.append("💡 Use `/history` to see all your transcripts")

    await message.answer("\n".join(lines))


# Callback handlers


@router.callback_query(F.data.startswith("export:"))
async def export_history_callback(
    query: CallbackQuery,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Handle history export callbacks."""
    if not transcription_db:
        await query.answer("⚠️ Database service not available.", show_alert=True)
        return

    format_type = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    await query.answer("Generating export file...")

    try:
        if format_type == "json":
            content = transcription_db.export_history_json(user_id)
            filename = f"transcription_history_{user_id}.json"
            mime_type = "application/json"
        elif format_type == "csv":
            content = transcription_db.export_history_csv(user_id)
            filename = f"transcription_history_{user_id}.csv"
            mime_type = "text/csv"
        else:
            await query.answer("Unknown format", show_alert=True)
            return

        # Send file
        file = BufferedInputFile(
            content.encode("utf-8"),
            filename=filename,
        )
        await query.message.answer_document(
            file,
            caption=f"📥 Your transcription history ({format_type.upper()})",
        )

    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        await query.answer(f"Export failed: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("export_transcript:"))
async def export_transcript_callback(
    query: CallbackQuery,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Handle transcript export callbacks."""
    if not transcription_db:
        await query.answer("⚠️ Database service not available.", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid callback data", show_alert=True)
        return

    _, record_id, format_type = parts

    await query.answer("Generating file...")

    try:
        # Get record from database
        records = transcription_db.get_history(query.from_user.id, limit=1000)
        record = next((r for r in records if r.id == int(record_id)), None)

        if not record:
            await query.answer("Transcript not found", show_alert=True)
            return

        # Prepare metadata
        metadata = {
            "file_name": record.file_name,
            "duration": record.duration,
            "detected_language": record.detected_language,
            "provider": record.provider,
            "model": record.model,
            "timestamp": record.timestamp,
        }

        # Generate content based on format
        if format_type == "txt":
            content = ExportService.to_txt(record.transcript, metadata)
        elif format_type == "md":
            content = ExportService.to_markdown(record.transcript, metadata)
        elif format_type == "srt":
            content = ExportService.to_srt(record.transcript, record.duration)
        elif format_type == "vtt":
            content = ExportService.to_vtt(record.transcript, record.duration)
        else:
            await query.answer("Unknown format", show_alert=True)
            return

        # Generate filename
        base_name = record.file_name or f"transcript_{record.id}"
        filename = ExportService.get_filename(base_name, format_type)

        # Send file
        file = BufferedInputFile(
            content.encode("utf-8"),
            filename=filename,
        )
        await query.message.answer_document(
            file,
            caption=f"📥 {filename}",
        )

    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        await query.answer(f"Export failed: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("translate_export:"))
async def translate_export_callback(
    query: CallbackQuery,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Handle translation export callbacks."""
    if not transcription_db:
        await query.answer("⚠️ Database service not available.", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer("Invalid callback data", show_alert=True)
        return

    _, record_id, target_lang, format_type = parts

    await query.answer("Generating file...")

    try:
        # Get translations
        translations = transcription_db.get_translations(int(record_id))

        if not translations:
            await query.answer("Translation not found", show_alert=True)
            return

        # Find translation for target language
        translation = next(
            (t for t in translations if t["target_language"] == target_lang), None
        )

        if not translation:
            await query.answer("Translation not found", show_alert=True)
            return

        # Get original record
        records = transcription_db.get_history(query.from_user.id, limit=1000)
        record = next((r for r in records if r.id == int(record_id)), None)

        if not record:
            await query.answer("Original transcript not found", show_alert=True)
            return

        # Prepare metadata
        metadata = {
            "file_name": record.file_name,
            "source_language": translation["source_language"],
            "target_language": LANGUAGE_CODES.get(target_lang, target_lang),
        }

        # Generate content
        if format_type == "txt":
            content = ExportService.to_txt(translation["translated_text"], metadata)
        elif format_type == "md":
            content = ExportService.to_markdown(
                translation["translated_text"], metadata
            )
        else:
            await query.answer("Unknown format", show_alert=True)
            return

        # Generate filename
        base_name = record.file_name or f"transcript_{record.id}"
        filename = f"{base_name}_translated_{target_lang}.{format_type}"

        # Send file
        file = BufferedInputFile(
            content.encode("utf-8"),
            filename=filename,
        )
        await query.message.answer_document(
            file,
            caption=f"📥 {filename}",
        )

    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        await query.answer(f"Export failed: {str(e)}", show_alert=True)


@router.callback_query(F.data == "history:stats")
async def history_stats_callback(
    query: CallbackQuery,
    transcription_db: TranscriptionDatabase = None,
) -> None:
    """Handle statistics view callback."""
    if not transcription_db:
        await query.answer("⚠️ Database service not available.", show_alert=True)
        return

    user_id = query.from_user.id
    stats = transcription_db.get_statistics(user_id)

    if stats["total_transcriptions"] == 0:
        await query.answer("No transcriptions yet!", show_alert=True)
        return

    lines = ["📊 **Your Statistics**\n"]
    lines.append(f"Total: **{stats['total_transcriptions']}** transcriptions\n")

    if stats["total_duration_seconds"] > 0:
        duration = ExportService._format_duration(stats["total_duration_seconds"])
        lines.append(f"Total audio: **{duration}**\n")

    if stats["providers"]:
        lines.append("**Providers:**")
        for provider, count in stats["providers"].items():
            lines.append(f"• {provider.title()}: {count}")

    await query.answer("\n".join(lines), show_alert=True)
