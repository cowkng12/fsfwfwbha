import asyncio
import hashlib
import hmac
import json
import random
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from app.catalog import (
    default_collection_names,
    get_catalog,
    has_collection_quality_rules,
    is_blocked_collection_model,
    is_priority_collection_backdrop,
    is_priority_collection_model,
)
from app.config import get_settings
from app.repositories import ListingRepository
from app.schemas import FilterRequest, Listing, ResultsResponse
from app.services.research import DealAnalyzer, ResearchService
from app.services.telegram_bot import TelegramBotService, WHITELIST_DENIED_MESSAGE

router = APIRouter(prefix="/api")


def parse_multi(value: list[str] | None) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    for part in value:
        items.extend(item.strip() for item in part.split(",") if item.strip())
    return items


def listing_repo() -> ListingRepository:
    return ListingRepository()


def research_service() -> ResearchService:
    from app.main import research

    return research


def mrkt_client():
    from app.main import research

    return research.mrkt


def telegram_bot_service() -> TelegramBotService:
    from app.main import telegram_bot

    return telegram_bot


def require_allowed_telegram_user(x_telegram_init_data: str | None = Header(default=None)) -> None:
    settings = get_settings()
    allowed_user_ids = settings.telegram_allowed_user_id_set | settings.telegram_allowed_chat_id_set
    if not allowed_user_ids:
        return
    user_id = telegram_init_data_user_id(x_telegram_init_data, settings.telegram_bot_token)
    if user_id not in allowed_user_ids:
        raise HTTPException(status_code=403, detail=WHITELIST_DENIED_MESSAGE)


