from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from .config import Settings

logger = logging.getLogger(__name__)


class WebhookConfig:
    """
    Webhook configuration untuk production deployment.
    Lebih efisien dibanding long polling untuk high-traffic bots.
    """

    def __init__(
        self,
        webhook_url: str,
        webhook_path: str = "/webhook",
        host: str = "0.0.0.0",
        port: int = 8080,
        secret_token: Optional[str] = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.webhook_path = webhook_path
        self.host = host
        self.port = port
        self.secret_token = secret_token

    @property
    def full_webhook_url(self) -> str:
        """Full webhook URL untuk Telegram."""
        return f"{self.webhook_url.rstrip('/')}{self.webhook_path}"

    @classmethod
    def from_env(cls, settings: Settings) -> Optional["WebhookConfig"]:
        """Load webhook config dari environment variables."""
        import os

        webhook_url = os.getenv("WEBHOOK_URL")
        if not webhook_url:
            return None

        return cls(
            webhook_url=webhook_url,
            webhook_path=os.getenv("WEBHOOK_PATH", "/webhook"),
            host=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
            port=int(os.getenv("WEBHOOK_PORT", "8080")),
            secret_token=os.getenv("WEBHOOK_SECRET"),
        )


async def setup_webhook(
    bot: Bot,
    dispatcher: Dispatcher,
    config: WebhookConfig,
) -> web.Application:
    """
    Setup webhook untuk bot.

    Args:
        bot: Aiogram Bot instance
        dispatcher: Aiogram Dispatcher instance
        config: Webhook configuration

    Returns:
        aiohttp web application
    """
    # Set webhook
    await bot.set_webhook(
        url=config.full_webhook_url,
        allowed_updates=dispatcher.resolve_used_update_types(),
        secret_token=config.secret_token,
        drop_pending_updates=True,
    )

    logger.info(
        "Webhook set to %s (secret: %s)",
        config.full_webhook_url,
        "enabled" if config.secret_token else "disabled",
    )

    # Create aiohttp application
    app = web.Application()

    # Create request handler
    handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=config.secret_token,
    )

    # Register webhook handler
    handler.register(app, path=config.webhook_path)

    # Setup application
    setup_application(app, dispatcher, bot=bot)

    # Health check endpoint
    async def health_check(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "bot": "running"})

    app.router.add_get("/health", health_check)

    logger.info("Webhook application configured")
    return app


async def start_webhook_server(
    app: web.Application,
    config: WebhookConfig,
) -> None:
    """
    Start webhook server.

    Args:
        app: aiohttp web application
        config: Webhook configuration
    """
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host=config.host,
        port=config.port,
    )

    await site.start()

    logger.info(
        "Webhook server started on %s:%d",
        config.host,
        config.port,
    )


async def remove_webhook(bot: Bot) -> None:
    """Remove webhook dan kembali ke polling mode."""
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook removed, bot can now use polling")


class WebhookManager:
    """
    Manager untuk handle webhook lifecycle.
    Auto-detect mode berdasarkan environment variables.
    """

    def __init__(self, bot: Bot, dispatcher: Dispatcher, settings: Settings) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self.settings = settings
        self.webhook_config = WebhookConfig.from_env(settings)

    @property
    def is_webhook_mode(self) -> bool:
        """Check apakah bot running dalam webhook mode."""
        return self.webhook_config is not None

    async def start(self) -> None:
        """
        Start bot dengan mode yang sesuai (webhook atau polling).
        """
        if self.is_webhook_mode:
            await self._start_webhook()
        else:
            await self._start_polling()

    async def _start_webhook(self) -> None:
        """Start bot dalam webhook mode."""
        logger.info("Starting bot in WEBHOOK mode")

        app = await setup_webhook(
            bot=self.bot,
            dispatcher=self.dispatcher,
            config=self.webhook_config,
        )

        await start_webhook_server(app, self.webhook_config)

        # Keep running
        import asyncio

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutting down webhook server...")
        finally:
            await remove_webhook(self.bot)

    async def _start_polling(self) -> None:
        """Start bot dalam polling mode."""
        logger.info("Starting bot in POLLING mode")

        # Pastikan tidak ada webhook yang aktif
        await remove_webhook(self.bot)

        # Start polling
        await self.dispatcher.start_polling(self.bot)


# Utility functions untuk manual webhook management


async def process_webhook_update(
    bot: Bot,
    dispatcher: Dispatcher,
    update_data: dict,
) -> None:
    """
    Process single webhook update manually.
    Berguna untuk custom webhook handlers.

    Args:
        bot: Bot instance
        dispatcher: Dispatcher instance
        update_data: Raw update data from Telegram
    """
    update = Update(**update_data)
    await dispatcher.feed_update(bot, update)


def create_webhook_handler(
    bot: Bot,
    dispatcher: Dispatcher,
    secret_token: Optional[str] = None,
):
    """
    Create custom webhook handler function untuk frameworks lain
    (FastAPI, Flask, Django, etc).

    Example untuk FastAPI:
        ```python
        from fastapi import FastAPI, Request, Header

        app = FastAPI()
        handler = create_webhook_handler(bot, dispatcher, secret_token="xxx")

        @app.post("/webhook")
        async def webhook(
            request: Request,
            x_telegram_bot_api_secret_token: str = Header(None)
        ):
            return await handler(request, x_telegram_bot_api_secret_token)
        ```
    """

    async def handler(request, token_header: Optional[str] = None):
        # Verify secret token
        if secret_token and token_header != secret_token:
            logger.warning("Invalid webhook secret token")
            return {"status": "error", "message": "Invalid secret token"}

        # Get update data
        if hasattr(request, "json"):
            # FastAPI/Starlette
            update_data = await request.json()
        elif hasattr(request, "get_json"):
            # Flask
            update_data = request.get_json()
        else:
            # aiohttp or custom
            update_data = await request.json()

        # Process update
        try:
            await process_webhook_update(bot, dispatcher, update_data)
            return {"status": "ok"}
        except Exception as e:
            logger.exception("Error processing webhook update")
            return {"status": "error", "message": str(e)}

    return handler


# Environment variables documentation
"""
Webhook mode environment variables:

WEBHOOK_URL=https://yourdomain.com
WEBHOOK_PATH=/webhook (optional, default: /webhook)
WEBHOOK_HOST=0.0.0.0 (optional, default: 0.0.0.0)
WEBHOOK_PORT=8080 (optional, default: 8080)
WEBHOOK_SECRET=your-secret-token (optional, untuk keamanan tambahan)

Jika WEBHOOK_URL tidak di-set, bot akan otomatis menggunakan polling mode.

Deployment examples:

1. Docker:
   docker run -e WEBHOOK_URL=https://yourdomain.com -p 8080:8080 your-bot

2. Railway/Heroku:
   Set environment variable WEBHOOK_URL di dashboard

3. VPS dengan Nginx reverse proxy:
   - Set WEBHOOK_URL=https://yourdomain.com
   - Configure Nginx untuk proxy ke port 8080

4. Cloudflare Tunnel:
   - Setup cloudflared tunnel
   - Set WEBHOOK_URL ke tunnel URL
"""
