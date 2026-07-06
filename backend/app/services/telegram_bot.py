import httpx
import logging
from datetime import datetime, timezone
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
            "drop_pending_updates": False,
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
        user_id = (message.get("from") or {}).get("id")
        if not self._is_allowed(chat_id, user_id):
            logger.info("Ignoring Telegram update from non-whitelisted chat=%s user=%s", chat_id, user_id)
            return
        if text.startswith("/start"):
            await self.send_start(chat_id)

    def _is_allowed(self, chat_id: int, user_id: int | None) -> bool:
        allowed_chats = self.settings.telegram_allowed_chat_id_set
        allowed_users = self.settings.telegram_allowed_user_id_set
        if not allowed_chats and not allowed_users:
            return True
        return chat_id in allowed_chats or (user_id is not None and user_id in allowed_users)

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

    async def send_new_listing_alerts(self, repo: ListingRepository, limit: int = 5, first_seen_after: str | None = None) -> int:
        if not self.settings.telegram_alert_chat_id:
            return 0
        sent = 0
        for listing in repo.find_unnotified(limit, first_seen_after=first_seen_after):
            try:
                await self.send_listing_alert(listing)
                repo.mark_notified(listing.source, listing.external_id)
                sent += 1
            except Exception as exc:
                logger.warning("Telegram listing alert failed for %s: %s", listing.external_id, exc)
        return sent

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
        gift_floor = self._format_ton(listing.floor_price)
        model_floor = self._format_ton(listing.model_floor_price)
        return "\n".join([
            "✔ <b>ЛИСТИНГ</b>",
            f"{title_html} на <b>MRKT</b> за",
            f"<b>{self._format_ton(listing.price)} TON</b>",
            f"Фон: <b>{escape(listing.backdrop_name or 'не указан')}</b>",
            "",
            "<b>Владельцы:</b>",
            "<blockquote>" + escape(self._format_owners(listing)) + "</blockquote>",
            f"Флор гифта: <b>{gift_floor} TON</b>",
            f"Флор модели: <b>{model_floor} TON</b>",
            self._format_combo_market(listing),
            "",
            "<b>Активность:</b>",
            "<blockquote>" + escape(self._format_activity(listing)) + "</blockquote>",
            self._format_links(listing, preview_url),
        ])

    def _format_combo_market(self, listing: Listing) -> str:
        parts: list[str] = []
        if listing.combo_listed_count is not None:
            parts.append(f"{self._format_int(listing.combo_listed_count)} на MRKT")
        if listing.combo_floor_price is not None:
            parts.append(f"от {self._format_ton(listing.combo_floor_price)} TON")
        if not parts:
            return "Сочетание: <b>нет данных MRKT</b>"
        return f"Сочетание: <b>{' / '.join(parts)}</b>"

    def _format_links(self, listing: Listing, visual_url: str | None) -> str:
        links: list[str] = []
        if listing.marketplace_url:
            links.append(f'<a href="{escape(listing.marketplace_url)}">MRKT</a>')
        if visual_url:
            links.append(f'<a href="{escape(visual_url)}">Визуал</a>')
        if not links:
            return "<b>Ссылки:</b> нет"
        return f"<b>Ссылки:</b> {' | '.join(links)}"

    def _format_ton(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _format_int(self, value: int | None) -> str:
        return f"{value:,}".replace(",", " ") if value is not None else "-"

    def _format_owners(self, listing: Listing) -> str:
        parts: list[str] = []
        if listing.current_owner:
            parts.append(f"Текущий владелец: {listing.current_owner}")
        if listing.original_sender:
            parts.append(f"Первый отправитель: {listing.original_sender}")
        if listing.original_recipient:
            parts.append(f"Первый получатель: {listing.original_recipient}")
        return "\n".join(parts) if parts else "Владельцы не найдены"

    def _format_activity(self, listing: Listing) -> str:
        parts: list[str] = []
        parts.extend(self._format_sale_activity(listing))
        if listing.sales_count is not None:
            parts.append(f"Продаж MRKT: {listing.sales_count}")
        if listing.next_resale_at:
            parts.append(f"Ресейл: {self._format_availability(listing.next_resale_at)}")
        if listing.next_transfer_at:
            parts.append(f"Трансфер: {self._format_availability(listing.next_transfer_at)}")
        if listing.received_at:
            parts.append(f"Получен: {self._format_days_ago(listing.received_at)}")
        parts = parts[:5]
        if not parts:
            parts.append("Нет данных активности")
        return "\n".join(parts)

    def _format_sale_activity(self, listing: Listing) -> list[str]:
        sales: list[tuple[str, str, str]] = []
        if listing.last_sale_at and listing.last_sale_price is not None:
            sales.append((
                listing.last_sale_at,
                "Продажа",
                self._format_money(listing.last_sale_price, listing.last_sale_currency),
            ))
        if listing.initial_sale_at and (listing.initial_sale_price is not None or listing.initial_sale_stars is not None):
            initial_price = (
                self._format_money(listing.initial_sale_price, listing.initial_sale_currency)
                if listing.initial_sale_price is not None
                else "-"
            )
            stars = f" / {listing.initial_sale_stars} Stars" if listing.initial_sale_stars is not None else ""
            sales.append((listing.initial_sale_at, "Первая", f"{initial_price}{stars}"))
        sales.sort(key=lambda row: self._date_sort_key(row[0]), reverse=True)
        return [
            f"{index}. {label}: {price}, {self._format_days_ago(date)}"
            for index, (date, label, price) in enumerate(sales[:5], start=1)
        ]

    def _format_money(self, value: float | None, currency: str | None) -> str:
        if value is None:
            return "-"
        formatted = f"{value:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
        return f"{formatted} {currency or ''}".strip()

    def _format_date(self, value: str) -> str:
        parsed = self._parse_datetime(value)
        if not parsed:
            return value
        return parsed.strftime("%d.%m.%Y %H:%M UTC")

    def _format_availability(self, value: str) -> str:
        parsed = self._parse_datetime(value)
        if not parsed:
            return value
        if parsed <= datetime.now(timezone.utc):
            return "доступна"
        return self._format_date(value)

    def _format_days_ago(self, value: str) -> str:
        parsed = self._parse_datetime(value)
        if not parsed:
            return value
        days = max(0, (datetime.now(timezone.utc).date() - parsed.date()).days)
        return f"{days} дн назад"

    def _date_sort_key(self, value: str) -> datetime:
        return self._parse_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)

    def _parse_datetime(self, value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

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
