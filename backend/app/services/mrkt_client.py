import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib.parse import unquote

import httpx
from curl_cffi.requests import AsyncSession
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.messages import RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName, InputPeerUser, InputUser

from app.config import Settings
from app.database import connect


MRKT_AUTH_CACHE_KEY = "mrkt_auth_token"


class MrktAuthError(RuntimeError):
    def __init__(self, message: str, cooldown_until: datetime | None = None):
        super().__init__(message)
        self.cooldown_until = cooldown_until


class MrktClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._token = settings.mrkt_auth_token
        self._cookie = f"access_token={settings.mrkt_auth_token}" if settings.mrkt_auth_token else None
        self._gift_collections_cache: tuple[float, list[dict]] | None = None
        self._auth_blocked_until: datetime | None = None
        self._auth_blocked_reason: str | None = None
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def _fetch_token_from_telegram(self) -> str:
        self._raise_if_auth_blocked()
        init_data = await self._fetch_init_data_from_telegram()

        async with AsyncSession(impersonate="chrome", timeout=30) as http:
            await self._wait_for_request_slot()
            response = await http.post(f"{self.settings.mrkt_api_url}/auth", json={"data": init_data})
            if response.status_code in {401, 403}:
                self._block_auth(f"MRKT auth rejected Telegram session with {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
            token = response.json().get("token")
        if not token:
            raise RuntimeError("MRKT auth did not return token")
        self._set_token(token, persist=True)
        return token

    async def _fetch_init_data_from_telegram(self) -> str:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash or not self.settings.telegram_session:
            raise RuntimeError("MRKT auth requires TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION")

        async with TelegramClient(
            StringSession(self.settings.telegram_session),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        ) as client:
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram session is not authorized")
            resolved = await client(ResolveUsernameRequest("mrkt"))
            bot_user = resolved.users[0]
            bot = InputUser(user_id=bot_user.id, access_hash=bot_user.access_hash)
            peer = InputPeerUser(user_id=bot_user.id, access_hash=bot_user.access_hash)
            app = InputBotAppShortName(bot_id=bot, short_name="app")
            web_view = await client(RequestAppWebViewRequest(peer=peer, app=app, platform="android"))
            init_data = unquote(web_view.url.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion", 1)[0])
        return init_data

    async def token(self) -> str:
        self._raise_if_auth_blocked()
        cached_token = self._load_persisted_token()
        if cached_token and cached_token != self._token:
            self._set_token(cached_token)
        return self._token or await self._fetch_token_from_telegram()

    async def saling(
        self,
        collection_names: list[str],
        model_names: list[str] | None = None,
        backdrop_names: list[str] | None = None,
        count: int = 20,
        cursor: str = "",
        min_price: float | None = None,
        max_price: float | None = None,
        use_default_max_price: bool = True,
    ) -> list[dict]:
        payload = await self.saling_page(
            collection_names,
            model_names=model_names,
            backdrop_names=backdrop_names,
            count=count,
            cursor=cursor,
            min_price=min_price,
            max_price=max_price,
            use_default_max_price=use_default_max_price,
        )
        return payload.get("gifts", [])

    async def saling_page(
        self,
        collection_names: list[str],
        model_names: list[str] | None = None,
        backdrop_names: list[str] | None = None,
        count: int = 20,
        cursor: str = "",
        min_price: float | None = None,
        max_price: float | None = None,
        use_default_max_price: bool = True,
    ) -> dict:
        effective_max_price = max_price if max_price is not None else (self.settings.mrkt_max_price if use_default_max_price else None)
        payload = self._saling_payload(
            collection_names,
            model_names=model_names,
            backdrop_names=backdrop_names,
            count=count,
            cursor=cursor,
            min_price=min_price,
            max_price=effective_max_price,
        )
        headers = self._headers(await self.token())
        async with AsyncSession(impersonate="chrome", timeout=45) as http:
            await self._wait_for_request_slot()
            response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=headers, json=payload)
            if response.status_code == 401:
                self._clear_token()
                headers = self._headers(await self._fetch_token_from_telegram())
                await self._wait_for_request_slot()
                response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=headers, json=payload)
                if response.status_code in {401, 403}:
                    self._clear_token()
                    self._block_auth(f"MRKT saling rejected refreshed token with {response.status_code}: {response.text[:200]}")
            if response.status_code == 403:
                self._clear_token()
                self._block_auth(f"MRKT saling rejected token with {response.status_code}: {response.text[:200]}")
            if response.status_code >= 400:
                raise RuntimeError(f"MRKT saling failed {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception:
                raise RuntimeError(f"MRKT saling returned non-JSON {response.status_code}: {response.text[:300]}")
        return payload

    async def token_diagnostics(self, refresh_auth: bool = False) -> dict:
        result: dict = {
            "has_mrkt_auth_token": bool(self.settings.mrkt_auth_token),
            "has_telegram_api_id": bool(self.settings.telegram_api_id),
            "has_telegram_api_hash": bool(self.settings.telegram_api_hash),
            "has_telegram_session": bool(self.settings.telegram_session),
            "has_persisted_mrkt_token": bool(self._load_persisted_token()),
            "auth_cooldown_active": self.auth_cooldown_active,
            "auth_cooldown_until": self.auth_cooldown_until_iso,
            "auth_cooldown_reason": self._auth_blocked_reason,
        }
        payload = self._saling_payload(["Airplane"], count=1, max_price=self.settings.mrkt_research_max_price)
        if self.auth_cooldown_active:
            result["cached_token_ok"] = False
            result["cached_token_status"] = None
            result["cached_token_error"] = "MRKT auth is in cooldown"
        elif self._token:
            try:
                async with AsyncSession(impersonate="chrome", timeout=30) as http:
                    await self._wait_for_request_slot()
                    response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=self._headers(self._token), json=payload)
                result["cached_token_status"] = response.status_code
                result["cached_token_ok"] = response.status_code < 400
                if response.status_code >= 400:
                    result["cached_token_error"] = response.text[:200]
            except Exception as exc:
                result["cached_token_ok"] = False
                result["cached_token_error"] = str(exc)
        else:
            result["cached_token_ok"] = False
            result["cached_token_status"] = None

        if not refresh_auth:
            result["fresh_auth_skipped"] = True
            return result

        init_data = ""
        try:
            init_data = await self._fetch_init_data_from_telegram()
            result["telegram_session_ok"] = True
            result["telegram_webview_ok"] = bool(init_data)
        except Exception as exc:
            result["telegram_session_ok"] = False
            result["telegram_webview_ok"] = False
            result["telegram_session_error"] = str(exc)

        if init_data:
            try:
                async with AsyncSession(impersonate="chrome", timeout=30) as http:
                    await self._wait_for_request_slot()
                    response = await http.post(f"{self.settings.mrkt_api_url}/auth", json={"data": init_data})
                result["fresh_auth_status"] = response.status_code
                result["fresh_auth_ok"] = response.status_code < 400
                if response.status_code < 400:
                    token = response.json().get("token")
                    result["fresh_auth_token_returned"] = bool(token)
                    if token:
                        self._set_token(token, persist=True)
                else:
                    result["fresh_auth_error"] = response.text[:200]
            except Exception as exc:
                result["fresh_auth_ok"] = False
                result["fresh_auth_error"] = str(exc)
        return result

    async def gift_trait_options(self, trait: str, collection_names: list[str]) -> list[dict]:
        headers = self._headers(await self.token())
        payload = {"collections": collection_names}
        async with AsyncSession(impersonate="chrome", timeout=45) as http:
            await self._wait_for_request_slot()
            response = await http.post(f"{self.settings.mrkt_api_url}/gifts/{trait}", headers=headers, json=payload)
            if response.status_code == 401:
                self._clear_token()
                headers = self._headers(await self._fetch_token_from_telegram())
                await self._wait_for_request_slot()
                response = await http.post(f"{self.settings.mrkt_api_url}/gifts/{trait}", headers=headers, json=payload)
                if response.status_code in {401, 403}:
                    self._clear_token()
                    self._block_auth(f"MRKT {trait} rejected refreshed token with {response.status_code}: {response.text[:200]}")
            if response.status_code == 403:
                self._clear_token()
                self._block_auth(f"MRKT {trait} rejected token with {response.status_code}: {response.text[:200]}")
            if response.status_code >= 400:
                raise RuntimeError(f"MRKT {trait} failed {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            try:
                data = response.json()
            except Exception:
                raise RuntimeError(f"MRKT {trait} returned non-JSON {response.status_code}: {response.text[:300]}")
        return data if isinstance(data, list) else []

    async def gift_collections(self) -> list[dict]:
        if self._gift_collections_cache and monotonic() - self._gift_collections_cache[0] < 300:
            return self._gift_collections_cache[1]
        async with AsyncSession(impersonate="chrome", timeout=45) as http:
            await self._wait_for_request_slot()
            response = await http.get(f"{self.settings.mrkt_api_url}/gifts/collections", headers=self._public_headers())
            if response.status_code >= 400:
                raise RuntimeError(f"MRKT gift collections failed {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            try:
                data = response.json()
            except Exception:
                raise RuntimeError(f"MRKT gift collections returned non-JSON {response.status_code}: {response.text[:300]}")
        collections = data if isinstance(data, list) else data.get("collections") if isinstance(data, dict) else []
        if not isinstance(collections, list):
            collections = []
        self._gift_collections_cache = (monotonic(), collections)
        return collections

    def _saling_payload(
        self,
        collection_names: list[str],
        model_names: list[str] | None = None,
        backdrop_names: list[str] | None = None,
        count: int = 20,
        cursor: str = "",
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict:
        return {
            "collectionNames": collection_names,
            "modelNames": model_names or [],
            "backdropNames": backdrop_names or [],
            "symbolNames": [],
            "ordering": "Price",
            "lowToHigh": True,
            "maxPrice": self._ton_to_nano(max_price),
            "minPrice": self._ton_to_nano(min_price),
            "mintable": None,
            "number": None,
            "count": min(count, 20),
            "cursor": cursor,
            "query": None,
            "promotedFirst": False,
        }

    def _public_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "origin": "https://cdn.tgmrkt.io",
            "referer": "https://cdn.tgmrkt.io/",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": "Android",
            "user-agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
        }

    def _ton_to_nano(self, value: float | None) -> int | None:
        if value is None:
            return None
        return int(float(value) * 1_000_000_000)

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": token,
            "origin": "https://cdn.tgmrkt.io",
            "referer": "https://cdn.tgmrkt.io/",
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        }
        if self._cookie:
            headers["cookie"] = self._cookie
        return headers

    @property
    def auth_cooldown_active(self) -> bool:
        return bool(self._auth_blocked_until and datetime.now(timezone.utc) < self._auth_blocked_until)

    @property
    def auth_cooldown_until_iso(self) -> str | None:
        return self._auth_blocked_until.isoformat() if self.auth_cooldown_active and self._auth_blocked_until else None

    def _raise_if_auth_blocked(self) -> None:
        if self.auth_cooldown_active:
            raise MrktAuthError(
                self._auth_blocked_reason or "MRKT auth is in cooldown",
                cooldown_until=self._auth_blocked_until,
            )
        self._auth_blocked_until = None
        self._auth_blocked_reason = None

    def _block_auth(self, reason: str) -> None:
        seconds = max(300, int(self.settings.mrkt_auth_cooldown_seconds or 0))
        self._auth_blocked_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self._auth_blocked_reason = reason
        raise MrktAuthError(reason, cooldown_until=self._auth_blocked_until)

    def _set_token(self, token: str, persist: bool = False) -> None:
        self._token = token
        self._cookie = f"access_token={token}"
        if persist:
            self._save_persisted_token(token)

    def _clear_token(self) -> None:
        self._token = None
        self._cookie = None
        self._delete_persisted_token()

    def clear_token_cache(self) -> dict[str, bool]:
        self._clear_token()
        self._auth_blocked_until = None
        self._auth_blocked_reason = None
        return {"cleared": True, "auth_cooldown_reset": True}

    def _load_persisted_token(self) -> str | None:
        try:
            with connect() as conn:
                row = conn.execute("SELECT value FROM auth_cache WHERE key = ?", (MRKT_AUTH_CACHE_KEY,)).fetchone()
            return str(row["value"]) if row and row["value"] else None
        except Exception:
            return None

    def _save_persisted_token(self, token: str) -> None:
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO auth_cache (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (MRKT_AUTH_CACHE_KEY, token, datetime.now(timezone.utc).isoformat()),
                )
        except Exception:
            pass

    def _delete_persisted_token(self) -> None:
        try:
            with connect() as conn:
                conn.execute("DELETE FROM auth_cache WHERE key = ?", (MRKT_AUTH_CACHE_KEY,))
        except Exception:
            pass

    async def _wait_for_request_slot(self) -> None:
        delay = max(0.0, float(self.settings.mrkt_request_delay_seconds or 0))
        if delay <= 0:
            return
        async with self._request_lock:
            elapsed = monotonic() - self._last_request_at
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request_at = monotonic()
