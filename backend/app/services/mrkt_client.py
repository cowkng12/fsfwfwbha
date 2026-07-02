from urllib.parse import unquote

import httpx

from app.config import Settings


class MrktClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._token = settings.mrkt_auth_token

    async def _fetch_token_from_telegram(self) -> str:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash or not self.settings.telegram_session:
            raise RuntimeError("MRKT auth requires TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION")

        from pyrogram import Client
        from pyrogram.raw.functions.messages import RequestAppWebView
        from pyrogram.raw.types import InputBotAppShortName, InputUser

        async with Client(
            name="mrkt_research",
            api_id=self.settings.telegram_api_id,
            api_hash=self.settings.telegram_api_hash,
            session_string=self.settings.telegram_session,
            in_memory=True,
        ) as client:
            bot_entity = await client.get_users("mrkt")
            peer = await client.resolve_peer("mrkt")
            bot = InputUser(user_id=bot_entity.id, access_hash=bot_entity.raw.access_hash)
            bot_app = InputBotAppShortName(bot_id=bot, short_name="app")
            web_view = await client.invoke(RequestAppWebView(peer=peer, app=bot_app, platform="android"))
            init_data = unquote(web_view.url.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion", 1)[0])

        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.post(f"{self.settings.mrkt_api_url}/auth", json={"data": init_data})
            response.raise_for_status()
            token = response.json().get("token")
        if not token:
            raise RuntimeError("MRKT auth did not return token")
        self._token = token
        return token

    async def token(self) -> str:
        return self._token or await self._fetch_token_from_telegram()

    async def saling(self, collection_names: list[str], model_names: list[str] | None = None, backdrop_names: list[str] | None = None, count: int = 20) -> list[dict]:
        payload = {
            "collectionNames": collection_names,
            "modelNames": model_names or [],
            "backdropNames": backdrop_names or [],
            "symbolNames": [],
            "ordering": "Price",
            "lowToHigh": True,
            "maxPrice": None,
            "minPrice": None,
            "mintable": None,
            "number": None,
            "count": min(count, 20),
            "cursor": "",
            "query": None,
            "promotedFirst": False,
        }
        headers = {"Authorization": await self.token(), "Referer": "https://cdn.tgmrkt.io/"}
        async with httpx.AsyncClient(timeout=45) as http:
            response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=headers, json=payload)
            if response.status_code in {401, 403}:
                self._token = None
                headers["Authorization"] = await self._fetch_token_from_telegram()
                response = await http.post(f"{self.settings.mrkt_api_url}/gifts/saling", headers=headers, json=payload)
            response.raise_for_status()
        return response.json().get("gifts", [])
