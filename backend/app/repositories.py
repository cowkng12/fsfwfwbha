from datetime import datetime, timezone
from typing import Iterable

from app.database import connect
from app.schemas import FilterRequest, Listing


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ListingRepository:
    def upsert_many(self, listings: Iterable[dict]) -> int:
        rows = list(listings)
        if not rows:
            return 0
        with connect() as conn:
            conn.executemany(
                """
                INSERT INTO listings (
                    source, external_id, collection_name, name, number, model_name,
                    backdrop_name, symbol_name, image_url, price, marketplace_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    collection_name=excluded.collection_name,
                    name=excluded.name,
                    number=excluded.number,
                    model_name=excluded.model_name,
                    backdrop_name=excluded.backdrop_name,
                    symbol_name=excluded.symbol_name,
                    image_url=excluded.image_url,
                    price=excluded.price,
                    marketplace_url=excluded.marketplace_url,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row["source"], row["external_id"], row["collection_name"], row["name"],
                        row.get("number"), row.get("model_name"), row.get("backdrop_name"),
                        row.get("symbol_name"), row.get("image_url"), row["price"],
                        row.get("marketplace_url"), row["updated_at"],
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def find(self, filters: FilterRequest) -> list[Listing]:
        where = []
        params: list[str | float | int] = []
        if filters.collection_names:
            where.append(f"collection_name IN ({','.join(['?'] * len(filters.collection_names))})")
            params.extend(filters.collection_names)
        if filters.backdrop_names:
            where.append(f"backdrop_name IN ({','.join(['?'] * len(filters.backdrop_names))})")
            params.extend(filters.backdrop_names)
        if filters.model_names:
            where.append(f"model_name IN ({','.join(['?'] * len(filters.model_names))})")
            params.extend(filters.model_names)

        sql = "SELECT * FROM listings"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY collection_name ASC, price ASC LIMIT ?"
        params.append(filters.limit)

        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Listing(**dict(row), floor_price=None, deal_score=0) for row in rows]

    def last_research_at(self) -> str | None:
        with connect() as conn:
            row = conn.execute("SELECT created_at FROM research_runs ORDER BY id DESC LIMIT 1").fetchone()
        return row["created_at"] if row else None


class ResearchRunRepository:
    def add(self, source: str, status: str, message: str | None = None) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO research_runs (source, status, message, created_at) VALUES (?, ?, ?, ?)",
                (source, status, message, utc_now()),
            )
