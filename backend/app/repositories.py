import json
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.catalog import blocked_collection_model_pairs, canonical_collection_name, collection_quality_rules, default_collection_names
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
        max_price = filters.max_price if filters.max_price is not None else get_settings().mrkt_max_price
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

    def find_unnotified(
        self,
        limit: int = 5,
        first_seen_after: str | None = None,
        collection_names: list[str] | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> list[Listing]:
        params: list[str | float | int] = [max_price or get_settings().mrkt_max_price]
        first_seen_filter = ""
        if first_seen_after:
            first_seen_filter = "AND (first_seen_at >= ? OR updated_at >= ?)"
            params.extend([first_seen_after, first_seen_after])
        min_price_filter = ""
        if min_price is not None:
            min_price_filter = "AND price >= ?"
            params.append(min_price)
        catalog_filter = self._selected_collection_sql(collection_names, params) if collection_names else self._catalog_collection_sql(params)
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
                  {min_price_filter}
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
                    min_price_filter=min_price_filter,
                    catalog_filter=catalog_filter,
                    blocked_filter=blocked_filter,
                    quality_filter=quality_filter,
                ),
                (*params, limit),
            ).fetchall()
        return [Listing(**dict(row), deal_score=0) for row in rows]

    def _selected_collection_sql(self, collection_names: list[str] | None, params: list[str | float | int]) -> str:
        names = [name.strip() for name in (collection_names or []) if name.strip()]
        if not names:
            return ""
        placeholders = ",".join("?" for _ in names)
        params.extend(names)
        return f"AND collection_name IN ({placeholders})"

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

    def prune_stale_listings(self, retention_hours: int) -> int:
        if retention_hours <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()
        with connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM listings WHERE updated_at < ?",
                (cutoff,),
            ).fetchone()
            conn.execute("DELETE FROM listings WHERE updated_at < ?", (cutoff,))
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


