from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from app.catalog import default_collection_names, get_catalog
from app.config import get_settings
from app.repositories import ListingRepository
from app.schemas import FilterRequest, ResultsResponse
from app.services.research import DealAnalyzer, ResearchService
from app.services.telegram_bot import TelegramBotService

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


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/catalog")
def catalog():
    return get_catalog()


@router.get("/results", response_model=ResultsResponse)
def results(
    collectionNames: list[str] | None = Query(default=None),
    backdropNames: list[str] | None = Query(default=None),
    modelNames: list[str] | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=200),
    repo: ListingRepository = Depends(listing_repo),
):
    filters = FilterRequest(
        collection_names=parse_multi(collectionNames) or default_collection_names(),
        backdrop_names=parse_multi(backdropNames),
        model_names=parse_multi(modelNames),
        limit=limit,
    )
    try:
        items = DealAnalyzer().apply_scores(repo.find(filters))
    except Exception:
        items = []
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
def clear_listings(confirm: bool = Query(default=False), repo: ListingRepository = Depends(listing_repo)):
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
    }
    try:
        token = await client.token()
        debug_items = []
        first_gift = None
        for collection in default_collection_names():
            gifts = await client.saling([collection], count=3, max_price=settings.mrkt_max_price)
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
