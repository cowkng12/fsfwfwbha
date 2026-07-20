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
from app.database import database_storage_info
from app.repositories import ListingRepository, SearchPreferencesRepository, SubscriptionRepository
from app.schemas import (
    FilterRequest,
    Listing,
    ResultsResponse,
    SearchPreferences,
    SubscriptionInvoiceRequest,
    SubscriptionInvoiceResponse,
    SubscriptionStatus,
)
from app.services.research import DealAnalyzer, ResearchService
from app.services.telegram_bot import TelegramBotService, WHITELIST_DENIED_MESSAGE
from app.supabase_store import SupabaseStore

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


def search_preferences_repo() -> SearchPreferencesRepository:
    return SearchPreferencesRepository()


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


def require_telegram_user_id(x_telegram_init_data: str | None = Header(default=None)) -> int:
    settings = get_settings()
    user_id = telegram_init_data_user_id(x_telegram_init_data, settings.telegram_bot_token)
    if user_id is None:
        raise HTTPException(status_code=403, detail=WHITELIST_DENIED_MESSAGE)
    return user_id


def require_active_subscription(user_id: int = Depends(require_telegram_user_id)) -> int:
    if not SubscriptionRepository().get(user_id)["active"]:
        raise HTTPException(status_code=402, detail="Subscription required")
    return user_id


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
    return {"ok": True, "supabase_persistence": SupabaseStore().enabled}


@router.get("/catalog")
async def catalog(_: int = Depends(require_telegram_user_id), client=Depends(mrkt_client)):
    base_catalog = get_catalog()
    try:
        collections = normalize_gift_collections(await client.gift_collections())
    except Exception:
        collections = []
    if not collections:
        return base_catalog
    return {**base_catalog, "nfts": collections}


