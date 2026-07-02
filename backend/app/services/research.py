import asyncio
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

from app.catalog import default_collection_names
from app.repositories import ListingRepository, ResearchRunRepository
from app.services.mrkt_client import MrktClient


class ResearchService:
    def __init__(self, mrkt: MrktClient, listings: ListingRepository, runs: ResearchRunRepository):
        self.mrkt = mrkt
        self.listings = listings
        self.runs = runs
        self._lock = asyncio.Lock()

    async def run(self, collection_names: list[str] | None = None) -> int:
        async with self._lock:
            collections = collection_names or default_collection_names()
            normalized: list[dict] = []
            for name in collections:
                try:
                    gifts = await self.mrkt.saling([name])
                    normalized.extend(self._normalize_gift(gift, name) for gift in gifts)
                except Exception as exc:
                    self.runs.add("mrkt", "error", f"{name}: {exc}")
            count = self.listings.upsert_many(normalized)
            self.runs.add("mrkt", "success", f"stored {count} listings")
            return count

    def _normalize_gift(self, gift: dict[str, Any], fallback_collection: str) -> dict:
        collection = self._pick(gift, "collectionName", "collection", "giftName") or fallback_collection
        number = str(self._pick(gift, "number", "giftNumber") or "") or None
        external_id = str(self._pick(gift, "id", "giftId", "slug") or sha1(repr(gift).encode()).hexdigest())
        price = float(self._pick(gift, "price", "salePrice", "tonPrice") or 0)
        return {
            "source": "mrkt",
            "external_id": external_id,
            "collection_name": collection,
            "name": f"{collection} #{number}" if number else collection,
            "number": number,
            "model_name": self._pick(gift, "modelName", "model"),
            "backdrop_name": self._pick(gift, "backdropName", "backdrop", "backgroundName"),
            "symbol_name": self._pick(gift, "symbolName", "symbol"),
            "image_url": self._pick(gift, "image", "imageUrl", "photoUrl", "previewUrl"),
            "price": price,
            "marketplace_url": self._pick(gift, "url", "link"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _pick(self, data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("url")
            if value not in (None, ""):
                return value
        return None


class DealAnalyzer:
    def apply_scores(self, listings):
        floors: dict[str, float] = {}
        for item in listings:
            floors[item.collection_name] = min(floors.get(item.collection_name, item.price), item.price)
        for item in listings:
            item.floor_price = floors.get(item.collection_name)
            if item.floor_price and item.price:
                item.deal_score = round((item.floor_price / item.price) * 100, 2)
        return sorted(listings, key=lambda row: (row.collection_name, row.price))
