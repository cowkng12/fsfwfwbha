from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.catalog import blocked_collection_model_pairs, collection_quality_rules, default_collection_names
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
                    model_floor_price, sales_count, uses_count, uses_total,
                    combo_listed_count, combo_floor_price, model_last_sale_at,
                    model_recent_sales, current_owner,
                    original_sender, original_recipient, original_gift_at,
                    last_sale_at, last_sale_price, last_sale_currency,
                    initial_sale_at, initial_sale_price, initial_sale_currency,
                    initial_sale_stars, received_at, export_at, next_resale_at,
                    next_transfer_at, marketplace_url, telegram_url, first_seen_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    uses_count=excluded.uses_count,
                    uses_total=excluded.uses_total,
                    combo_listed_count=excluded.combo_listed_count,
                    combo_floor_price=excluded.combo_floor_price,
                    model_last_sale_at=excluded.model_last_sale_at,
                    model_recent_sales=excluded.model_recent_sales,
                    current_owner=excluded.current_owner,
                    original_sender=excluded.original_sender,
                    original_recipient=excluded.original_recipient,
                    original_gift_at=excluded.original_gift_at,
                    last_sale_at=excluded.last_sale_at,
                    last_sale_price=excluded.last_sale_price,
                    last_sale_currency=excluded.last_sale_currency,
                    initial_sale_at=excluded.initial_sale_at,
                    initial_sale_price=excluded.initial_sale_price,
                    initial_sale_currency=excluded.initial_sale_currency,
                    initial_sale_stars=excluded.initial_sale_stars,
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
                        row.get("sales_count"), row.get("uses_count"),
                        row.get("uses_total"), row.get("combo_listed_count"),
                        row.get("combo_floor_price"), row.get("model_last_sale_at"),
                        row.get("model_recent_sales"), row.get("current_owner"),
                        row.get("original_sender"), row.get("original_recipient"),
                        row.get("original_gift_at"), row.get("last_sale_at"),
                        row.get("last_sale_price"), row.get("last_sale_currency"),
                        row.get("initial_sale_at"), row.get("initial_sale_price"),
                        row.get("initial_sale_currency"), row.get("initial_sale_stars"),
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
        if filters.symbol_names:
            where.append(f"symbol_name IN ({','.join(['?'] * len(filters.symbol_names))})")
            params.extend(filters.symbol_names)
        if filters.number:
            where.append("number = ?")
            params.append(filters.number)
        if filters.min_price is not None:
            where.append("price >= ?")
            params.append(filters.min_price)
        max_price = get_settings().mrkt_max_price
        if filters.max_price is not None:
            max_price = min(max_price, filters.max_price)
        where.append("price <= ?")
        params.append(max_price)
        self._append_catalog_collection_filter(where, params)
        self._append_blocked_model_filter(where, params)
        self._append_collection_quality_filter(where, params)
        where.append("NOT EXISTS (SELECT 1 FROM hidden_items WHERE hidden_items.source = listings.source AND hidden_items.external_id = listings.external_id)")

        sql = "SELECT * FROM listings"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY first_seen_at DESC, updated_at DESC, price ASC LIMIT ?"
        params.append(filters.limit)

        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Listing(**dict(row), deal_score=0) for row in rows]

    def find_recent(self, limit: int = 80) -> list[Listing]:
        params: list[str | float | int] = [get_settings().mrkt_max_price]
        catalog_filter = self._catalog_collection_sql(params)
        blocked_filter = self._blocked_model_sql(params)
        quality_filter = self._collection_quality_sql(params)
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM listings
                WHERE image_url IS NOT NULL
                  AND price > 0
                  AND price <= ?
                  {catalog_filter}
                  {blocked_filter}
                  {quality_filter}
                  AND NOT EXISTS (
                    SELECT 1 FROM hidden_items
                    WHERE hidden_items.source = listings.source
                      AND hidden_items.external_id = listings.external_id
                  )
                ORDER BY first_seen_at DESC, updated_at DESC, price ASC
                LIMIT ?
                """.format(
                    catalog_filter=catalog_filter,
                    blocked_filter=blocked_filter,
                    quality_filter=quality_filter,
                ),
                (*params, limit),
            ).fetchall()
        return [Listing(**dict(row), deal_score=0) for row in rows]

    def find_unnotified(self, limit: int = 5, first_seen_after: str | None = None) -> list[Listing]:
        params: list[str | float | int] = [get_settings().mrkt_max_price]
        first_seen_filter = ""
        if first_seen_after:
            first_seen_filter = "AND first_seen_at >= ?"
            params.append(first_seen_after)
        catalog_filter = self._catalog_collection_sql(params)
        blocked_filter = self._blocked_model_sql(params)
        quality_filter = self._collection_quality_sql(params)
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM listings
                WHERE notified_at IS NULL
                  AND image_url IS NOT NULL
                  AND price > 0
                  AND price <= ?
                  {first_seen_filter}
                  {catalog_filter}
                  {blocked_filter}
                  {quality_filter}
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
                """.format(
                    first_seen_filter=first_seen_filter,
                    catalog_filter=catalog_filter,
                    blocked_filter=blocked_filter,
                    quality_filter=quality_filter,
                ),
                (*params, limit),
            ).fetchall()
        return [Listing(**dict(row), deal_score=0) for row in rows]

    def _append_blocked_model_filter(self, where: list[str], params: list[str | float | int]) -> None:
        blocked_filter = self._blocked_model_sql(params)
        if blocked_filter:
            where.append(blocked_filter.removeprefix("AND "))

    def _append_collection_quality_filter(self, where: list[str], params: list[str | float | int]) -> None:
        quality_filter = self._collection_quality_sql(params)
        if quality_filter:
            where.append(quality_filter.removeprefix("AND "))

    def _append_catalog_collection_filter(self, where: list[str], params: list[str | float | int]) -> None:
        catalog_filter = self._catalog_collection_sql(params)
        if catalog_filter:
            where.append(catalog_filter.removeprefix("AND "))

    def _catalog_collection_sql(self, params: list[str | float | int]) -> str:
        names = sorted({" ".join(name.strip().lower().split()) for name in default_collection_names() if name.strip()})
        if not names:
            return ""
        placeholders = ",".join("?" for _ in names)
        params.extend(names)
        return f"AND LOWER(TRIM(COALESCE(collection_name, ''))) IN ({placeholders})"

    def _model_liquidity_sql(self, params: list[str | float | int]) -> str:
        days = get_settings().mrkt_model_sales_max_age_days
        if days <= 0:
            return ""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        params.append(cutoff)
        return "AND (model_last_sale_at IS NULL OR model_last_sale_at >= ?)"

    def _blocked_model_sql(self, params: list[str | float | int]) -> str:
        blocked = blocked_collection_model_pairs()
        if not blocked:
            return ""
        clauses: list[str] = []
        for collection_name, model_name in sorted(blocked):
            clauses.append(
                "(LOWER(TRIM(COALESCE(collection_name, ''))) = ? "
                "AND LOWER(TRIM(COALESCE(model_name, ''))) = ?)"
            )
            params.extend([collection_name, model_name])
        return f"AND NOT ({' OR '.join(clauses)})"

    def _collection_quality_sql(self, params: list[str | float | int]) -> str:
        clauses: list[str] = []
        for collection_name, rule in collection_quality_rules().items():
            model_placeholders = ",".join("?" for _ in rule["models"])
            params.append(collection_name)
            params.extend(sorted(rule["models"]))
            if rule.get("require_model"):
                clauses.append(
                    "(LOWER(TRIM(COALESCE(collection_name, ''))) = ? "
                    f"AND LOWER(TRIM(COALESCE(model_name, ''))) NOT IN ({model_placeholders}))"
                )
                continue
            backdrop_placeholders = ",".join("?" for _ in rule["backdrops"])
            clauses.append(
                "(LOWER(TRIM(COALESCE(collection_name, ''))) = ? "
                f"AND NOT (LOWER(TRIM(COALESCE(model_name, ''))) IN ({model_placeholders}) "
                f"OR LOWER(TRIM(COALESCE(backdrop_name, ''))) IN ({backdrop_placeholders})))"
            )
            params.extend(sorted(rule["backdrops"]))
        return f"AND NOT ({' OR '.join(clauses)})" if clauses else ""

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

    def reset_alert_state(self) -> dict[str, int]:
        with connect() as conn:
            listing_count = conn.execute("SELECT COUNT(*) AS count FROM listings").fetchone()
            notified_count = conn.execute("SELECT COUNT(*) AS count FROM notified_items").fetchone()
            hidden_count = conn.execute("SELECT COUNT(*) AS count FROM hidden_items").fetchone()
            conn.execute("DELETE FROM listings")
            conn.execute("DELETE FROM notified_items")
            conn.execute("DELETE FROM hidden_items")
        return {
            "deleted_listings": int(listing_count["count"] if listing_count else 0),
            "deleted_notified": int(notified_count["count"] if notified_count else 0),
            "deleted_hidden": int(hidden_count["count"] if hidden_count else 0),
        }

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
