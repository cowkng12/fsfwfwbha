import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from hashlib import sha1
from typing import Any

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions, types

from app.catalog import (
    collection_requires_priority_model,
    default_collection_names,
    has_collection_quality_rules,
    has_collection_specific_quality,
    is_blocked_collection_model,
    priority_collection_search_models,
)
from app.database import init_db
from app.repositories import ListingRepository, ResearchRunRepository
from app.services.mrkt_client import MrktClient

PRIORITY_MODEL_SCAN_LIMIT = 96
PRIORITY_BACKDROP_SCAN_LIMIT = 120
PRIORITY_FILTER_BATCH_SIZE = 4
PRIORITY_FILTER_RESULT_COUNT = 20
RAW_CANDIDATE_SCAN_LIMIT = 40
ACCEPTED_LISTINGS_PER_COLLECTION = 3
MAX_LISTINGS_PER_RUN = 25
MODEL_SALE_SAMPLE_SIZE = 6
MODEL_RECENT_SALES_LIMIT = 3
COMBO_MARKET_MAX_PAGES = 3
# Keep the most visually strong and color-harmony-friendly backdrops first.
PRIORITY_BACKDROP_ORDER = [
    "Onyx Black",
    "Black",
    "White",
    "Platinum",
    "Mystic Pearl",
    "Cloud White",
    "Silver",
    "Gold",
    "Pure Gold",
    "Rose Gold",
    "Sapphire",
    "Ruby",
    "Emerald",
    "Cyberpunk",
    "Electric Indigo",
    "Electric Purple",
    "Neon Blue",
    "Azure Blue",
    "Cobalt Blue",
    "Celtic Blue",
    "French Blue",
    "Pacific Green",
    "Aquamarine",
    "Turquoise",
    "Teal",
    "Sea Foam",
    "Malachite",
    "Fuchsia",
    "Magenta",
    "Lavender",
    "Purple",
    "Violet",
    "Lilac",
    "Coral",
    "Peach",
    "Sunset Orange",
    "Amber",
    "Bronze",
    "Navy Blue",
    "Olive",
]
MODEL_PALETTE_HINTS: dict[str, set[str]] = {
    "beret": {"black", "grey", "white"},
    "bumblebee": {"yellow", "black"},
    "lady bits": {"pink", "purple", "white"},
    "megavolt": {"blue", "purple", "cyan"},
    "sweet kiss": {"pink", "white", "purple"},
    "anniversary": {"gold", "white", "silver"},
    "art project": {"purple", "pink", "white"},
    "ring of roots": {"green", "brown"},
    "asteroid": {"grey", "silver", "black"},
    "goldsmith": {"gold", "yellow"},
    "hourglass": {"gold", "brown", "silver"},
    "neo matrix": {"green", "black", "blue"},
    "spatial grid": {"blue", "silver", "grey"},
    "fireball": {"red", "orange", "yellow"},
    "highway": {"grey", "black", "blue"},
    "chrome": {"silver", "grey", "black"},
    "halo": {"gold", "white", "silver"},
    "prism": {"blue", "purple", "cyan", "silver"},
    "velvet": {"purple", "red", "black"},
    "orbit": {"blue", "black", "silver"},
    "bloom": {"pink", "green", "white"},
    "mosaic": {"blue", "purple", "gold"},
    "neon": {"blue", "purple", "cyan"},
    "ribbon": {"red", "pink", "gold"},
    "coral": {"red", "orange", "pink"},
}
COLOR_KEYWORDS: dict[str, set[str]] = {
    "gold": {"gold", "amber", "mustard", "yellow", "pure gold", "satin gold", "rose gold", "bronze"},
    "silver": {"silver", "platinum", "grey", "gray", "steel", "gunmetal", "battleship grey", "white", "pearl", "chrome", "cloud"},
    "black": {"black", "onyx", "midnight", "dark", "navy"},
    "blue": {"blue", "azure", "indigo", "cyan", "navy", "sapphire", "cobalt", "pacific", "sky"},
    "green": {"green", "emerald", "malachite", "hunter", "mint", "shamrock", "olive", "sea foam"},
    "red": {"red", "crimson", "ruby", "burgundy", "carmine", "fire"},
    "purple": {"purple", "violet", "lilac", "fandango", "magenta", "fuchsia", "lavender", "indigo"},
    "brown": {"brown", "chestnut", "chocolate", "copper", "desert", "sand", "bronze"},
    "pink": {"pink", "blush", "rose", "kiss", "peach", "coral"},
    "cyan": {"cyan", "aquamarine", "turquoise", "teal", "aqua", "sea foam"},
    "orange": {"orange", "sunset", "peach", "coral"},
    "white": {"white", "pearl"},
}


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
        self._priority_model_cache: dict[str, list[str]] = {}
        self._relaxed_model_cache: dict[str, list[str]] = {}
        self._model_sales_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def run(self, collection_names: list[str] | None = None) -> int:
        async with self._lock:
            init_db()
            collections = collection_names or default_collection_names()
            normalized: list[dict] = []
            telegram_client = await self._telegram_client()
            try:
                for name in collections:
                    if len(normalized) >= MAX_LISTINGS_PER_RUN:
                        break
                    try:
                        gifts = await self._candidate_gifts(name)
                        accepted_for_collection = 0
                        for gift in gifts:
                            listing = await self._normalize_gift(gift, name)
                            if self._is_quality_listing(listing):
                                await self._enrich_model_sales(listing, telegram_client)
                                await self._enrich_combo_market(listing)
                                await self._enrich_public_metadata(listing)
                                await self._enrich_unique_gift_metadata(listing, telegram_client)
                                normalized.append(listing)
                                accepted_for_collection += 1
                                if accepted_for_collection >= ACCEPTED_LISTINGS_PER_COLLECTION or len(normalized) >= MAX_LISTINGS_PER_RUN:
                                    break
                    except Exception as exc:
                        self.runs.add("mrkt", "error", f"{name}: {exc}")
                if not normalized:
                    normalized.extend(await self._relaxed_listings(collections, telegram_client))
            finally:
                if telegram_client:
                    await telegram_client.disconnect()
            count = self.listings.upsert_many(normalized)
            self.runs.add("mrkt", "success", f"stored {count} listings")
            return count

    async def _candidate_gifts(self, collection: str) -> list[dict[str, Any]]:
        max_price = self.mrkt.settings.mrkt_research_max_price
        gifts = await self.mrkt.saling([collection], max_price=max_price)

        model_names = await self._priority_model_names(collection)
        if model_names:
            gifts.extend(await self._filtered_gifts(collection, "model", model_names, max_price))

        if not collection_requires_priority_model(collection):
            backdrop_names = self._priority_backdrop_names()
            if backdrop_names:
                gifts.extend(await self._filtered_gifts(collection, "backdrop", backdrop_names, max_price))

        return self._limit_candidate_gifts(self._dedupe_gifts(gifts), RAW_CANDIDATE_SCAN_LIMIT)

    async def _relaxed_listings(self, collections: list[str], telegram_client: TelegramClient | None) -> list[dict]:
        normalized: list[dict] = []
        for name in collections:
            if len(normalized) >= MAX_LISTINGS_PER_RUN:
                break
            try:
                gifts = await self._relaxed_candidate_gifts(name)
                accepted_for_collection = 0
                for gift in gifts:
                    listing = await self._normalize_gift(gift, name)
                    if self._is_relaxed_quality_listing(listing):
                        await self._enrich_model_sales(listing, telegram_client)
                        await self._enrich_combo_market(listing)
                        await self._enrich_public_metadata(listing)
                        await self._enrich_unique_gift_metadata(listing, telegram_client)
                        normalized.append(listing)
                        accepted_for_collection += 1
                        if accepted_for_collection >= ACCEPTED_LISTINGS_PER_COLLECTION or len(normalized) >= MAX_LISTINGS_PER_RUN:
                            break
            except Exception as exc:
                self.runs.add("mrkt", "error", f"{name} relaxed: {exc}")
        return normalized

    async def debug_candidate_quality(self, collection_names: list[str] | None = None, sample_size: int = 8) -> dict[str, Any]:
        collections = collection_names or default_collection_names()
        summary: dict[str, Any] = {
            "settings": {
                "mrkt_max_price": self.mrkt.settings.mrkt_max_price,
                "mrkt_research_max_price": self.mrkt.settings.mrkt_research_max_price,
                "mrkt_min_model_floor": self.mrkt.settings.mrkt_min_model_floor,
                "mrkt_max_model_rarity": self.mrkt.settings.mrkt_max_model_rarity,
                "mrkt_max_backdrop_rarity": self.mrkt.settings.mrkt_max_backdrop_rarity,
            },
            "total_candidates": 0,
            "strict_pass": 0,
            "relaxed_pass": 0,
            "rejections": {},
            "collections": [],
            "accepted_examples": [],
        }
        rejection_counts: dict[str, int] = {}
        for collection in collections:
            item = {"collection": collection, "candidates": 0, "strict_pass": 0, "relaxed_pass": 0, "examples": []}
            try:
                gifts = await self.mrkt.saling([collection], count=sample_size, max_price=self.mrkt.settings.mrkt_research_max_price)
                item["candidates"] = len(gifts)
                summary["total_candidates"] += len(gifts)
                for gift in gifts:
                    listing = await self._normalize_gift(gift, collection)
                    strict_pass = self._is_quality_listing(listing)
                    relaxed_pass = self._is_relaxed_quality_listing(listing)
                    item["strict_pass"] += int(strict_pass)
                    item["relaxed_pass"] += int(relaxed_pass)
                    summary["strict_pass"] += int(strict_pass)
                    summary["relaxed_pass"] += int(relaxed_pass)
                    if strict_pass or relaxed_pass:
                        if len(summary["accepted_examples"]) < 10:
                            summary["accepted_examples"].append(self._debug_listing_summary(listing))
                    else:
                        reasons = self._quality_rejection_reasons(listing)
                        for reason in reasons:
                            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                    if len(item["examples"]) < 3:
                        example = self._debug_listing_summary(listing)
                        example["strict_pass"] = strict_pass
                        example["relaxed_pass"] = relaxed_pass
                        example["reasons"] = [] if strict_pass or relaxed_pass else self._quality_rejection_reasons(listing)
                        item["examples"].append(example)
            except Exception as exc:
                item["error"] = str(exc)
            summary["collections"].append(item)
        summary["rejections"] = dict(sorted(rejection_counts.items(), key=lambda row: row[1], reverse=True))
        return summary

    async def _relaxed_candidate_gifts(self, collection: str) -> list[dict[str, Any]]:
        max_price = self.mrkt.settings.mrkt_research_max_price
        gifts = await self.mrkt.saling([collection], max_price=max_price)

        model_names = await self._relaxed_model_names(collection)
        if model_names:
            gifts.extend(await self._filtered_gifts(collection, "model", model_names, max_price))

        backdrop_names = self._priority_backdrop_names()
        if backdrop_names:
            gifts.extend(await self._filtered_gifts(collection, "backdrop", backdrop_names, max_price))

        return self._limit_candidate_gifts(self._dedupe_gifts(gifts), RAW_CANDIDATE_SCAN_LIMIT)

    async def _priority_model_names(self, collection: str) -> list[str]:
        if collection in self._priority_model_cache:
            return self._priority_model_cache[collection]
        if has_collection_quality_rules(collection):
            models = priority_collection_search_models(collection)
        else:
            models = await self._market_priority_model_names(collection)
        self._priority_model_cache[collection] = models
        return models

    async def _market_priority_model_names(self, collection: str) -> list[str]:
        try:
            models = await self.mrkt.gift_trait_options("models", [collection])
        except Exception:
            return []
        ranked: list[tuple[float, float, str]] = []
        for item in models:
            name = item.get("modelTitle") or item.get("modelName")
            if not name:
                continue
            floor = self._nano_price(item.get("floorPriceNanoTons")) or 0
            rarity = self._rarity_value(item.get("rarityPerMille")) or 999
            if floor >= self.mrkt.settings.mrkt_min_model_floor or rarity <= self.mrkt.settings.mrkt_max_model_rarity:
                ranked.append((-floor, rarity, name))
        ranked.sort(key=lambda row: (row[0], row[1], row[2]))
        return [name for _, _, name in ranked[:PRIORITY_MODEL_SCAN_LIMIT]]

    async def _relaxed_model_names(self, collection: str) -> list[str]:
        if collection in self._relaxed_model_cache:
            return self._relaxed_model_cache[collection]
        try:
            models = await self.mrkt.gift_trait_options("models", [collection])
        except Exception:
            self._relaxed_model_cache[collection] = []
            return []
        floor_threshold = max(5, self.mrkt.settings.mrkt_min_model_floor * 0.6)
        rarity_threshold = self.mrkt.settings.mrkt_max_model_rarity + 1
        ranked: list[tuple[float, float, str]] = []
        for item in models:
            name = item.get("modelTitle") or item.get("modelName")
            if not name:
                continue
            floor = self._nano_price(item.get("floorPriceNanoTons")) or 0
            rarity = self._rarity_value(item.get("rarityPerMille")) or 999
            if floor >= floor_threshold or rarity <= rarity_threshold:
                ranked.append((-floor, rarity, name))
        ranked.sort(key=lambda row: (row[0], row[1], row[2]))
        self._relaxed_model_cache[collection] = [name for _, _, name in ranked[:PRIORITY_MODEL_SCAN_LIMIT]]
        return self._relaxed_model_cache[collection]

    def _priority_backdrop_names(self) -> list[str]:
        premium = {name.lower(): name for name in self.mrkt.settings.premium_backdrop_list}
        ordered = [premium[name.lower()] for name in PRIORITY_BACKDROP_ORDER if name.lower() in premium]
        ordered.extend(name for key, name in premium.items() if key not in {item.lower() for item in ordered})
        return ordered[:PRIORITY_BACKDROP_SCAN_LIMIT]

    async def _filtered_gifts(self, collection: str, kind: str, names: list[str], max_price: float) -> list[dict[str, Any]]:
        gifts: list[dict[str, Any]] = []
        for batch in self._chunks(names, PRIORITY_FILTER_BATCH_SIZE):
            try:
                gifts.extend(await self._saling_by_filter(collection, kind, batch, max_price))
            except Exception:
                for name in batch:
                    try:
                        gifts.extend(await self._saling_by_filter(collection, kind, [name], max_price))
                    except Exception:
                        continue
        return gifts

    async def _saling_by_filter(self, collection: str, kind: str, names: list[str], max_price: float) -> list[dict[str, Any]]:
        if kind == "model":
            return await self.mrkt.saling(
                [collection],
                model_names=names,
                count=PRIORITY_FILTER_RESULT_COUNT,
                max_price=max_price,
            )
        return await self.mrkt.saling(
            [collection],
            backdrop_names=names,
            count=PRIORITY_FILTER_RESULT_COUNT,
            max_price=max_price,
        )

    def _chunks(self, items: list[str], size: int):
        for index in range(0, len(items), size):
            yield items[index:index + size]

    def _dedupe_gifts(self, gifts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for gift in gifts:
            unique.setdefault(self._gift_key(gift), gift)
        return list(unique.values())

    def _limit_candidate_gifts(self, gifts: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        ranked = sorted(gifts, key=lambda gift: self._price(gift, "salePrice", "salePriceWithoutFee", "priceNano", "price", "tonPrice") or 999999)
        return ranked[:limit]

    def _gift_key(self, gift: dict[str, Any]) -> str:
        key = self._pick(gift, "id", "giftIdString", "giftId", "slug")
        return str(key or sha1(repr(gift).encode()).hexdigest())

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
        telegram_url = self._telegram_url_from_gift(gift, number) or self._telegram_url(collection, number)
        return {
            "source": "mrkt",
            "external_id": external_id,
            "collection_name": collection,
            "name": f"{collection} #{number}" if number else collection,
            "number": number,
            "model_name": model_name,
            "model_rarity": self._rarity_value(self._deep_pick(gift, "modelRarityPerMille", "modelRarity")),
            "backdrop_name": backdrop_name,
            "backdrop_rarity": self._rarity_value(self._deep_pick(gift, "backdropRarityPerMille", "backdropRarity")),
            "symbol_name": self._deep_pick(gift, "symbolName", "symbol"),
            "image_url": self._fragment_image_url_from_url(telegram_url) or self._fragment_image_url(collection, number) or self._image_url(gift),
            "price": price or 0,
            "floor_price": floor_price,
            "model_floor_price": model_floor_price,
            "model_last_sale_at": None,
            "model_recent_sales": "[]",
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
            "telegram_url": telegram_url,
            "first_seen_at": now,
            "updated_at": now,
        }

    def _is_quality_listing(self, listing: dict) -> bool:
        if not listing.get("image_url") or not listing.get("price"):
            return False
        if is_blocked_collection_model(listing.get("collection_name"), listing.get("model_name")):
            return False
        if listing["price"] > self.mrkt.settings.mrkt_research_max_price:
            return False
        if has_collection_quality_rules(listing.get("collection_name")):
            return has_collection_specific_quality(
                listing.get("collection_name"),
                listing.get("model_name"),
                listing.get("backdrop_name"),
            )
        gift_floor = listing.get("floor_price")
        model_floor = listing.get("model_floor_price")
        model_rarity = listing.get("model_rarity")
        backdrop_rarity = listing.get("backdrop_rarity")
        if self.mrkt.settings.mrkt_min_gift_floor and (not gift_floor or gift_floor < self.mrkt.settings.mrkt_min_gift_floor):
            return False
        premium_backdrops = {name.lower() for name in self.mrkt.settings.premium_backdrop_list}
        backdrop = str(listing.get("backdrop_name") or "").lower()
        has_premium_backdrop = backdrop in premium_backdrops
        has_expensive_model = bool(model_floor and model_floor >= self.mrkt.settings.mrkt_min_model_floor)
        has_rare_model = bool(model_rarity and model_rarity <= self.mrkt.settings.mrkt_max_model_rarity)
        has_rare_backdrop = bool(backdrop_rarity and backdrop_rarity <= self.mrkt.settings.mrkt_max_backdrop_rarity)
        has_harmony = self._has_color_harmony(listing.get("model_name"), listing.get("backdrop_name"))
        if listing["price"] <= self.mrkt.settings.mrkt_max_price and has_harmony and self._has_liquidity_signal(listing, relaxed=True):
            return True
        return has_premium_backdrop or has_expensive_model or has_rare_model or has_rare_backdrop

    def _is_relaxed_quality_listing(self, listing: dict) -> bool:
        if not listing.get("image_url") or not listing.get("price"):
            return False
        if is_blocked_collection_model(listing.get("collection_name"), listing.get("model_name")):
            return False
        if listing["price"] > self.mrkt.settings.mrkt_research_max_price:
            return False
        premium_backdrops = {name.lower() for name in self.mrkt.settings.premium_backdrop_list}
        backdrop = str(listing.get("backdrop_name") or "").lower()
        has_visual_signal = backdrop in premium_backdrops or self._has_color_harmony(
            listing.get("model_name"),
            listing.get("backdrop_name"),
        )
        return has_visual_signal and self._has_liquidity_signal(listing, relaxed=True)

    def _has_liquidity_signal(self, listing: dict, relaxed: bool = False) -> bool:
        model_floor = listing.get("model_floor_price")
        gift_floor = listing.get("floor_price")
        model_rarity = listing.get("model_rarity")
        backdrop_rarity = listing.get("backdrop_rarity")
        model_floor_threshold = self.mrkt.settings.mrkt_min_model_floor if not relaxed else max(5, self.mrkt.settings.mrkt_min_model_floor * 0.6)
        rarity_bonus = 0 if not relaxed else 1
        has_model_floor = bool(model_floor and model_floor >= model_floor_threshold)
        has_model_rarity = bool(model_rarity and model_rarity <= self.mrkt.settings.mrkt_max_model_rarity + rarity_bonus)
        has_backdrop_rarity = bool(backdrop_rarity and backdrop_rarity <= self.mrkt.settings.mrkt_max_backdrop_rarity + rarity_bonus)
        has_floor_discount = bool(gift_floor and listing.get("price") and listing["price"] <= gift_floor * 0.95)
        return has_model_floor or has_model_rarity or has_backdrop_rarity or has_floor_discount

    def _has_color_harmony(self, model_name: str | None, backdrop_name: str | None) -> bool:
        model_palette = self._palette_for_model(model_name)
        backdrop_palette = self._palette_for_backdrop(backdrop_name)
        if not model_palette or not backdrop_palette:
            return False
        return bool(model_palette & backdrop_palette)

    def _quality_rejection_reasons(self, listing: dict) -> list[str]:
        reasons: list[str] = []
        if not listing.get("image_url"):
            reasons.append("missing_image")
        if not listing.get("price"):
            reasons.append("missing_price")
        if is_blocked_collection_model(listing.get("collection_name"), listing.get("model_name")):
            reasons.append("blocked_model")
        if listing.get("price") and listing["price"] > self.mrkt.settings.mrkt_research_max_price:
            reasons.append("over_research_price")
        if has_collection_quality_rules(listing.get("collection_name")) and not has_collection_specific_quality(
            listing.get("collection_name"),
            listing.get("model_name"),
            listing.get("backdrop_name"),
        ):
            reasons.append("collection_quality_rule")
        gift_floor = listing.get("floor_price")
        if self.mrkt.settings.mrkt_min_gift_floor and (not gift_floor or gift_floor < self.mrkt.settings.mrkt_min_gift_floor):
            reasons.append("gift_floor_too_low")
        premium_backdrops = {name.lower() for name in self.mrkt.settings.premium_backdrop_list}
        backdrop = str(listing.get("backdrop_name") or "").lower()
        has_premium_backdrop = backdrop in premium_backdrops
        model_floor = listing.get("model_floor_price")
        model_rarity = listing.get("model_rarity")
        backdrop_rarity = listing.get("backdrop_rarity")
        has_expensive_model = bool(model_floor and model_floor >= self.mrkt.settings.mrkt_min_model_floor)
        has_rare_model = bool(model_rarity and model_rarity <= self.mrkt.settings.mrkt_max_model_rarity)
        has_rare_backdrop = bool(backdrop_rarity and backdrop_rarity <= self.mrkt.settings.mrkt_max_backdrop_rarity)
        has_harmony = self._has_color_harmony(listing.get("model_name"), listing.get("backdrop_name"))
        has_liquidity = self._has_liquidity_signal(listing, relaxed=True)
        if not has_harmony:
            reasons.append("no_color_harmony")
        if not has_liquidity:
            reasons.append("no_liquidity_signal")
        if not any([has_premium_backdrop, has_expensive_model, has_rare_model, has_rare_backdrop]):
            reasons.append("no_premium_or_rare_trait")
        return reasons or ["unknown"]

    def _debug_listing_summary(self, listing: dict) -> dict[str, Any]:
        return {
            "collection": listing.get("collection_name"),
            "number": listing.get("number"),
            "price": listing.get("price"),
            "model": listing.get("model_name"),
            "backdrop": listing.get("backdrop_name"),
            "model_floor": listing.get("model_floor_price"),
            "model_rarity": listing.get("model_rarity"),
            "backdrop_rarity": listing.get("backdrop_rarity"),
            "harmony": self._has_color_harmony(listing.get("model_name"), listing.get("backdrop_name")),
            "liquidity": self._has_liquidity_signal(listing, relaxed=True),
        }

    async def _enrich_model_sales(self, listing: dict, client: TelegramClient | None) -> None:
        sales = await self._model_recent_sales(
            listing.get("collection_name"),
            listing.get("model_name"),
            client,
        )
        listing["model_recent_sales"] = json.dumps(sales, ensure_ascii=False)
        listing["model_last_sale_at"] = sales[0]["date"] if sales else None

    async def _model_recent_sales(
        self,
        collection: str | None,
        model: str | None,
        client: TelegramClient | None,
    ) -> list[dict[str, Any]]:
        if not collection or not model or not client:
            return []
        key = (collection, model)
        if key in self._model_sales_cache:
            return self._model_sales_cache[key]
        try:
            gifts = await self.mrkt.saling(
                [collection],
                model_names=[model],
                count=MODEL_SALE_SAMPLE_SIZE,
                use_default_max_price=False,
            )
        except Exception:
            self._model_sales_cache[key] = []
            return []

        sales: list[dict[str, Any]] = []
        seen: set[str] = set()
        for gift in gifts:
            number = str(self._deep_pick(gift, "number", "giftNumber", "num", "gift_num", "giftNum") or "") or None
            gift_collection = self._deep_pick(gift, "collectionName", "collection", "collectionTitle", "giftName") or collection
            url = self._telegram_url_from_gift(gift, number) or self._telegram_url(gift_collection, number)
            slug = self._telegram_slug(url)
            if not number or not slug or slug in seen:
                continue
            seen.add(slug)
            try:
                value_info = await client(functions.payments.GetUniqueStarGiftValueInfoRequest(slug=slug))
            except Exception:
                continue
            date = self._iso_datetime(getattr(value_info, "last_sale_date", None))
            price = self._price(gift, "salePrice", "salePriceWithoutFee", "priceNano", "price", "tonPrice")
            if not date or price is None:
                continue
            sales.append({"number": number, "price": price, "platform": "MRKT", "date": date})

        sales.sort(key=lambda item: self._parse_datetime(item["date"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        self._model_sales_cache[key] = sales[:MODEL_RECENT_SALES_LIMIT]
        return self._model_sales_cache[key]

    def _has_recent_model_sale(self, listing: dict) -> bool:
        max_age_days = self.mrkt.settings.mrkt_model_sales_max_age_days
        if max_age_days <= 0:
            return True
        parsed = self._parse_datetime(listing.get("model_last_sale_at"))
        if not parsed:
            return False
        return parsed >= datetime.now(timezone.utc) - timedelta(days=max_age_days)

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
            pages = 0
            while pages < COMBO_MARKET_MAX_PAGES:
                pages += 1
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

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

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

    def _nano_price(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(value) / 1_000_000_000, 4)
        except (TypeError, ValueError):
            return None

    def _rarity_value(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(value) / 10, 2)
        except (TypeError, ValueError):
            return None

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

    def _telegram_url_from_gift(self, data: Any, number: str | None = None) -> str | None:
        raw = self._find_telegram_nft_url(data)
        if not raw:
            slug = self._find_telegram_nft_slug(data, number)
            return f"https://t.me/nft/{slug}" if slug else None
        match = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/nft/([A-Za-z0-9_-]+)", raw)
        return f"https://t.me/nft/{match.group(1)}" if match else None

    def _find_telegram_nft_url(self, data: Any) -> str | None:
        if isinstance(data, str):
            match = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/nft/[A-Za-z0-9_-]+", data)
            return match.group(0) if match else None
        if isinstance(data, dict):
            for value in data.values():
                found = self._find_telegram_nft_url(value)
                if found:
                    return found
        if isinstance(data, list):
            for item in data:
                found = self._find_telegram_nft_url(item)
                if found:
                    return found
        return None

    def _find_telegram_nft_slug(self, data: Any, number: str | None = None) -> str | None:
        expected_suffix = f"-{number}" if number else None
        if isinstance(data, str):
            match = re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", data.strip())
            if match and (not expected_suffix or match.group(0).endswith(expected_suffix)):
                return match.group(0)
            return None
        if isinstance(data, dict):
            for key in ("name", "slug", "giftSlug", "nftSlug", "telegramSlug"):
                found = self._find_telegram_nft_slug(data.get(key), number)
                if found:
                    return found
            for value in data.values():
                found = self._find_telegram_nft_slug(value, number)
                if found:
                    return found
        if isinstance(data, list):
            for item in data:
                found = self._find_telegram_nft_slug(item, number)
                if found:
                    return found
        return None

    def _fragment_image_url_from_url(self, telegram_url: str | None) -> str | None:
        slug = self._telegram_slug(telegram_url)
        return f"https://nft.fragment.com/gift/{slug.lower()}.webp" if slug else None

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

    def _palette_for_model(self, model_name: str | None) -> set[str]:
        normalized = self._normalize_name(model_name)
        if not normalized:
            return set()
        if normalized in MODEL_PALETTE_HINTS:
            return MODEL_PALETTE_HINTS[normalized]
        return self._palette_from_text(normalized)

    def _palette_for_backdrop(self, backdrop_name: str | None) -> set[str]:
        normalized = self._normalize_name(backdrop_name)
        if not normalized:
            return set()
        return self._palette_from_text(normalized)

    def _palette_from_text(self, text: str) -> set[str]:
        palette: set[str] = set()
        for family, keywords in COLOR_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                palette.add(family)
        if "grey" in text or "gray" in text or "steel" in text or "gunmetal" in text:
            palette.add("silver")
        if "dark" in text and "black" not in palette:
            palette.update({"black", "silver"})
        return palette

    def _normalize_name(self, value: str | None) -> str:
        return " ".join(str(value or "").strip().lower().split())

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
        return sorted(listings, key=lambda row: row.first_seen_at or row.updated_at or "", reverse=True)
