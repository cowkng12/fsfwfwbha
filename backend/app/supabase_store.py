import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class SupabaseStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = (settings.supabase_url or "").rstrip("/")
        self.key = settings.supabase_service_role_key or ""

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        response = self._request("GET", table, params=params)
        data = response.json()
        return data if isinstance(data, list) else []

    def select_one(self, table: str, params: dict[str, str]) -> dict[str, Any] | None:
        rows = self.select(table, {**params, "limit": "1"})
        return rows[0] if rows else None

    def upsert(self, table: str, row: dict[str, Any], on_conflict: str) -> dict[str, Any] | None:
        response = self._request(
            "POST",
            table,
            params={"on_conflict": on_conflict},
            json=row,
            prefer="resolution=merge-duplicates,return=representation",
        )
        data = response.json()
        return data[0] if isinstance(data, list) and data else None

    def insert(self, table: str, row: dict[str, Any]) -> None:
        self._request("POST", table, json=row, prefer="return=minimal")

    def _request(
        self,
        method: str,
        table: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }
        if prefer:
            headers["Prefer"] = prefer
        url = f"{self.url}/rest/v1/{table}"
        with httpx.Client(timeout=20) as client:
            response = client.request(method, url, params=params, json=json, headers=headers)
        if response.is_error:
            logger.warning("Supabase %s %s failed: %s", method, table, response.text)
            raise RuntimeError(f"Supabase {method} {table} failed with status {response.status_code}")
        return response
