import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import get_settings
from app.database import init_db
from app.repositories import ListingRepository, ResearchRunRepository, utc_now
from app.routes import router
from app.services.mrkt_client import MrktClient
from app.services.research import ResearchService
from app.services.telegram_bot import TelegramBotService

settings = get_settings()
research = ResearchService(MrktClient(settings), ListingRepository(), ResearchRunRepository())
telegram_bot = TelegramBotService(settings)
scheduler = AsyncIOScheduler()
alerts_ready = False


async def research_job() -> None:
    global alerts_ready
    started_at = utc_now()
    await research.run()
    repo = ListingRepository()
    if not alerts_ready:
        repo.mark_alert_baseline()
        alerts_ready = True
        return
    await telegram_bot.send_new_listing_alerts(repo, first_seen_after=started_at)

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
        return FileResponse(index)
    return {"ok": True, "message": "Frontend build is not available. Run npm run build."}


@app.on_event("startup")
async def startup() -> None:
    init_db()
    await telegram_bot.set_webhook()
    asyncio.create_task(research_job())
    scheduler.add_job(research_job, "interval", seconds=settings.research_interval_seconds, id="mrkt-research", max_instances=1)
    scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)
