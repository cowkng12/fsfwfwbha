from datetime import datetime, timezone
from typing import Iterable

from app.config import get_settings
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
            hidden = {
                (row["source"], row["external_id"])
                for row in conn.execute("SELECT source, external_id FROM hidden_items").fetchall()
            }
            rows = [row for row in rows if (row["source"], row["external_id"]) not in hidden]
            if not rows:
                return 0
            conn.executemany(
                """
                INSERT INTO listings (
                    source, external_id, collection_name, name, number, model_name,
                    backdrop_name, symbol_name, image_url, price, floor_price,
                    model_floor_price, sales_count, current_owner, received_at, export_at,
                    next_resale_at, next_transfer_at, marketplace_url, telegram_url,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    collection_name=excluded.collection_name,
                    name=excluded.name,
                    number=excluded.number,
                    model_name=excluded.model_name,
                    backdrop_name=excluded.backdrop_name,
                    symbol_name=excluded.symbol_name,
                    image_url=excluded.image_url,
                    price=excluded.price,
                    floor_price=excluded.floor_price,
                    model_floor_price=excluded.model_floor_price,
                    sales_count=excluded.sales_count,
                    current_owner=excluded.current_owner,
                    received_at=excluded.received_at,
                    export_at=excluded.export_at,
                    next_resale_at=excluded.next_resale_at,
                    next_transfer_at=excluded.next_transfer_at,
                    marketplace_url=excluded.marketplace_url,
                    telegram_url=excluded.telegram_url,
                    first_seen_at=COALESCE(listings.first_seen_at, excluded.first_seen_at),
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row["source"], row["external_id"], row["collection_name"], row["name"],
                        row.get("number"), row.get("model_name"), row.get("backdrop_name"),
                        row.get("symbol_name"), row.get("image_url"), row["price"],
                        row.get("floor_price"), row.get("model_floor_price"),
                        row.get("sales_count"), row.get("current_owner"),
                        row.get("received_at"), row.get("export_at"),
                        row.get("next_resale_at"), row.get("next_transfer_at"),
                        row.get("marketplace_url"), row.get("telegram_url"),
                        row.get("first_seen_at"), row["updated_at"],
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
        where.append("price <= ?")
        params.append(get_settings().mrkt_max_price)
        where.append("NOT EXISTS (SELECT 1 FROM hidden_items WHERE hidden_items.source = listings.source AND hidden_items.external_id = listings.external_id)")

        sql = "SELECT * FROM listings"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY first_seen_at DESC, updated_at DESC, price ASC LIMIT ?"
        params.append(filters.limit)

        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Listing(**dict(row), deal_score=0) for row in rows]

    def find_unnotified(self, limit: int = 5, first_seen_after: str | None = None) -> list[Listing]:
        params: list[str | int] = []
        first_seen_filter = ""
        if first_seen_after:
            first_seen_filter = "AND first_seen_at >= ?"
            params.append(first_seen_after)
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM listings
                WHERE notified_at IS NULL
                  AND image_url IS NOT NULL
                  AND price > 0
                  {first_seen_filter}
                  AND NOT EXISTS (
                    SELECT 1 FROM notified_items
                    WHERE notified_items.source = listings.source
                      AND notified_items.external_id = listings.external_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM hidden_items
                    WHERE hidden_items.source = listings.source
                      AND hidden_items.external_id = listings.external_id
                  )
                ORDER BY first_seen_at DESC, updated_at DESC
                LIMIT ?
                """.format(first_seen_filter=first_seen_filter),
                (*params, limit),
            ).fetchall()
        return [Listing(**dict(row), deal_score=0) for row in rows]

    def mark_notified(self, source: str, external_id: str) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO notified_items (source, external_id, created_at) VALUES (?, ?, ?)",
                (source, external_id, utc_now()),
            )
            conn.execute(
                "UPDATE listings SET notified_at = ? WHERE source = ? AND external_id = ?",
                (utc_now(), source, external_id),
            )

    def mark_alert_baseline(self, first_seen_before: str | None = None) -> int:
        now = utc_now()
        where = "WHERE notified_at IS NULL"
        params: list[str] = []
        if first_seen_before:
            where += " AND (first_seen_at IS NULL OR first_seen_at < ?)"
            params.append(first_seen_before)
        with connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM listings {where}", params).fetchone()
            conn.execute(
                f"""
                INSERT OR IGNORE INTO notified_items (source, external_id, created_at)
                SELECT source, external_id, ? FROM listings {where}
                """,
                (now, *params),
            )
            conn.execute(f"UPDATE listings SET notified_at = COALESCE(notified_at, ?) {where}", (now, *params))
        return int(row["count"] if row else 0)

    def clear_feed(self, archive_current: bool = True) -> int:
        with connect() as conn:
            if archive_current:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO notified_items (source, external_id, created_at)
                    SELECT source, external_id, ? FROM listings
                    """,
                    (utc_now(),),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO hidden_items (source, external_id, created_at)
                    SELECT source, external_id, ? FROM listings
                    """,
                    (utc_now(),),
                )
            row = conn.execute("SELECT COUNT(*) AS count FROM listings").fetchone()
            conn.execute("DELETE FROM listings")
        return int(row["count"] if row else 0)

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
