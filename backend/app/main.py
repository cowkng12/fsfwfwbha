from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.repositories import ListingRepository, ResearchRunRepository
from app.routes import router
from app.services.mrkt_client import MrktClient
from app.services.research import ResearchService

settings = get_settings()
research = ResearchService(MrktClient(settings), ListingRepository(), ResearchRunRepository())
scheduler = AsyncIOScheduler()

app = FastAPI(title="Telegram NFT Research API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    scheduler.add_job(research.run, "interval", seconds=settings.research_interval_seconds, id="mrkt-research", max_instances=1)
    scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)
