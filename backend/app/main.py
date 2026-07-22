import asyncio
import logging

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import get_settings
from app.database import init_db
from app.repositories import ListingRepository, ResearchRunRepository, SearchPreferencesRepository, utc_now
from app.routes import router
from app.services.mrkt_client import MrktAuthError, MrktClient
from app.services.research import ResearchService
from app.services.telegram_bot import TelegramBotService

settings = get_settings()
logger = logging.getLogger(__name__)
research = ResearchService(MrktClient(settings), ListingRepository(), ResearchRunRepository())
telegram_bot = TelegramBotService(settings)
scheduler = AsyncIOScheduler()
alerts_ready = False
cycle_lock = asyncio.Lock()
last_mrkt_auth_alert_until: str | None = None


async def run_research_cycle() -> dict[str, int | bool | str | None]:
    global alerts_ready, last_mrkt_auth_alert_until
    async with cycle_lock:
        started_at = utc_now()
        repo = ListingRepository()
        baseline_count = 0
        if not alerts_ready:
            baseline_count = repo.mark_alert_baseline(first_seen_before=started_at)
            alerts_ready = True
        pruned_count = repo.prune_stale_listings(settings.listings_retention_hours)
        targets = SearchPreferencesRepository().active_targets()
        try:
            stored = await research.run(
                collection_names=targets["collection_names"],
                min_price=targets["min_price"],
                max_price=targets["max_price"],
            )
        except MrktAuthError as exc:
            cooldown_until = exc.cooldown_until.isoformat() if exc.cooldown_until else None
            ResearchRunRepository().add("mrkt", "paused", f"auth cooldown: {exc}")
            if cooldown_until != last_mrkt_auth_alert_until:
                last_mrkt_auth_alert_until = cooldown_until
                try:
                    await telegram_bot.send_system_alert(
                        "FloorHunt MRKT paused",
                        (
                            "MRKT rejected the auth token/session, so research was stopped "
                            f"until {cooldown_until or 'manual restart'}. Check /api/debug/tokens before running cron again."
                        ),
                    )
                except Exception as alert_exc:
                    logger.warning("MRKT auth alert failed: %s", alert_exc)
            return {
                "stored": 0,
                "sent": 0,
                "pruned": pruned_count,
                "baseline": baseline_count,
                "alerts_ready": alerts_ready,
                "paused": True,
                "reason": "mrkt_auth_cooldown",
                "cooldown_until": cooldown_until,
            }
        sent = await telegram_bot.send_new_listing_alerts(
            repo,
            first_seen_after=started_at,
            collection_names=targets["collection_names"],
            min_price=targets["min_price"],
            max_price=targets["max_price"],
        )
        return {"stored": stored, "sent": sent, "pruned": pruned_count, "baseline": baseline_count, "alerts_ready": alerts_ready}


async def research_job() -> None:
    await run_research_cycle()


async def keepalive_job() -> None:
    if not settings.public_base_url:
        return
    url = f"{settings.public_base_url.rstrip('/')}/api/health"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.get(url)
    except Exception as exc:
        logger.info("Keepalive ping failed: %s", exc)

app = FastAPI(title="Telegram NFT Research API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

frontend_dist = Path(__file__).parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")


@app.get("/{full_path:path}")
def frontend(full_path: str):
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return {"ok": True, "message": "Frontend build is not available. Run npm run build."}


@app.on_event("startup")
async def startup() -> None:
    init_db()
    await telegram_bot.set_webhook()
    asyncio.create_task(research_job())
    scheduler.add_job(research_job, "interval", seconds=settings.research_interval_seconds, id="mrkt-research", max_instances=1)
    if settings.keepalive_interval_seconds > 0 and settings.public_base_url:
        scheduler.add_job(keepalive_job, "interval", seconds=settings.keepalive_interval_seconds, id="render-keepalive", max_instances=1)
    scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)