@router.get("/catalog/traits")
async def catalog_traits(
    collectionName: str = Query(..., min_length=1),
    _: int = Depends(require_telegram_user_id),
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


def normalize_gift_collections(items: list[dict]) -> list[dict]:
    collections: dict[str, dict] = {}
    for item in items:
        normalized = normalize_gift_collection(item)
        if normalized["name"]:
            collections.setdefault(normalized["name"].lower(), normalized)
    return sorted(collections.values(), key=lambda item: item["name"].lower())


def normalize_gift_collection(item: dict) -> dict:
    if not isinstance(item, dict):
        name = str(item or "").strip()
        return {"id": name, "name": name, "image": "", "floorPrice": None, "searchNames": [name] if name else []}
    name = str(
        item.get("title")
        or item.get("name")
        or item.get("collectionName")
        or item.get("collectionTitle")
        or item.get("giftName")
        or ""
    ).strip()
    identifier = str(item.get("id") or item.get("slug") or item.get("collectionId") or name)
    logo = (
        item.get("logo")
        or item.get("image")
        or item.get("thumbnail")
        or item.get("modelStickerThumbnailKey")
        or item.get("cover")
        or ""
    )
    return {
        "id": identifier,
        "name": name,
        "image": cdn_url(logo),
        "floorPrice": collection_floor_ton(item),
        "volume": collection_volume_ton(item),
        "searchNames": [name] if name else [],
    }


def cdn_url(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if text.startswith(("http://", "https://", "data:")):
        return text
    return f"https://cdn.tgmrkt.io/{text.lstrip('/')}"


def collection_floor_ton(item: dict) -> float | None:
    nano_value = item.get("floorPriceNanoTons") or item.get("floorPriceNanoTONs")
    if nano_value not in (None, ""):
        return nano_ton(nano_value)
    try:
        value = item.get("floorPrice")
        if value in (None, ""):
            return None
        numeric = float(value)
        return round(numeric / 1_000_000_000, 4) if numeric > 1_000_000 else round(numeric, 4)
    except (TypeError, ValueError):
        return None


def collection_volume_ton(item: dict) -> float | None:
    try:
        value = item.get("volume")
        if value in (None, ""):
            return None
        return round(float(value) / 1_000_000_000, 2)
    except (TypeError, ValueError):
        return None


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
    user_id: int = Depends(require_telegram_user_id),
    repo: ListingRepository = Depends(listing_repo),
):
    if not SubscriptionRepository().get(user_id)["active"]:
        return ResultsResponse(items=[], last_research_at=repo.last_research_at())
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
    try:
        listings = repo.find(filters, delivered_to_user_id=user_id)
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


@router.get("/subscription", response_model=SubscriptionStatus)
def subscription_status(user_id: int = Depends(require_telegram_user_id)):
    return SubscriptionRepository().get(user_id)


@router.get("/search-preferences", response_model=SearchPreferences)
def search_preferences(
    user_id: int = Depends(require_telegram_user_id),
    repo: SearchPreferencesRepository = Depends(search_preferences_repo),
):
    return repo.get(user_id)


@router.put("/search-preferences", response_model=SearchPreferences)
def save_search_preferences(
    request: SearchPreferences,
    user_id: int = Depends(require_active_subscription),
    repo: SearchPreferencesRepository = Depends(search_preferences_repo),
):
    return repo.save(user_id, request.model_dump(exclude={"updated_at"}))


@router.post("/subscription/invoice", response_model=SubscriptionInvoiceResponse)
async def subscription_invoice(
    request: SubscriptionInvoiceRequest,
    user_id: int = Depends(require_telegram_user_id),
    service: TelegramBotService = Depends(telegram_bot_service),
):
    repo = SubscriptionRepository()
    plan = repo.plan(request.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Unknown subscription plan")
    if not service.settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured")
    invoice_link = await service.create_subscription_invoice(user_id, plan)
    return {"invoice_link": invoice_link, "plan": plan}


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
    _: int = Depends(require_active_subscription),
    repo: ListingRepository = Depends(listing_repo),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to clear listings")
    deleted = repo.clear_feed(archive_current=True)
    return {"deleted": deleted, "archived": True}


@router.get("/debug/mrkt")
async def debug_mrkt(client=Depends(mrkt_client)):
    settings = client.settings
    targets = SearchPreferencesRepository().active_targets()
    result = {
        "has_telegram_api_id": bool(settings.telegram_api_id),
        "has_telegram_api_hash": bool(settings.telegram_api_hash),
        "has_telegram_session": bool(settings.telegram_session),
        "has_mrkt_auth_token": bool(settings.mrkt_auth_token),
        "mrkt_max_price": settings.mrkt_max_price,
        "mrkt_research_max_price": settings.mrkt_research_max_price,
        "mrkt_collections_per_run": settings.mrkt_collections_per_run,
        "mrkt_min_model_floor": settings.mrkt_min_model_floor,
        "mrkt_max_model_rarity": settings.mrkt_max_model_rarity,
        "mrkt_max_backdrop_rarity": settings.mrkt_max_backdrop_rarity,
        "mrkt_model_sales_max_age_days": settings.mrkt_model_sales_max_age_days,
        "active_search_targets": {
            "collection_count": len(targets["collection_names"]),
            "collection_sample": targets["collection_names"][:12],
            "min_price": targets["min_price"],
            "max_price": targets["max_price"],
        },
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
    try:
        collections = normalize_gift_collections(await client.gift_collections())
        result.update({
            "gift_collections_ok": True,
            "gift_collections_count": len(collections),
            "gift_collections_sample": [item["name"] for item in collections[:10]],
        })
    except Exception as exc:
        result.update({"gift_collections_ok": False, "gift_collections_error": str(exc)})
    return result


@router.get("/debug/tokens")
async def debug_tokens(
    refresh: bool = Query(default=False),
    client=Depends(mrkt_client),
    service: TelegramBotService = Depends(telegram_bot_service),
):
    result = {
        "telegram_bot": {
            "configured": bool(service.settings.telegram_bot_token),
            "alert_chat_configured": bool(service.settings.telegram_alert_chat_id),
            "env_granted_user_count": len(service.settings.telegram_granted_user_id_set),
        },
        "storage": {
            **database_storage_info(),
        },
        "mrkt": await client.token_diagnostics(refresh_auth=refresh),
    }
    try:
        me = await service.get_me()
        result["telegram_bot"].update({
            "ok": bool(me),
            "id": me.get("id"),
            "username": me.get("username"),
            "first_name": me.get("first_name"),
        })
    except Exception as exc:
        result["telegram_bot"].update({"ok": False, "error": str(exc)})
    return result


@router.post("/debug/clear-mrkt-token")
@router.get("/debug/clear-mrkt-token")
def debug_clear_mrkt_token(
    secret: str | None = Query(default=None),
    x_cron_secret: str | None = Header(default=None),
    client=Depends(mrkt_client),
):
    require_cron_secret(secret, x_cron_secret)
    return client.clear_token_cache()


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
