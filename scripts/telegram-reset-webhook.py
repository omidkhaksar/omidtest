#!/usr/bin/env python3
"""Reset Telegram webhook so local polling works."""

import httpx

from app.config import settings


def main() -> None:
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/deleteWebhook"
    response = httpx.post(url, params={"drop_pending_updates": "true"}, timeout=15.0)
    response.raise_for_status()
    print(response.json())
    print("Webhook removed. Start bot with: python -m app.bot")


if __name__ == "__main__":
    main()
