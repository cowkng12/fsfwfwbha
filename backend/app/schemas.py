from pydantic import BaseModel, Field


class FilterRequest(BaseModel):
    collection_names: list[str] = Field(default_factory=list)
    backdrop_names: list[str] = Field(default_factory=list)
    model_names: list[str] = Field(default_factory=list)
    limit: int = Field(default=60, ge=1, le=200)


class Listing(BaseModel):
    source: str
    external_id: str
    collection_name: str
    name: str
    number: str | None = None
    model_name: str | None = None
    backdrop_name: str | None = None
    symbol_name: str | None = None
    image_url: str | None = None
    price: float
    floor_price: float | None = None
    model_floor_price: float | None = None
    sales_count: int | None = None
    deal_score: float = 0
    marketplace_url: str | None = None
    telegram_url: str | None = None
    first_seen_at: str | None = None
    notified_at: str | None = None
    updated_at: str


class ResultsResponse(BaseModel):
    items: list[Listing]
    last_research_at: str | None
