from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..services import (
    DeepgramModelPreferences,
    ProviderPreferences,
    TranscriberRegistry,
)
from ..services.audio_optimizer import TranscriptCache
from ..services.queue_service import TaskQueue

router = Router()


@router.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer(
        "Kirim atau forward file audio, voice note, atau video ke bot ini. "
        "Bot akan mengunduh hingga 2GB dan mengirimkan transkrip dari provider pilihan Anda "
        "(Groq atau Deepgram). Gunakan /provider untuk mengganti layanan."
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "🎵 **Transhades Transcription Bot**\n\n"
        "**Cara Pakai:**\n"
        "• Kirim/forward file audio atau video\n"
        "• Bot akan transkripsi otomatis\n"
        "• Support hingga 2GB file!\n\n"
        "**Basic Commands:**\n"
        "/start - Info bot\n"
        "/help - Panduan ini\n"
        "/provider - Pilih provider (Groq/Deepgram)\n"
        "/status - Cek status bot & cache\n\n"
        "**📚 History & Search:**\n"
        "/history - Lihat riwayat transkripsi (20 terakhir)\n"
        "/search <keyword> - Cari dalam transcript\n"
        "/stats - Statistik penggunaan Anda\n\n"
        "**🌐 Translation:**\n"
        "/translate <lang> - Translate hasil terakhir\n"
        "/languages - Lihat bahasa yang didukung (20+ bahasa)\n\n"
        "**📥 Export:**\n"
        "/export - Download transcript dalam format:\n"
        "  • TXT (plain text)\n"
        "  • Markdown (.md)\n"
        "  • SRT (subtitles)\n"
        "  • VTT (web subtitles)\n\n"
        "**Features:**\n"
        "✨ Smart cache (instant untuk file duplikat)\n"
        "⚡ 5 concurrent workers\n"
        "🎵 Auto-compression untuk large files\n"
        "🔄 Auto-retry jika gagal\n"
        "💾 SQLite database untuk history\n"
        "🌐 Multi-language translation"
    )


@router.message(Command("status"))
async def status_command(
    message: Message,
    transcript_cache: TranscriptCache = None,
    task_queue: TaskQueue = None,
    telethon_downloader=None,
) -> None:
    """Show bot status, cache stats, queue info, and API rotation stats."""
    status_lines = ["🤖 **Bot Status**\n"]

    # API Rotation stats
    if telethon_downloader:
        try:
            api_stats = await telethon_downloader.get_stats()
            available_apis = sum(1 for api in api_stats.values() if api["available"])
            total_apis = len(api_stats)

            status_lines.append("🔄 **API Rotation:**")
            status_lines.append(f"• Available APIs: {available_apis}/{total_apis}")

            for api_name, api_data in api_stats.items():
                status_emoji = "✅" if api_data["available"] else "⏳"
                status_text = "Ready" if api_data["available"] else "FloodWait"

                if api_data["in_flood_wait"] and api_data["flood_wait_until"]:
                    status_text = f"FloodWait until {api_data['flood_wait_until']}"

                status_lines.append(
                    f"  {status_emoji} {api_name}: {status_text} "
                    f"({api_data['success_rate']} success, "
                    f"{api_data['total_requests']} requests)"
                )
            status_lines.append("")
        except Exception as e:
            status_lines.append(f"⚠️ API stats unavailable: {e}\n")

    # Queue stats
    if task_queue:
        stats = await task_queue.get_stats()
        status_lines.append("📊 **Queue Statistics:**")
        status_lines.append(
            f"• Active workers: {stats['active_workers']}/{task_queue.max_workers}"
        )
        status_lines.append(f"• Queue size: {stats['queue_size']}")
        status_lines.append(f"• Total tasks: {stats['total_tasks']}")
        status_lines.append(f"• Completed: {stats['by_status'].get('completed', 0)}")
        status_lines.append(f"• Failed: {stats['by_status'].get('failed', 0)}")
        status_lines.append(f"• Processing: {stats['by_status'].get('processing', 0)}")

        if stats.get("avg_processing_time"):
            status_lines.append(f"• Avg time: {stats['avg_processing_time']:.1f}s")
        status_lines.append("")

    # Cache stats
    if transcript_cache:
        cache_size = len(transcript_cache)
        cache_max = transcript_cache.max_size
        cache_pct = (cache_size / cache_max * 100) if cache_max > 0 else 0

        status_lines.append("💾 **Cache Statistics:**")
        status_lines.append(
            f"• Cached items: {cache_size}/{cache_max} ({cache_pct:.0f}%)"
        )
        status_lines.append(f"• Type: In-memory")
        status_lines.append("")

        if cache_size > 0:
            status_lines.append("✨ **Cache Working!**")
            status_lines.append("File duplikat akan instant dari cache!")
        else:
            status_lines.append("ℹ️ Cache kosong - belum ada file yang diproses")

    status_lines.append("\n🚀 **Bot Online & Ready!**")
    status_lines.append("\n💡 Kirim file audio/video untuk mulai transkripsi")

    await message.answer("\n".join(status_lines))