class SearchPreferencesRepository:
    DEFAULT_MAX_PRICE = 10.0

    def get(self, user_id: int | str) -> dict:
        with connect() as conn:
            row = conn.execute("SELECT * FROM search_preferences WHERE user_id = ?", (str(user_id),)).fetchone()
        if not row:
            return self._default()
        try:
            filters = json.loads(row["filters_json"])
        except json.JSONDecodeError:
            filters = {}
        return self._normalize(filters, updated_at=row["updated_at"])

    def save(self, user_id: int | str, filters: dict) -> dict:
        normalized = self._normalize(filters)
        now = utc_now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO search_preferences (user_id, filters_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    filters_json=excluded.filters_json,
                    updated_at=excluded.updated_at
                """,
                (str(user_id), json.dumps(normalized, ensure_ascii=False), now),
            )
        normalized["updated_at"] = now
        return normalized

    def active_targets(self) -> dict:
        preferences = self.active_recipient_preferences()
        if not preferences:
            return {"collection_names": default_collection_names(), "min_price": None, "max_price": get_settings().mrkt_research_max_price}
        collection_names: set[str] = set()
        min_prices: list[float] = []
        max_prices: list[float] = []
        for item in preferences:
            collection_names.update(item["collection_names"])
            if item["min_price"] is not None:
                min_prices.append(float(item["min_price"]))
            if item["max_price"] is not None:
                max_prices.append(float(item["max_price"]))
        return {
            "collection_names": sorted(collection_names) or default_collection_names(),
            "min_price": min(min_prices) if min_prices else None,
            "max_price": max(max_prices) if max_prices else get_settings().mrkt_research_max_price,
        }

    def active_recipient_preferences(self) -> list[dict]:
        recipients = SubscriptionRepository().active_recipient_ids()
        if not recipients:
            return []
        placeholders = ",".join("?" for _ in recipients)
        with connect() as conn:
            rows = conn.execute(
                f"SELECT user_id, filters_json, updated_at FROM search_preferences WHERE user_id IN ({placeholders})",
                tuple(recipients),
            ).fetchall()
        saved: dict[str, dict] = {}
        for row in rows:
            try:
                saved[str(row["user_id"])] = self._normalize(json.loads(row["filters_json"]), updated_at=row["updated_at"])
            except json.JSONDecodeError:
                continue
        return [
            {
                "user_id": str(user_id),
                **self._alert_filters(saved.get(str(user_id)) or self._default()),
            }
            for user_id in recipients
        ]

    def _alert_filters(self, filters: dict) -> dict:
        collection_names = filters.get("nfts") or default_collection_names()
        min_price = self._price_float(filters.get("minPrice"))
        max_price = self._price_float(filters.get("maxPrice")) or get_settings().mrkt_research_max_price
        return {
            "collection_names": collection_names,
            "collection_keys": {
                self._collection_key(name)
                for name in collection_names
                if name
            },
            "min_price": min_price,
            "max_price": max_price,
        }

    def matches_listing(self, listing: Listing, filters: dict) -> bool:
        collection_key = self._collection_key(listing.collection_name)
        if filters.get("collection_keys") and collection_key not in filters["collection_keys"]:
            return False
        if filters.get("min_price") is not None and listing.price < filters["min_price"]:
            return False
        if filters.get("max_price") is not None and listing.price > filters["max_price"]:
            return False
        return True

    def _default(self) -> dict:
        return {
            "nfts": [],
            "backdrops": [],
            "models": [],
            "symbols": [],
            "number": "",
            "minPrice": "",
            "maxPrice": str(int(self.DEFAULT_MAX_PRICE)),
            "updated_at": None,
        }

    def _normalize(self, filters: dict, updated_at: str | None = None) -> dict:
        def names(key: str) -> list[str]:
            value = filters.get(key)
            if not isinstance(value, list):
                return []
            clean = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    clean.append(item.strip())
            return list(dict.fromkeys(clean))[:200]

        normalized = {
            "nfts": names("nfts"),
            "backdrops": names("backdrops"),
            "models": names("models"),
            "symbols": names("symbols"),
            "number": str(filters.get("number") or "").strip()[:32],
            "minPrice": self._price_text(filters.get("minPrice")),
            "maxPrice": self._price_text(filters.get("maxPrice")),
            "updated_at": updated_at,
        }
        return normalized

    def _price_text(self, value: object) -> str:
        if value is None:
            return ""
        text = str(value).strip().replace(",", ".")
        if not text:
            return ""
        try:
            number = float(text)
        except ValueError:
            return ""
        if number < 0:
            return ""
        return f"{number:.4f}".rstrip("0").rstrip(".")

    def _price_float(self, value: object) -> float | None:
        text = self._price_text(value)
        return float(text) if text else None

    def _collection_key(self, value: str | None) -> str:
        return " ".join(canonical_collection_name(value).strip().lower().split())


SUBSCRIPTION_PLANS = {
    "day": {
        "id": "day",
        "title": "1 день",
        "description": "Пробный доступ к алертам бота на сутки.",
        "stars": 99,
        "duration_days": 1,
    },
    "week": {
        "id": "week",
        "title": "1 неделя",
        "description": "Недорогой доступ к алертам на неделю.",
        "stars": 399,
        "duration_days": 7,
    },
    "month": {
        "id": "month",
        "title": "1 месяц",
        "description": "Самый удобный тариф для постоянного поиска.",
        "stars": 1199,
        "duration_days": 30,
    },
    "forever": {
        "id": "forever",
        "title": "Навсегда",
        "description": "Разовая покупка без продления.",
        "stars": 4999,
        "duration_days": None,
    },
}


class SubscriptionRepository:
    def plans(self) -> list[dict]:
        return list(SUBSCRIPTION_PLANS.values())

    def plan(self, plan_id: str) -> dict | None:
        return SUBSCRIPTION_PLANS.get(plan_id)

    def get(self, user_id: int | str) -> dict:
        now = utc_now()
        if self.is_owner(user_id):
            return {
                "active": True,
                "plan_id": "owner",
                "status": "owner",
                "started_at": None,
                "expires_at": None,
                "updated_at": None,
                "plans": self.plans(),
            }
        if self.is_env_granted(user_id):
            return {
                "active": True,
                "plan_id": "env_grant",
                "status": "env_grant",
                "started_at": None,
                "expires_at": None,
                "updated_at": None,
                "plans": self.plans(),
            }
        with connect() as conn:
            row = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (str(user_id),)).fetchone()
        if not row:
            return {
                "active": False,
                "plan_id": None,
                "status": "inactive",
                "started_at": None,
                "expires_at": None,
                "updated_at": None,
                "plans": self.plans(),
            }
        data = dict(row)
        active = data["status"] == "active" and (not data.get("expires_at") or data["expires_at"] > now)
        data["active"] = active
        data["plans"] = self.plans()
        return data

    def is_owner(self, user_id: int | str) -> bool:
        settings = get_settings()
        owner_ids = settings.telegram_allowed_user_id_set | settings.telegram_allowed_chat_id_set
        return int(user_id) in owner_ids if str(user_id).isdigit() else False

    def is_env_granted(self, user_id: int | str) -> bool:
        return int(user_id) in get_settings().telegram_granted_user_id_set if str(user_id).isdigit() else False

    def active_recipient_ids(self) -> list[str]:
        settings = get_settings()
        recipients = {str(item) for item in (settings.telegram_allowed_user_id_set | settings.telegram_allowed_chat_id_set)}
        recipients.update(str(item) for item in settings.telegram_granted_user_id_set)
        if settings.telegram_alert_chat_id:
            recipients.add(str(settings.telegram_alert_chat_id))
        now = utc_now()
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM subscriptions
                WHERE status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (now,),
            ).fetchall()
        recipients.update(str(row["user_id"]) for row in rows)
        return sorted(recipients)

    def activate(
        self,
        user_id: int | str,
        plan_id: str,
        payload: str,
        currency: str,
        total_amount: int,
        telegram_payment_charge_id: str | None = None,
        provider_payment_charge_id: str | None = None,
    ) -> dict:
        plan = self.plan(plan_id)
        if not plan:
            raise ValueError(f"Unknown subscription plan: {plan_id}")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with connect() as conn:
            current = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (str(user_id),)).fetchone()
            if plan["duration_days"] is None:
                expires_at = None
            else:
                base = now_dt
                if current and current["expires_at"]:
                    try:
                        parsed = datetime.fromisoformat(current["expires_at"])
                        if parsed > now_dt:
                            base = parsed
                    except ValueError:
                        pass
                expires_at = (base + timedelta(days=plan["duration_days"])).isoformat()
            conn.execute(
                """
                INSERT INTO subscriptions (user_id, plan_id, status, started_at, expires_at, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    plan_id=excluded.plan_id,
                    status='active',
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (str(user_id), plan_id, now, expires_at, now),
            )
            conn.execute(
                """
                INSERT INTO subscription_payments (
                    user_id, plan_id, payload, currency, total_amount,
                    telegram_payment_charge_id, provider_payment_charge_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user_id),
                    plan_id,
                    payload,
                    currency,
                    total_amount,
                    telegram_payment_charge_id,
                    provider_payment_charge_id,
                    now,
                ),
            )
        return self.get(user_id)

    def grant(self, user_id: int | str, days: int | None, granted_by: int | str | None = None) -> dict:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at: str | None
        with connect() as conn:
            current = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (str(user_id),)).fetchone()
            if days is None:
                expires_at = None
                plan_id = "manual_forever"
            else:
                base = now_dt
                if current and current["status"] == "active" and current["expires_at"]:
                    try:
                        parsed = datetime.fromisoformat(current["expires_at"])
                        if parsed > now_dt:
                            base = parsed
                    except ValueError:
                        pass
                expires_at = (base + timedelta(days=days)).isoformat()
                plan_id = "manual"
            payload = f"admin_grant:{granted_by or 'unknown'}:{user_id}:{int(now_dt.timestamp())}"
            conn.execute(
                """
                INSERT INTO subscriptions (user_id, plan_id, status, started_at, expires_at, updated_at)
                VALUES (?, ?, 'active', ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    plan_id=excluded.plan_id,
                    status='active',
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (str(user_id), plan_id, now, expires_at, now),
            )
            conn.execute(
                """
                INSERT INTO subscription_payments (
                    user_id, plan_id, payload, currency, total_amount,
                    telegram_payment_charge_id, provider_payment_charge_id, created_at
                ) VALUES (?, ?, ?, 'ADMIN', 0, NULL, NULL, ?)
                """,
                (str(user_id), plan_id, payload, now),
            )
        return self.get(user_id)

    def revoke(self, user_id: int | str) -> dict:
        now = utc_now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO subscriptions (user_id, plan_id, status, started_at, expires_at, updated_at)
                VALUES (?, 'manual', 'revoked', ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    status='revoked',
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (str(user_id), now, now, now),
            )
        return self.get(user_id)
