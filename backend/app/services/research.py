import asyncio
from datetime import datetime, timezone
from html.parser import HTMLParser
from hashlib import sha1
from typing import Any

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions, types

from app.catalog import default_collection_names, is_blocked_collection_model
from app.database import init_db
from app.repositories import ListingRepository, ResearchRunRepository
from app.services.mrkt_client import MrktClient


class GiftTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, str] = {}
        self._row: list[str] = []
        self._capture: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        if tag in {"th", "td"}:
            self._capture = tag
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._capture == tag:
            value = " ".join("".join(self._text).split())
            self._row.append(value)
            self._capture = None
            self._text = []
        if tag == "tr" and len(self._row) >= 2:
            self.rows[self._row[0].lower()] = self._row[1]


class ResearchService:
    def __init__(self, mrkt: MrktClient, listings: ListingRepository, runs: ResearchRunRepository):
        self.mrkt = mrkt
        self.listings = listings
        self.runs = runs
        self._lock = asyncio.Lock()
        self._collection_floor_cache: dict[str, float | None] = {}
        self._model_floor_cache: dict[tuple[str, str], float | None] = {}
        self._combo_market_cache: dict[tuple[str, str, str], tuple[int | None, float | None]] = {}

    async def run(self, collection_names: list[str] | None = None) -> int:
        async with self._lock:
            init_db()
            collections = collection_names or default_collection_names()
            normalized: list[dict] = []
            telegram_client = await self._telegram_client()
            try:
                for name in collections:
                    try:
                        gifts = await self.mrkt.saling([name], max_price=self.mrkt.settings.mrkt_max_price)
                        for gift in gifts:
                            listing = await self._normalize_gift(gift, name)
                            if self._is_quality_listing(listing):
                                await self._enrich_combo_market(listing)
                                await self._enrich_public_metadata(listing)
                                await self._enrich_unique_gift_metadata(listing, telegram_client)
                                normalized.append(listing)
                    except Exception as exc:
                        self.runs.add("mrkt", "error", f"{name}: {exc}")
            finally:
                if telegram_client:
                    await telegram_client.disconnect()
            count = self.listings.upsert_many(normalized)
            self.runs.add("mrkt", "success", f"stored {count} listings")
            return count

    async def _normalize_gift(self, gift: dict[str, Any], fallback_collection: str) -> dict:
        collection = self._deep_pick(gift, "collectionName", "collection", "collectionTitle", "giftName") or fallback_collection
        number = str(self._deep_pick(gift, "number", "giftNumber", "num", "gift_num", "giftNum") or "") or None
        external_id = str(self._pick(gift, "id", "giftId", "slug") or sha1(repr(gift).encode()).hexdigest())
        price = self._price(gift, "salePrice", "salePriceWithoutFee", "priceNano", "price", "tonPrice")
        model_name = self._deep_pick(gift, "modelName", "model")
        backdrop_name = self._deep_pick(gift, "backdropName", "backdrop", "backgroundName")
        floor_price = self._price(gift, "floorPriceNanoTONsByCollection", "collectionFloor", "floorPrice") or await self._collection_floor(collection)
        model_floor_price = self._price(gift, "floorPriceNanoTONsByBackdropModel", "modelFloor", "backdropModelFloor") or await self._model_floor(collection, model_name)
        now = datetime.now(timezone.utc).isoformat()
        return {
            "source": "mrkt",
            "external_id": external_id,
            "collection_name": collection,
            "name": f"{collection} #{number}" if number else collection,
            "number": number,
            "model_name": model_name,
            "backdrop_name": backdrop_name,
            "symbol_name": self._deep_pick(gift, "symbolName", "symbol"),
            "image_url": self._fragment_image_url(collection, number) or self._image_url(gift),
            "price": price or 0,
            "floor_price": floor_price,
            "model_floor_price": model_floor_price,
            "sales_count": self._int_value(self._deep_pick(gift, "salesCount", "sales_count")),
            "uses_count": self._int_value(
                self._deep_pick(
                    gift,
                    "usesCount",
                    "uses_count",
                    "usedCount",
                    "used_count",
                    "availabilityIssued",
                    "availability_issued",
                )
            ),
            "uses_total": self._int_value(
                self._deep_pick(gift, "usesTotal", "uses_total", "availabilityTotal", "availability_total")
            ),
            "combo_listed_count": None,
            "combo_floor_price": None,
            "current_owner": None,
            "original_sender": None,
            "original_recipient": None,
            "original_gift_at": None,
            "last_sale_at": None,
            "last_sale_price": None,
            "last_sale_currency": None,
            "initial_sale_at": None,
            "initial_sale_price": None,
            "initial_sale_currency": None,
            "initial_sale_stars": None,
            "received_at": self._deep_pick(gift, "receivedDate", "received_at"),
            "export_at": self._deep_pick(gift, "exportDate", "export_at"),
            "next_resale_at": self._deep_pick(gift, "nextResaleDate", "next_resale_at"),
            "next_transfer_at": self._deep_pick(gift, "nextTransferDate", "next_transfer_at"),
            "marketplace_url": self._marketplace_url(gift),
            "telegram_url": self._telegram_url(collection, number),
            "first_seen_at": now,
            "updated_at": now,
        }

    def _is_quality_listing(self, listing: dict) -> bool:
        if not listing.get("image_url") or not listing.get("price"):
            return False
        if is_blocked_collection_model(listing.get("collection_name"), listing.get("model_name")):
            return False
        if listing["price"] > self.mrkt.settings.mrkt_max_price:
            return False
        gift_floor = listing.get("floor_price")
        model_floor = listing.get("model_floor_price")
        if self.mrkt.settings.mrkt_min_gift_floor and (not gift_floor or gift_floor < self.mrkt.settings.mrkt_min_gift_floor):
            return False
        premium_backdrops = {name.lower() for name in self.mrkt.settings.premium_backdrop_list}
        backdrop = str(listing.get("backdrop_name") or "").lower()
        has_premium_backdrop = backdrop in premium_backdrops
        has_expensive_model = bool(model_floor and model_floor >= self.mrkt.settings.mrkt_min_model_floor)
        return has_premium_backdrop or has_expensive_model

    async def _enrich_combo_market(self, listing: dict) -> None:
        count, floor = await self._combo_market(
            listing.get("collection_name"),
            listing.get("model_name"),
            listing.get("backdrop_name"),
        )
        listing["combo_listed_count"] = count
        listing["combo_floor_price"] = floor

    async def _combo_market(self, collection: str | None, model: str | None, backdrop: str | None) -> tuple[int | None, float | None]:
        if not collection or not model or not backdrop:
            return None, None
        key = (collection, model, backdrop)
        if key in self._combo_market_cache:
            return self._combo_market_cache[key]
        count = 0
        floor_price: float | None = None
        cursor = ""
        seen_cursors: set[str] = set()
        try:
            while True:
                page = await self.mrkt.saling_page(
                    [collection],
                    model_names=[model],
                    backdrop_names=[backdrop],
                    count=20,
                    cursor=cursor,
                    use_default_max_price=False,
                )
                gifts = page.get("gifts", []) or []
                if gifts and floor_price is None:
                    floor_price = self._price(gifts[0], "salePrice", "salePriceWithoutFee", "priceNano", "price", "tonPrice")
                count += len(gifts)
                cursor = page.get("cursor") or ""
                if not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
        except Exception:
            result = (None, None)
        else:
            result = (count, floor_price)
        self._combo_market_cache[key] = result
        return result

    async def _enrich_public_metadata(self, listing: dict) -> None:
        url = listing.get("telegram_url")
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                response = await client.get(url, headers={"user-agent": "Mozilla/5.0"})
                response.raise_for_status()
        except Exception:
            return
        parser = GiftTableParser()
        parser.feed(response.text)
        owner = parser.rows.get("owner")
        if owner:
            listing["current_owner"] = owner

    async def _telegram_client(self) -> TelegramClient | None:
        settings = self.mrkt.settings
        if not settings.telegram_api_id or not settings.telegram_api_hash or not settings.telegram_session:
            return None
        try:
            client = TelegramClient(StringSession(settings.telegram_session), settings.telegram_api_id, settings.telegram_api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return None
            return client
        except Exception:
            return None

    async def _enrich_unique_gift_metadata(self, listing: dict, client: TelegramClient | None) -> None:
        if not client:
            return
        slug = self._telegram_slug(listing.get("telegram_url"))
        if not slug:
            return
        try:
            unique = await client(functions.payments.GetUniqueStarGiftRequest(slug=slug))
            value_info = await client(functions.payments.GetUniqueStarGiftValueInfoRequest(slug=slug))
        except Exception:
            return

        gift = getattr(unique, "gift", None)
        if gift:
            uses_count = self._int_value(getattr(gift, "availability_issued", None))
            uses_total = self._int_value(getattr(gift, "availability_total", None))
            if uses_count is not None:
                listing["uses_count"] = uses_count
            if uses_total is not None:
                listing["uses_total"] = uses_total
            owner = self._peer_display(getattr(gift, "owner_id", None), unique.users, unique.chats) or getattr(gift, "owner_name", None)
            if owner:
                listing["current_owner"] = owner
            for attribute in getattr(gift, "attributes", []) or []:
                if isinstance(attribute, types.StarGiftAttributeOriginalDetails):
                    sender = self._peer_display(getattr(attribute, "sender_id", None), unique.users, unique.chats)
                    recipient = self._peer_display(getattr(attribute, "recipient_id", None), unique.users, unique.chats)
                    if sender:
                        listing["original_sender"] = sender
                    if recipient:
                        listing["original_recipient"] = recipient
                    listing["original_gift_at"] = self._iso_datetime(getattr(attribute, "date", None))

        currency = getattr(value_info, "currency", None)
        listing["last_sale_at"] = self._iso_datetime(getattr(value_info, "last_sale_date", None))
        listing["last_sale_price"] = self._currency_amount(getattr(value_info, "last_sale_price", None), currency)
        listing["last_sale_currency"] = currency if listing["last_sale_price"] is not None else None
        listing["initial_sale_at"] = self._iso_datetime(getattr(value_info, "initial_sale_date", None))
        listing["initial_sale_price"] = self._currency_amount(getattr(value_info, "initial_sale_price", None), currency)
        listing["initial_sale_currency"] = currency if listing["initial_sale_price"] is not None else None
        listing["initial_sale_stars"] = self._int_value(getattr(value_info, "initial_sale_stars", None))

    def _telegram_slug(self, url: str | None) -> str | None:
        if not url:
            return None
        return url.rstrip("/").rsplit("/", 1)[-1] or None

    def _peer_display(self, peer: Any, users: list[Any], chats: list[Any]) -> str | None:
        if isinstance(peer, types.PeerUser):
            item = next((user for user in users if getattr(user, "id", None) == peer.user_id), None)
            return self._user_display(item) if item else str(peer.user_id)
        if isinstance(peer, (types.PeerChannel, types.PeerChat)):
            peer_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
            item = next((chat for chat in chats if getattr(chat, "id", None) == peer_id), None)
            return self._chat_display(item) if item else str(peer_id)
        return None

    def _user_display(self, user: Any) -> str | None:
        if not user:
            return None
        username = getattr(user, "username", None)
        if username:
            return f"@{username}"
        name = " ".join(part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part)
        return name or str(getattr(user, "id", "")) or None

    def _chat_display(self, chat: Any) -> str | None:
        if not chat:
            return None
        username = getattr(chat, "username", None)
        if username:
            return f"@{username}"
        return getattr(chat, "title", None) or str(getattr(chat, "id", "")) or None

    def _iso_datetime(self, value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return value if isinstance(value, str) and value else None

    def _currency_amount(self, value: Any, currency: str | None) -> float | None:
        if value in (None, ""):
            return None
        amount = float(value)
        if currency == "TON":
            return round(amount / 1_000_000_000, 4)
        if currency in {"XTR", "STARS"}:
            return amount
        return round(amount / 100, 2)

    async def _collection_floor(self, collection: str) -> float | None:
        if collection in self._collection_floor_cache:
            return self._collection_floor_cache[collection]
        try:
            gifts = await self.mrkt.saling([collection], count=1, use_default_max_price=False)
            value = self._price(gifts[0], "salePrice", "salePriceWithoutFee", "priceNano", "price", "tonPrice") if gifts else None
        except Exception:
            value = None
        self._collection_floor_cache[collection] = value
        return value

    async def _model_floor(self, collection: str, model: str | None) -> float | None:
        if not model:
            return None
        key = (collection, model)
        if key in self._model_floor_cache:
            return self._model_floor_cache[key]
        try:
            gifts = await self.mrkt.saling([collection], model_names=[model], count=1, use_default_max_price=False)
            value = self._price(gifts[0], "salePrice", "salePriceWithoutFee", "priceNano", "price", "tonPrice") if gifts else None
        except Exception:
            value = None
        self._model_floor_cache[key] = value
        return value

    def _price(self, data: dict[str, Any], *keys: str) -> float | None:
        value = self._deep_pick(data, *keys)
        if value in (None, ""):
            return None
        price = float(value)
        joined = " ".join(keys).lower()
        if "nano" in joined or price > 1_000_000:
            return round(price / 1_000_000_000, 4)
        return price

    def _int_value(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _marketplace_url(self, data: dict[str, Any]) -> str | None:
        url = self._deep_pick(data, "url", "link")
        if url:
            return url
        start_app = self._deep_pick(data, "startApp", "startapp", "startAppPayload", "slug", "id")
        return f"https://t.me/mrkt/app?startapp={start_app}" if start_app else None

    def _telegram_url(self, collection: str, number: str | None) -> str | None:
        if not collection or not number:
            return None
        slug = "".join(part for part in collection.title() if part.isalnum())
        return f"https://t.me/nft/{slug}-{number}"

    def _fragment_image_url(self, collection: str, number: str | None) -> str | None:
        if not collection or not number:
            return None
        slug = "".join(part for part in collection.title() if part.isalnum())
        return f"https://nft.fragment.com/gift/{slug.lower()}-{number}.webp"

    def _pick(self, data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("url")
            if value not in (None, ""):
                return value
        return None

    def _deep_pick(self, data: Any, *keys: str) -> Any:
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, dict):
                    value = value.get("name") or value.get("title") or value.get("value") or value.get("url")
                if value not in (None, ""):
                    return value
            for value in data.values():
                found = self._deep_pick(value, *keys)
                if found not in (None, ""):
                    return found
        if isinstance(data, list):
            for item in data:
                found = self._deep_pick(item, *keys)
                if found not in (None, ""):
                    return found
        return None

    def _image_url(self, data: Any) -> str | None:
        if isinstance(data, dict):
            key = data.get("modelStickerThumbnailKey")
            if isinstance(key, str) and key:
                return f"https://cdn.tgmrkt.io/{key}"
            for key, value in data.items():
                lowered = key.lower()
                if isinstance(value, str) and self._looks_like_image_url(value, lowered):
                    return value
                found = self._image_url(value)
                if found:
                    return found
        if isinstance(data, list):
            for item in data:
                found = self._image_url(item)
                if found:
                    return found
        return None

    def _looks_like_image_url(self, value: str, key: str) -> bool:
        if not value.startswith("http"):
            return False
        lowered = value.lower()
        if any(part in key for part in ("image", "photo", "preview", "thumb", "media", "picture")):
            return True
        return any(part in lowered for part in ("cdn.tgmrkt", "static", ".webp", ".png", ".jpg", ".jpeg", ".gif"))


class DealAnalyzer:
    def apply_scores(self, listings):
        floors: dict[str, float] = {}
        for item in listings:
            floors[item.collection_name] = min(floors.get(item.collection_name, item.price), item.price)
        for item in listings:
            item.floor_price = item.floor_price or floors.get(item.collection_name)
            if item.floor_price and item.price:
                item.deal_score = round((item.floor_price / item.price) * 100, 2)
        return sorted(listings, key=lambda row: (row.collection_name, row.price))
