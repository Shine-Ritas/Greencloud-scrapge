import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class TelegramError(Exception):
    pass


def send_message(settings, text):
    if not settings.telegram_configured:
        raise TelegramError("Telegram is not configured")
    request = Request(
        f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
        data=json.dumps({"chat_id": settings.telegram_chat_id, "text": text,
                         "link_preview_options": {"is_disabled": True}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(65536))
    except HTTPError as error:
        # Never log the request URL: it contains the bot token.
        try:
            description = json.loads(error.read(65536)).get("description", "Request rejected")
        except Exception:
            description = "Request rejected"
        description = str(description).replace(settings.telegram_token, "[redacted]")[:250]
        raise TelegramError(f"Telegram HTTP {error.code}: {description}") from None
    except Exception:
        raise TelegramError("Telegram connection failed or returned an invalid response") from None
    if not payload.get("ok"):
        raise TelegramError("Telegram did not confirm delivery")
