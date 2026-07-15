from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.services.mrkt_client import MrktClient  # noqa: E402

OUTPUT_DIR = ROOT / "scripts" / "mrkt-discovery-output"
GIFT_HINTS = {
    "surge boat",
    "b-day candle",
    "evil eye",
    "money pot",
    "instant ramen",
    "vice cream",
    "liberty figure",
}


@dataclass(frozen=True)
class Probe:
    name: str
    method: str
    path: str
    payload: dict[str, Any] | None = None


DEFAULT_PROBES = [
    Probe("gift_collections_get", "GET", "/gifts/collections"),
    Probe("sticker_characters_get", "GET", "/sticker-sets/characters"),
    Probe("sticker_characters_post", "POST", "/sticker-sets/characters", {}),
    Probe("sticker_collections_get", "GET", "/sticker-sets/collections"),
    Probe("sticker_collections_post", "POST", "/sticker-sets/collections", {}),
    Probe("gift_saling_empty", "POST", "/gifts/saling", {
        "collectionNames": [],
        "modelNames": [],
        "backdropNames": [],
        "symbolNames": [],
        "ordering": "Price",
        "lowToHigh": True,
        "maxPrice": None,
        "minPrice": None,
        "mintable": None,
        "number": None,
        "count": 20,
        "cursor": "",
        "query": None,
        "promotedFirst": False,
    }),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe MRKT API endpoints and save JSON responses without printing tokens.")
    parser.add_argument("--endpoint", help="Extra endpoint path or full URL to probe, for example /sticker-sets/characters")
    parser.add_argument("--method", default="POST", choices=["GET", "POST"], help="Method for --endpoint")
    parser.add_argument("--payload", help="JSON payload for --endpoint")
    parser.add_argument("--no-defaults", action="store_true", help="Only probe --endpoint")
    return parser.parse_args()


async def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    args = parse_args()
    settings = get_settings()
    client = MrktClient(settings)
    token = await client.token()
    headers = client._headers(token)
    probes = [] if args.no_defaults else list(DEFAULT_PROBES)
    if args.endpoint:
        payload = json.loads(args.payload) if args.payload else ({} if args.method == "POST" else None)
        probes.append(Probe("custom", args.method, args.endpoint, payload))
    if not probes:
        raise SystemExit("No probes selected. Pass --endpoint or omit --no-defaults.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    async with AsyncSession(impersonate="chrome", timeout=30) as http:
        for probe in probes:
            refreshed_headers = await run_probe(http, settings.mrkt_api_url.rstrip("/"), headers, probe, client)
            if refreshed_headers:
                headers = refreshed_headers


async def run_probe(
    http: AsyncSession,
    api_url: str,
    headers: dict[str, str],
    probe: Probe,
    client: MrktClient,
) -> dict[str, str] | None:
    url = probe.path if probe.path.startswith("http") else f"{api_url}/{probe.path.lstrip('/')}"
    try:
        response = await send(http, url, headers, probe)
        refreshed_headers = None
        if response.status_code in {401, 403}:
            token = await client._fetch_token_from_telegram()
            refreshed_headers = client._headers(token)
            response = await send(http, url, refreshed_headers, probe)
    except Exception as exc:
        print(f"{probe.name}: ERROR {exc}")
        return None

    content_type = response.headers.get("content-type", "")
    body = response.text
    parsed: Any = None
    if "json" in content_type or body[:1] in ("[", "{"):
        try:
            parsed = response.json()
        except Exception:
            parsed = None

    summary = summarize(parsed, body)
    print(f"{probe.name}: {response.status_code} {content_type} bytes={len(body)} {summary}")
    if response.status_code < 400 and parsed is not None:
        target = OUTPUT_DIR / f"{probe.name}.json"
        target.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  saved: {target}")
    return refreshed_headers


async def send(http: AsyncSession, url: str, headers: dict[str, str], probe: Probe):
    if probe.method == "GET":
        return await http.get(url, headers=headers)
    return await http.post(url, headers=headers, json=probe.payload or {})


def summarize(parsed: Any, body: str) -> str:
    if parsed is None:
        return preview(body)
    items = parsed if isinstance(parsed, list) else first_list(parsed)
    keys = sorted({key for item in items[:10] if isinstance(item, dict) for key in item.keys()}) if items else []
    hints = gift_hint_count(parsed)
    item_count = len(items) if items is not None else "?"
    return f"items={item_count} keys={keys[:12]} gift_hints={hints}"


def first_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("items", "data", "characters", "collections", "gifts", "results"):
        item = value.get(key)
        if isinstance(item, list):
            return item
    for item in value.values():
        if isinstance(item, list):
            return item
    return None


def gift_hint_count(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False).lower()
    return sum(1 for hint in GIFT_HINTS if hint in text)


def preview(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return compact[:160]


if __name__ == "__main__":
    asyncio.run(main())
