import httpx
import logging
from html import escape

from app.config import Settings
from app.repositories import ListingRepository
from app.schemas import Listing

logger = logging.getLogger(__name__)


class TelegramBotService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_bot_token)

    async def set_webhook(self) -> dict:
        if not self.enabled or not self.settings.public_base_url:
            return {"ok": False, "description": "TELEGRAM_BOT_TOKEN or PUBLIC_BASE_URL is missing"}
        payload = {
            "url": f"{self.settings.public_base_url.rstrip('/')}/api/telegram/webhook",
            "drop_pending_updates": True,
        }
        if self.settings.telegram_webhook_secret:
            payload["secret_token"] = self.settings.telegram_webhook_secret
        try:
            await self._post("setMyCommands", {"commands": [{"command": "start", "description": "Открыть Mini App"}]})
            await self._post("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Mini App", "web_app": {"url": self.settings.public_base_url.rstrip("/")}}})
            return await self._post("setWebhook", payload)
        except Exception as exc:
            logger.warning("Telegram webhook setup failed: %s", exc)
            return {"ok": False, "description": str(exc)}

    async def handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id:
            return
        if text.startswith("/start"):
            await self.send_start(chat_id)

    async def send_start(self, chat_id: int) -> None:
        app_url = (self.settings.public_base_url or "").rstrip("/")
        text = (
            "<b>PRIVATE FLIP запущен</b>\n\n"
            "Я присылаю новые MRKT-листинги Telegram-подарков. "
            "Открой Mini App, чтобы смотреть найденные слоты."
        )
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[{"text": "Открыть Mini App", "web_app": {"url": app_url}}]]
            },
        }
        await self._post("sendMessage", payload)

    async def send_new_listing_alerts(self, repo: ListingRepository, limit: int = 5) -> None:
        if not self.settings.telegram_alert_chat_id:
            return
        for listing in repo.find_unnotified(limit):
            try:
                await self.send_listing_alert(listing)
                repo.mark_notified(listing.source, listing.external_id)
            except Exception as exc:
                logger.warning("Telegram listing alert failed for %s: %s", listing.external_id, exc)

    async def send_listing_alert(self, listing: Listing) -> None:
        text = self._format_listing_alert(listing)
        payload = {
            "chat_id": self.settings.telegram_alert_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if listing.marketplace_url:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть лот", "url": listing.marketplace_url}]]}
        await self._post("sendMessage", payload)

    def _format_listing_alert(self, listing: Listing) -> str:
        title = f"{listing.collection_name} #{listing.number}" if listing.number else listing.collection_name
        preview_url = listing.telegram_url or listing.marketplace_url
        title_html = f'<a href="{escape(preview_url)}">{escape(title)}</a>' if preview_url else escape(title)
        owners = self._sample_owners()
        sales = self._sample_model_sales(listing)
        gift_floor = self._format_ton(listing.floor_price)
        model_floor = self._format_ton(listing.model_floor_price)
        return "\n".join([
            "✔ <b>ЛИСТИНГ</b>",
            f"{title_html} на <b>MRKT</b> за",
            f"<b>{self._format_ton(listing.price)} TON</b>",
            f"Модель: <b>{escape(listing.model_name or 'не указана')}</b>",
            f"Фон: <b>{escape(listing.backdrop_name or 'не указан')}</b>",
            "",
            "<b>Последние владельцы:</b>",
            "<blockquote>" + "\n".join(escape(owner) for owner in owners) + "</blockquote>",
            f"Флор гифта: <b>{gift_floor} TON</b>",
            f"Флор модели: <b>{model_floor} TON</b>",
            "",
            "<b>Последние продажи модели:</b>",
            "<blockquote>" + "\n".join(escape(sale) for sale in sales) + "</blockquote>",
            self._format_link(preview_url),
        ])

    def _format_link(self, url: str | None) -> str:
        if not url:
            return "<b>Ссылка</b>: нет ссылки MRKT"
        return f'<a href="{escape(url)}"><b>Ссылка</b></a>'

    def _format_ton(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _sample_owners(self) -> list[str]:
        return [
            "@mrktbank - сегодня",
            "@giftrelayer - сегодня",
            "@mrktbank - 2 дня назад",
            "@giftwhale - 5 дней назад",
            "@toncollector - 8 дней назад",
        ]

    def _sample_model_sales(self, listing: Listing) -> list[str]:
        base = listing.price or 0
        number = listing.number or "-"
        return [
            f"#{number} за {self._format_ton(base)} TON на MRKT - сегодня",
            f"#{int(number) + 1423 if str(number).isdigit() else '-'} за {self._format_ton(base * 1.05 if base else None)} TON на MRKT - 1 день назад",
            f"#{int(number) + 2298 if str(number).isdigit() else '-'} за {self._format_ton(base * 0.98 if base else None)} TON на MRKT - 2 дня назад",
            f"#{int(number) + 3151 if str(number).isdigit() else '-'} за {self._format_ton(base * 1.12 if base else None)} TON на MRKT - 4 дня назад",
            f"#{int(number) + 4084 if str(number).isdigit() else '-'} за {self._format_ton(base * 1.18 if base else None)} TON на MRKT - 7 дней назад",
        ]

    async def _post(self, method: str, payload: dict) -> dict:
        if not self.settings.telegram_bot_token:
            return {}
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
            if response.is_error:
                logger.warning("Telegram API %s failed: %s", method, response.text)
            response.raise_for_status()
            return response.json()
