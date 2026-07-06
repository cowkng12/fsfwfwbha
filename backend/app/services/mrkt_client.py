from urllib.parse import unquote

import httpx
from curl_cffi.requests import AsyncSession
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.messages import RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName, InputPeerUser, InputUser

from app.config import Settings


class MrktClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._token = settings.mrkt_auth_token
        self._cookie = f"access_token={settings.mrkt_auth_token}" if settings.mrkt_auth_token else None

    async def _fetch_token_from_telegram(self) -> str:
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

        async with AsyncSession(impersonate="chrome", timeout=30) as http:
            response = await http.post(f"{self.settings.mrkt_api_url}/auth", json={"data": init_data})
            response.raise_for_status()
            token = response.json().get("token")
        if not token:
            raise RuntimeError("MRKT auth did not return token")
        self._token = token
        self._cookie = f"access_token={token}"
        return token

    async def token(self) -> str:
        return self._token or await self._fetch_token_from_telegram()

    async def saling(
        self,
        collection_names: list[str],
        model_names: list[str] | None = None,
        backdrop_names: list[str] | None = None,
        count: int = 20,
        max_price: float | None = None,
        use_default_max_price: bool = True,
    ) -> list[dict]:
        effective_max_price = max_price if max_price is not None else (self.settings.mrkt_max_price if use_default_max_price else None)
        payload = {
            "collectionNames": collection_names,
            "modelNames": model_names or [],
            "backdropNames": backdrop_names or [],
            "symbolNames": [],
            "ordering": "Price",
            "lowToHigh": True,
            "maxPrice": self._ton_to_nano(effective_max_price),
            "minPrice": None,
            "mintable": None,
            "number": None,
            "count": min(count, 20),
            "cursor": "",
            "query": None,
            "promotedFirst": False,
        }
        headers = self._headers(await self.token())
        async with AsyncSession(impersonate="chrome", timeout=45) as http:
            response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=headers, json=payload)
            if response.status_code in {401, 403}:
                self._token = None
                headers = self._headers(await self._fetch_token_from_telegram())
                response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=headers, json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"MRKT saling failed {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception:
                raise RuntimeError(f"MRKT saling returned non-JSON {response.status_code}: {response.text[:300]}")
        return payload.get("gifts", [])

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
