#!/usr/bin/env python3
"""Run API server and Telegram bot together."""

import asyncio
import logging
import multiprocessing
import os

import uvicorn

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_api() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


def run_bot() -> None:
    from app.bot import main

    main()


def main() -> None:
    mode = settings.run_mode.lower()
    if mode == "api":
        run_api()
        return
    if mode == "bot":
        run_bot()
        return

    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — starting API only")
        run_api()
        return

    api_process = multiprocessing.Process(target=run_api, daemon=True)
    api_process.start()
    logger.info("API running at %s", settings.dashboard_url)

    # Give API a moment to start before bot sends requests
    import time

    time.sleep(1.5)
    run_bot()


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    main()