def _build_provider_keyboard(
    transcriber_registry: TranscriberRegistry,
    provider_preferences: ProviderPreferences,
    deepgram_model_preferences: DeepgramModelPreferences,
    chat_id: int,
) -> InlineKeyboardMarkup:
    current_provider = provider_preferences.get(chat_id)
    keyboard = []
    for provider in transcriber_registry.providers():
        label = provider.title()
        if provider == current_provider:
            label = "✅ " + label
        keyboard.append(
            [InlineKeyboardButton(text=label, callback_data=f"provider:{provider}")]
        )

    if "deepgram" in transcriber_registry.providers():
        current_model = deepgram_model_preferences.get(chat_id)
        model_buttons = []
        for model_code, caption in (("whisper", "Whisper"), ("nova-3", "Nova-3")):
            label = caption
            if current_model == model_code:
                label = "✅ " + caption
            model_buttons.append(
                InlineKeyboardButton(
                    text=label, callback_data=f"deepgram_model:{model_code}"
                )
            )
        keyboard.append(model_buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.message(Command("provider"))
async def provider_command(
    message: Message,
    transcriber_registry: TranscriberRegistry,
    provider_preferences: ProviderPreferences,
    deepgram_model_preferences: DeepgramModelPreferences,
) -> None:
    keyboard = _build_provider_keyboard(
        transcriber_registry,
        provider_preferences,
        deepgram_model_preferences,
        message.chat.id,
    )
    await message.answer(
        "Pilih penyedia transkripsi dan model Deepgram (jika diperlukan) untuk chat ini:",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data and c.data.startswith("provider:"))
async def provider_callback(
    query: CallbackQuery,
    transcriber_registry: TranscriberRegistry,
    provider_preferences: ProviderPreferences,
    deepgram_model_preferences: DeepgramModelPreferences,
) -> None:
    if not query.data:
        return

    provider = query.data.split(":", maxsplit=1)[1]
    if provider not in transcriber_registry.providers():
        await query.answer("Provider tidak tersedia.", show_alert=True)
        return

    current_provider = provider_preferences.get(query.message.chat.id)
    if provider == current_provider:
        await query.answer("Provider sudah aktif.")
        return

    provider_preferences.set(query.message.chat.id, provider)
    keyboard = _build_provider_keyboard(
        transcriber_registry,
        provider_preferences,
        deepgram_model_preferences,
        query.message.chat.id,
    )
    await query.message.edit_text(
        f"Provider transkripsi diubah ke {provider}.",
        reply_markup=keyboard,
    )
    await query.answer("Provider diperbarui.")


@router.callback_query(lambda c: c.data and c.data.startswith("deepgram_model:"))
async def deepgram_model_callback(
    query: CallbackQuery,
    transcriber_registry: TranscriberRegistry,
    provider_preferences: ProviderPreferences,
    deepgram_model_preferences: DeepgramModelPreferences,
) -> None:
    if not query.data:
        return

    if "deepgram" not in transcriber_registry.providers():
        await query.answer("Deepgram tidak dikonfigurasi.", show_alert=True)
        return

    model = query.data.split(":", maxsplit=1)[1]
    if model not in ("whisper", "nova-3"):
        await query.answer("Model tidak didukung.", show_alert=True)
        return

    current_model = deepgram_model_preferences.get(query.message.chat.id)
    current_provider = provider_preferences.get(query.message.chat.id)

    if model == current_model and current_provider == "deepgram":
        await query.answer("Model Deepgram sudah aktif.")
        return

    deepgram_model_preferences.set(query.message.chat.id, model)
    provider_preferences.set(query.message.chat.id, "deepgram")

    keyboard = _build_provider_keyboard(
        transcriber_registry,
        provider_preferences,
        deepgram_model_preferences,
        query.message.chat.id,
    )
    await query.message.edit_text(
        f"Provider transkripsi diubah ke deepgram dengan model {model}.",
        reply_markup=keyboard,
    )
    await query.answer("Model Deepgram diperbarui.")