def telegram_init_data_user_id(init_data: str | None, bot_token: str | None) -> int | None:
    if not init_data or not bot_token:
        raise HTTPException(status_code=403, detail=WHITELIST_DENIED_MESSAGE)
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=403, detail=WHITELIST_DENIED_MESSAGE)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=403, detail=WHITELIST_DENIED_MESSAGE)
    try:
        user = json.loads(pairs.get("user") or "{}")
        return int(user.get("id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=403, detail=WHITELIST_DENIED_MESSAGE) from None


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/catalog")
def catalog(_: None = Depends(require_allowed_telegram_user)):
    return get_catalog()


@router.get("/catalog/traits")
async def catalog_traits(
    collectionName: str = Query(..., min_length=1),
    _: None = Depends(require_allowed_telegram_user),
    client=Depends(mrkt_client),
):
    collection_names = collection_search_names(collectionName)
    try:
        models, backdrops, symbols = await asyncio.gather(
            client.gift_trait_options("models", collection_names),
            client.gift_trait_options("backdrops", collection_names),
            client.gift_trait_options("symbols", collection_names),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    normalized_models = [
        item
        for item in (normalize_model(item) for item in models)
        if not is_blocked_collection_model(collectionName, item["name"])
    ]
    normalized_backdrops = [normalize_backdrop(item) for item in backdrops]
    if has_collection_quality_rules(collectionName):
        normalized_models = [
            item for item in normalized_models
            if is_priority_collection_model(collectionName, item["name"])
        ]
        normalized_backdrops = [
            item for item in normalized_backdrops
            if is_priority_collection_backdrop(collectionName, item["name"])
        ]
    return {
        "models": sorted(normalized_models, key=trait_sort_key),
        "backdrops": sorted(normalized_backdrops, key=trait_sort_key),
        "symbols": sorted((normalize_symbol(item) for item in symbols), key=trait_sort_key),
    }


def collection_search_names(collection_name: str) -> list[str]:
    for item in get_catalog()["nfts"]:
        if item.get("name") == collection_name:
            return item.get("searchNames") or [collection_name]
    return [collection_name]


def normalize_model(item: dict) -> dict:
    image_key = item.get("modelStickerThumbnailKey")
    return {
        "name": item.get("modelTitle") or item.get("modelName") or "",
        "image": f"https://cdn.tgmrkt.io/{image_key}" if image_key else "",
        "rarity": rarity_percent(item.get("rarityPerMille")),
        "floorPrice": nano_ton(item.get("floorPriceNanoTons")),
    }


def normalize_backdrop(item: dict) -> dict:
    name = item.get("backdropName") or ""
    return {
        "name": name,
        "color": int_color(item.get("colorsCenterColor")) or "#2b2c2b",
        "rarity": rarity_percent(item.get("rarityPerMille")) or static_backdrop_rarity(name),
    }


def normalize_symbol(item: dict) -> dict:
    return {
        "name": item.get("symbolName") or "",
        "rarity": rarity_percent(item.get("rarityPerMille")),
    }


def rarity_percent(value) -> float:
    try:
        return round(float(value) / 10, 2)
    except (TypeError, ValueError):
        return 0


def nano_ton(value) -> float | None:
    try:
        return round(float(value) / 1_000_000_000, 4) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def int_color(value) -> str | None:
    try:
        return f"#{int(value):06x}"
    except (TypeError, ValueError):
        return None


def static_backdrop_rarity(name: str) -> float:
    for item in get_catalog()["backdrops"]:
        if item.get("name") == name:
            return float(item.get("rarity") or 0)
    return 0


def trait_sort_key(item: dict) -> tuple[float, str]:
    rarity = item.get("rarity") or 0
    return (rarity if rarity > 0 else 999, item.get("name") or "")


@router.get("/results", response_model=ResultsResponse)
def results(
    collectionNames: list[str] | None = Query(default=None),
    backdropNames: list[str] | None = Query(default=None),
    modelNames: list[str] | None = Query(default=None),
    symbolNames: list[str] | None = Query(default=None),
    number: str | None = Query(default=None),
    minPrice: float | None = Query(default=None, ge=0),
    maxPrice: float | None = Query(default=None, ge=0),
    limit: int = Query(default=60, ge=1, le=200),
    _: None = Depends(require_allowed_telegram_user),
    repo: ListingRepository = Depends(listing_repo),
):
    collection_names = parse_multi(collectionNames)
    backdrop_names = parse_multi(backdropNames)
    model_names = parse_multi(modelNames)
    symbol_names = parse_multi(symbolNames)
    normalized_number = number.strip() if number and number.strip() else None
    filters = FilterRequest(
        collection_names=collection_names or default_collection_names(),
        backdrop_names=backdrop_names,
        model_names=model_names,
        symbol_names=symbol_names,
        number=normalized_number,
        min_price=minPrice,
        max_price=maxPrice,
        limit=limit,
    )
    has_filters = any([collection_names, backdrop_names, model_names, symbol_names, normalized_number, minPrice, maxPrice])
    try:
        listings = repo.find(filters) if has_filters else repo.find_recent(limit)
        items = DealAnalyzer().apply_scores(listings)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Cannot load results") from exc
    items = [item for item in items if item.source != "test" and item.image_url and "picsum.photos" not in item.image_url]
    if not any(item.image_url for item in items):
        items = []
    try:
        last_research_at = repo.last_research_at()
    except Exception:
        last_research_at = None
    return ResultsResponse(items=items, last_research_at=last_research_at)


@router.post("/research/run")
async def run_research(service: ResearchService = Depends(research_service)):
    count = await service.run()
    return {"stored": count}


def require_cron_secret(secret: str | None, x_cron_secret: str | None) -> None:
    expected = get_settings().cron_secret
    if not expected:
        raise HTTPException(status_code=503, detail="CRON_SECRET is not configured")
    if secret != expected and x_cron_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


@router.get("/cron/research")
@router.post("/cron/research")
async def cron_research(
    background_tasks: BackgroundTasks,
    secret: str | None = Query(default=None),
    wait: bool = Query(default=False),
    x_cron_secret: str | None = Header(default=None),
):
    require_cron_secret(secret, x_cron_secret)
    from app.main import run_research_cycle

    if wait:
        return await run_research_cycle()
    background_tasks.add_task(run_research_cycle)
    return {"queued": True}


@router.post("/listings/clear")
def clear_listings(
    confirm: bool = Query(default=False),
    _: None = Depends(require_allowed_telegram_user),
    repo: ListingRepository = Depends(listing_repo),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to clear listings")
    deleted = repo.clear_feed(archive_current=True)
    return {"deleted": deleted, "archived": True}


@router.get("/debug/mrkt")
async def debug_mrkt(client=Depends(mrkt_client)):
    settings = client.settings
    result = {
        "has_telegram_api_id": bool(settings.telegram_api_id),
        "has_telegram_api_hash": bool(settings.telegram_api_hash),
        "has_telegram_session": bool(settings.telegram_session),
        "has_mrkt_auth_token": bool(settings.mrkt_auth_token),
        "mrkt_max_price": settings.mrkt_max_price,
        "mrkt_research_max_price": settings.mrkt_research_max_price,
        "mrkt_min_model_floor": settings.mrkt_min_model_floor,
        "mrkt_max_model_rarity": settings.mrkt_max_model_rarity,
        "mrkt_max_backdrop_rarity": settings.mrkt_max_backdrop_rarity,
        "mrkt_model_sales_max_age_days": settings.mrkt_model_sales_max_age_days,
    }
    try:
        token = await client.token()
        debug_items = []
        first_gift = None
        for collection in default_collection_names():
            gifts = await client.saling([collection], count=3, max_price=settings.mrkt_research_max_price)
            debug_items.append({"collection": collection, "gift_count": len(gifts)})
            if gifts and first_gift is None:
                first_gift = gifts[0]
        result.update({
            "token_ok": bool(token),
            "gift_count": sum(item["gift_count"] for item in debug_items),
            "collections": debug_items,
            "first_gift_keys": list(first_gift.keys()) if first_gift else [],
            "first_gift": first_gift,
        })
    except Exception as exc:
        result.update({"token_ok": False, "error": str(exc)})
    return result


@router.get("/debug/test-alert")
@router.post("/debug/test-alert")
async def debug_test_alert(
    secret: str | None = Query(default=None),
    x_cron_secret: str | None = Header(default=None),
    service: TelegramBotService = Depends(telegram_bot_service),
):
    require_cron_secret(secret, x_cron_secret)
    if not service.settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured")
    if not service.settings.telegram_alert_chat_id:
        raise HTTPException(status_code=503, detail="TELEGRAM_ALERT_CHAT_ID is not configured")

    listing = random_test_listing()
    await service.send_listing_alert(listing)
    return {
        "sent": True,
        "collection_name": listing.collection_name,
        "number": listing.number,
        "price": listing.price,
        "model_name": listing.model_name,
        "backdrop_name": listing.backdrop_name,
    }


@router.post("/debug/reset-alert-state")
@router.get("/debug/reset-alert-state")
def debug_reset_alert_state(
    secret: str | None = Query(default=None),
    x_cron_secret: str | None = Header(default=None),
    repo: ListingRepository = Depends(listing_repo),
):
    require_cron_secret(secret, x_cron_secret)
    return {"reset": True, **repo.reset_alert_state()}


def random_test_listing() -> Listing:
    now = datetime.now(timezone.utc).isoformat()
    samples = [
        ("Vice Cream", "Vanilla Brick", "Pacific Green", "Musical Note"),
        ("Instant Ramen", "Chrome", "Silver", "Sparkle"),
        ("Pool Float", "Orbit", "Azure Blue", "Wave"),
        ("Money Pot", "Goldsmith", "Pure Gold", "Coin"),
        ("Victory Medal", "Halo", "Gold", "Star"),
        ("Restless Jar", "Prism", "Electric Indigo", "Lightning"),
        ("Evil Eye", "Neo Matrix", "Onyx Black", "Eye"),
    ]
    collection, model, backdrop, symbol = random.choice(samples)
    number = str(random.randint(100000, 999999))
    price = round(random.uniform(2.2, 9.8), 2)
    external_id = hashlib.sha1(f"test-alert:{collection}:{number}:{now}".encode()).hexdigest()
    slug = "".join(part for part in collection.title() if part.isalnum())
    return Listing(
        source="test-alert",
        external_id=external_id,
        collection_name=collection,
        name=f"{collection} #{number}",
        number=number,
        model_name=model,
        backdrop_name=backdrop,
        symbol_name=symbol,
        image_url=f"https://nft.fragment.com/gift/{slug.lower()}-{number}.webp",
        price=price,
        floor_price=round(price * random.uniform(1.08, 1.4), 2),
        model_floor_price=round(max(8, price * random.uniform(1.2, 2.8)), 2),
        sales_count=random.randint(1, 12),
        uses_count=random.randint(1000, 6000),
        uses_total=random.randint(10000, 100000),
        combo_listed_count=random.randint(1, 8),
        combo_floor_price=round(price * random.uniform(1.05, 1.6), 2),
        model_last_sale_at=now,
        model_recent_sales=json.dumps(
            [{"number": str(random.randint(100000, 999999)), "price": price, "platform": "TEST", "date": now}],
            ensure_ascii=False,
        ),
        current_owner="@test_owner",
        marketplace_url="https://t.me/mrkt/app",
        telegram_url=f"https://t.me/nft/{slug}-{number}",
        first_seen_at=now,
        updated_at=now,
    )


@router.get("/debug/research-quality")
async def debug_research_quality(
    secret: str | None = Query(default=None),
    limit: int = Query(default=8, ge=1, le=20),
    x_cron_secret: str | None = Header(default=None),
    service: ResearchService = Depends(research_service),
):
    require_cron_secret(secret, x_cron_secret)
    return await service.debug_candidate_quality(sample_size=limit)


@router.post("/telegram/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    service: TelegramBotService = Depends(telegram_bot_service),
):
    expected = service.settings.telegram_webhook_secret
    if expected and x_telegram_bot_api_secret_token != expected:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    await service.handle_update(update)
    return {"ok": True}


@router.post("/telegram/set-webhook")
async def set_telegram_webhook(service: TelegramBotService = Depends(telegram_bot_service)):
    return await service.set_webhook()
