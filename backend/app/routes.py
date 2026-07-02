from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.catalog import default_collection_names, get_catalog
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
