from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application

from app.bot import register_handlers
from app.config import settings

logger = logging.getLogger(__name__)

_application: Optional[Application] = None


def get_application() -> Optional[Application]:
    return _application


async def start_telegram_webhook() -> None:
    global _application
    if not settings.telegram_bot_token or not settings.use_webhook:
        return

    dashboard = settings.dashboard_url.rstrip("/")
    if "YOUR-PROJECT" in dashboard or dashboard.startswith("http://localhost"):
        logger.error(
            "DASHBOARD_URL must be your real Vercel URL for webhook mode (got %s). "
            "Use TELEGRAM_MODE=polling for local dev.",
            dashboard,
        )
        return

    _application = Application.builder().token(settings.telegram_bot_token).build()
    register_handlers(_application)
    await _application.initialize()
    await _application.start()

    webhook_url = f"{settings.dashboard_url.rstrip('/')}/api/telegram/webhook"
    await _application.bot.set_webhook(
        url=webhook_url,
        secret_token=settings.telegram_webhook_secret or None,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info("Telegram webhook registered at %s", webhook_url)


async def stop_telegram_webhook() -> None:
    global _application
    if not _application:
        return
    try:
        await _application.bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.exception("Failed to delete Telegram webhook")
    await _application.stop()
    await _application.shutdown()
    _application = None


async def process_webhook_update(data: dict) -> None:
    if not _application:
        raise RuntimeError("Telegram webhook is not initialized")
    update = Update.de_json(data, _application.bot)
    await _application.process_update(update)
