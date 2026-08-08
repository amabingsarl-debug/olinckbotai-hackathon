import smtplib
from email.message import EmailMessage

import httpx

from app.core.config import get_settings


class NotificationManager:
    async def send(self, title: str, message: str) -> dict:
        return {
            "telegram": await self._telegram(title, message),
            "discord": await self._discord(title, message),
            "email": self._email(title, message),
            "browser": {"title": title, "message": message},
        }

    async def _telegram(self, title: str, message: str) -> bool:
        settings = get_settings()
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return False
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": f"{title}\n{message}"},
            )
        return True

    async def _discord(self, title: str, message: str) -> bool:
        settings = get_settings()
        if not settings.discord_webhook_url:
            return False
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(settings.discord_webhook_url, json={"content": f"**{title}**\n{message}"})
        return True

    def _email(self, title: str, message: str) -> bool:
        settings = get_settings()
        if not all([settings.smtp_host, settings.smtp_user, settings.smtp_password, settings.alert_email_to]):
            return False
        email = EmailMessage()
        email["From"] = settings.smtp_user or ""
        email["To"] = settings.alert_email_to or ""
        email["Subject"] = title
        email.set_content(message)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(email)
        return True
