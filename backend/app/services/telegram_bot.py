import httpx
import json
import logging
from io import BytesIO
from datetime import datetime, timezone
from html import escape

from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.repositories import ListingRepository, SubscriptionRepository
from app.schemas import Listing

logger = logging.getLogger(__name__)
WHITELIST_DENIED_MESSAGE = "Вы не внесены в белый список бота."


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
            await self._post("setMyCommands", {"commands": [
                {"command": "start", "description": "Открыть Mini App"},
                {"command": "subscribe", "description": "Купить подписку"},
                {"command": "testalert", "description": "Проверить alert-чат"},
            ]})
            await self._post("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Mini App", "web_app": {"url": self.settings.public_base_url.rstrip("/")}}})
            return await self._post("setWebhook", payload)
        except Exception as exc:
            logger.warning("Telegram webhook setup failed: %s", exc)
            return {"ok": False, "description": str(exc)}

    async def handle_update(self, update: dict) -> None:
        pre_checkout = update.get("pre_checkout_query")
        if pre_checkout:
            await self.answer_pre_checkout(pre_checkout)
            return
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        successful_payment = message.get("successful_payment")
        if successful_payment:
            await self.handle_successful_payment(message, successful_payment)
            return
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id:
            return
        user_id = (message.get("from") or {}).get("id")
        if not self._is_allowed(chat_id, user_id):
            logger.info("Ignoring Telegram update from non-whitelisted chat=%s user=%s", chat_id, user_id)
            await self.send_not_whitelisted(chat_id)
            return
        if text.startswith("/subscribe"):
            await self.send_subscribe(chat_id)
            return
        if text.startswith("/grant"):
            await self.handle_grant_command(chat_id, user_id, text)
            return
        if text.startswith("/revoke"):
            await self.handle_revoke_command(chat_id, user_id, text)
            return
        if text.startswith("/testalert"):
            await self.handle_test_alert_command(chat_id, user_id)
            return
        if text.startswith("/start"):
            await self.send_start(chat_id)

    async def create_subscription_invoice(self, user_id: int, plan: dict) -> str:
        payload = f"subscription:{user_id}:{plan['id']}:{int(datetime.now(timezone.utc).timestamp())}"
        response = await self._post(
            "createInvoiceLink",
            {
                "title": f"PRIVATE FLIP - {plan['title']}",
                "description": plan["description"],
                "payload": payload,
                "provider_token": "",
                "currency": "XTR",
                "prices": [{"label": plan["title"], "amount": plan["stars"]}],
            },
        )
        result = response.get("result")
        if not result:
            raise RuntimeError("Telegram did not return invoice link")
        return str(result)

    async def answer_pre_checkout(self, pre_checkout: dict) -> None:
        await self._post("answerPreCheckoutQuery", {"pre_checkout_query_id": pre_checkout.get("id"), "ok": True})

    async def handle_successful_payment(self, message: dict, payment: dict) -> None:
        payload = payment.get("invoice_payload") or ""
        parts = payload.split(":")
        if len(parts) < 4 or parts[0] != "subscription":
            return
        user_id = parts[1]
        plan_id = parts[2]
        status = SubscriptionRepository().activate(
            user_id=user_id,
            plan_id=plan_id,
            payload=payload,
            currency=payment.get("currency") or "",
            total_amount=int(payment.get("total_amount") or 0),
            telegram_payment_charge_id=payment.get("telegram_payment_charge_id"),
            provider_payment_charge_id=payment.get("provider_payment_charge_id"),
        )
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id:
            await self.send_subscription_receipt(chat_id, status)

    async def send_subscription_receipt(self, chat_id: int, status: dict) -> None:
        expires_at = status.get("expires_at")
        expires_text = "навсегда" if not expires_at else self._format_date(expires_at)
        await self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"<b>Подписка активирована</b>\nДоступ: <b>{escape(expires_text)}</b>",
                "parse_mode": "HTML",
            },
        )

    async def handle_grant_command(self, chat_id: int, admin_user_id: int | None, text: str) -> None:
        if not self._is_admin(chat_id, admin_user_id):
            await self.send_admin_denied(chat_id)
            return
        parts = text.split()
        if len(parts) != 3:
            await self.send_grant_usage(chat_id)
            return
        target_user_id = self._parse_telegram_id(parts[1])
        days = self._parse_grant_days(parts[2])
        if target_user_id is None or days == 0:
            await self.send_grant_usage(chat_id)
            return
        status = SubscriptionRepository().grant(target_user_id, days, granted_by=admin_user_id or chat_id)
        expires_at = status.get("expires_at")
        expires_text = "навсегда" if not expires_at else self._format_date(expires_at)
        await self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "<b>Доступ выдан</b>\n"
                    f"Пользователь: <code>{target_user_id}</code>\n"
                    f"Доступ: <b>{escape(expires_text)}</b>"
                ),
                "parse_mode": "HTML",
            },
        )
        await self._try_notify_granted_user(target_user_id, expires_text)

    async def handle_revoke_command(self, chat_id: int, admin_user_id: int | None, text: str) -> None:
        if not self._is_admin(chat_id, admin_user_id):
            await self.send_admin_denied(chat_id)
            return
        parts = text.split()
        if len(parts) != 2:
            await self._post(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Формат: <code>/revoke 123456789</code>",
                    "parse_mode": "HTML",
                },
            )
            return
        target_user_id = self._parse_telegram_id(parts[1])
        if target_user_id is None:
            await self._post(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Telegram ID должен быть числом. Например: <code>/revoke 123456789</code>",
                    "parse_mode": "HTML",
                },
            )
            return
        SubscriptionRepository().revoke(target_user_id)
        await self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"<b>Доступ отозван</b>\nПользователь: <code>{target_user_id}</code>",
                "parse_mode": "HTML",
            },
        )
        await self._try_notify_revoked_user(target_user_id)

    async def handle_test_alert_command(self, chat_id: int, admin_user_id: int | None) -> None:
        if not self._is_admin(chat_id, admin_user_id):
            await self.send_admin_denied(chat_id)
            return
        if not self.settings.telegram_alert_chat_id:
            await self._post("sendMessage", {"chat_id": chat_id, "text": "TELEGRAM_ALERT_CHAT_ID не настроен."})
            return
        try:
            await self._post(
                "sendMessage",
                {
                    "chat_id": self.settings.telegram_alert_chat_id,
                    "text": "<b>FloorHunt test alert</b>\nСообщения в alert-чат доходят.",
                    "parse_mode": "HTML",
                },
            )
        except Exception as exc:
            await self._post("sendMessage", {"chat_id": chat_id, "text": f"Тест не прошёл: {exc}"})
            return
        await self._post("sendMessage", {"chat_id": chat_id, "text": "Тестовое сообщение отправлено в alert-чат."})

    def _is_allowed(self, chat_id: int, user_id: int | None) -> bool:
        allowed_chats = self.settings.telegram_allowed_chat_id_set
        allowed_users = self.settings.telegram_allowed_user_id_set
        if not allowed_chats and not allowed_users:
            return True
        if user_id is not None and SubscriptionRepository().get(user_id)["active"]:
            return True
        return chat_id in allowed_chats or (user_id is not None and user_id in allowed_users)

    def _is_admin(self, chat_id: int, user_id: int | None) -> bool:
        allowed_chats = self.settings.telegram_allowed_chat_id_set
        allowed_users = self.settings.telegram_allowed_user_id_set
        return chat_id in allowed_chats or (user_id is not None and user_id in allowed_users)

    def _parse_telegram_id(self, value: str) -> int | None:
        value = value.strip()
        if not value.isdigit():
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None

    def _parse_grant_days(self, value: str) -> int | None:
        normalized = value.strip().lower()
        if normalized in {"forever", "infinite", "навсегда", "вечность"}:
            return None
        if not normalized.isdigit():
            return 0
        days = int(normalized)
        if days < 1 or days > 3650:
            return 0
        return days

    async def send_admin_denied(self, chat_id: int) -> None:
        await self._post("sendMessage", {"chat_id": chat_id, "text": "Эта команда доступна только админу."})

    async def send_grant_usage(self, chat_id: int) -> None:
        await self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "Формат: <code>/grant 123456789 30</code>\n"
                    "Или навсегда: <code>/grant 123456789 forever</code>"
                ),
                "parse_mode": "HTML",
            },
        )

    async def _try_notify_granted_user(self, user_id: int, expires_text: str) -> None:
        try:
            await self._post(
                "sendMessage",
                {
                    "chat_id": user_id,
                    "text": f"<b>Вам выдали доступ</b>\nДоступ: <b>{escape(expires_text)}</b>",
                    "parse_mode": "HTML",
                },
            )
        except Exception as exc:
            logger.info("Cannot notify granted user %s: %s", user_id, exc)

    async def _try_notify_revoked_user(self, user_id: int) -> None:
        try:
            await self._post("sendMessage", {"chat_id": user_id, "text": "Ваш доступ к боту был отозван."})
        except Exception as exc:
            logger.info("Cannot notify revoked user %s: %s", user_id, exc)

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

    async def send_subscribe(self, chat_id: int) -> None:
        app_url = (self.settings.public_base_url or "").rstrip("/")
        payload = {
            "chat_id": chat_id,
            "text": (
                "<b>Подписка PRIVATE FLIP</b>\n\n"
                "Чтобы получить подписку, перейдите в приложение и откройте раздел <b>Моя подписка</b>."
            ),
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[{"text": "Перейти в приложение", "web_app": {"url": app_url}}]]
            },
        }
        await self._post("sendMessage", payload)

    async def send_not_whitelisted(self, chat_id: int) -> None:
        await self._post("sendMessage", {"chat_id": chat_id, "text": WHITELIST_DENIED_MESSAGE})

    async def send_new_listing_alerts(
        self,
        repo: ListingRepository,
        limit: int = 15,
        first_seen_after: str | None = None,
        collection_names: list[str] | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> int:
        recipients = SubscriptionRepository().active_recipient_ids()
        if not recipients:
            return 0
        sent = 0
        for listing in repo.find_unnotified(
            limit,
            first_seen_after=first_seen_after,
            collection_names=collection_names,
            min_price=min_price,
            max_price=max_price,
        ):
            delivered = False
            try:
                for chat_id in recipients:
                    await self.send_listing_alert(listing, chat_id=chat_id)
                    delivered = True
                if delivered:
                    repo.mark_notified(listing.source, listing.external_id)
                    sent += 1
            except Exception as exc:
                logger.warning("Telegram listing alert failed for %s: %s", listing.external_id, exc)
        return sent

    async def send_listing_alert(self, listing: Listing, chat_id: str | int | None = None) -> None:
        text = self._format_listing_alert(listing)
        payload = {
            "chat_id": chat_id or self.settings.telegram_alert_chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if listing.telegram_url:
            payload["link_preview_options"] = {
                "is_disabled": True,
            }
        if listing.marketplace_url:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть лот", "url": listing.marketplace_url}]]}
        await self._post("sendMessage", payload)
        await self._send_listing_preview(listing, chat_id=chat_id)

    async def _send_listing_preview(self, listing: Listing, chat_id: str | int | None = None) -> None:
        if not listing.image_url:
            return
        buttons: list[dict[str, str]] = []
        if listing.telegram_url:
            buttons.append({"text": "Telegram", "url": listing.telegram_url})
        if listing.marketplace_url:
            buttons.append({"text": "MRKT", "url": listing.marketplace_url})
        payload = {
            "chat_id": chat_id or self.settings.telegram_alert_chat_id,
            "caption": self._format_preview_caption(listing),
            "parse_mode": "HTML",
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [buttons]}
        try:
            image = await self._download_preview_image(listing.image_url)
            if not image:
                return
            await self._post_file(
                "sendPhoto",
                data=payload,
                files={"photo": ("preview.png", image, "image/png")},
            )
        except Exception as exc:
            logger.warning("Telegram listing preview failed for %s: %s", listing.external_id, exc)

    async def _download_preview_image(self, image_url: str) -> bytes | None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(image_url, headers={"user-agent": "Mozilla/5.0"})
            response.raise_for_status()
        try:
            image = Image.open(BytesIO(response.content))
            image.thumbnail((1024, 1024))
            output = BytesIO()
            image.convert("RGBA").save(output, format="PNG")
            return output.getvalue()
        except UnidentifiedImageError:
            logger.warning("Cannot decode listing preview image: %s", image_url)
            return None

    def _format_preview_caption(self, listing: Listing) -> str:
        title = f"{listing.collection_name} #{listing.number}" if listing.number else listing.collection_name
        return "\n".join([
            "<b>Telegram preview</b>",
            escape(title),
        ])

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
            f"Модель: <b>{escape(listing.model_name or 'не указана')}</b>",
            "",
            "<b>Владельцы:</b>",
            "<blockquote>" + escape(self._format_owners(listing)) + "</blockquote>",
            f"Флор гифта: <b>{gift_floor} TON</b>",
            f"Флор модели: <b>{model_floor} TON</b>",
            self._format_combo_market(listing),
            "",
            "<b>Последние продажи модели:</b>",
            "<blockquote>" + escape(self._format_model_sales(listing)) + "</blockquote>",
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
        parts.append(self._format_listing_activity(listing))
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

    def _format_model_sales(self, listing: Listing) -> str:
        try:
            sales = json.loads(listing.model_recent_sales or "[]")
        except json.JSONDecodeError:
            sales = []
        parts: list[str] = []
        for sale in sales[:5]:
            number = sale.get("number") or "-"
            price = self._format_sale_ton(sale.get("price"))
            platform = sale.get("platform") or "MRKT"
            date = sale.get("date") or ""
            parts.append(f"#{number} за {price} TON на {platform} - {self._format_days_ago(date)}")
        return "\n".join(parts) if parts else "Нет свежих продаж модели"

    def _format_sale_ton(self, value: float | int | str | None) -> str:
        if value is None:
            return "-"
        try:
            text = f"{float(value):.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(value)
        return text if "." in text else f"{text}.0"

    def _format_listing_activity(self, listing: Listing) -> str:
        owner = listing.current_owner or "владелец неизвестен"
        date = listing.last_sale_at or listing.first_seen_at or listing.updated_at
        return f"1. {owner}: {self._format_ton(listing.price)} TON, {self._format_days_ago(date)}"

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
        return f"{days} {self._day_word(days)} назад"

    def _day_word(self, value: int) -> str:
        if value % 10 == 1 and value % 100 != 11:
            return "день"
        if value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
            return "дня"
        return "дней"

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
                raise RuntimeError(f"Telegram API {method} failed with status {response.status_code}")
            return response.json()

    async def _post_file(self, method: str, data: dict, files: dict) -> dict:
        if not self.settings.telegram_bot_token:
            return {}
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method}"
        form_data = {
            key: json.dumps(value) if isinstance(value, (dict, list)) else value
            for key, value in data.items()
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, data=form_data, files=files)
            if response.is_error:
                logger.warning("Telegram API %s failed: %s", method, response.text)
                raise RuntimeError(f"Telegram API {method} failed with status {response.status_code}")
            return response.json()
