import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).parent / "catalogs"


def _load(name: str) -> list[dict[str, Any]]:
    with (CATALOG_DIR / name).open("r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache
def get_catalog() -> dict[str, list[dict[str, Any]]]:
    return {
        "nfts": _load("nfts.json"),
        "backdrops": _load("backdrops.json"),
        "models": _load("models.json"),
    }


def default_collection_names() -> list[str]:
    names: list[str] = []
    for item in get_catalog()["nfts"]:
        names.extend(item.get("searchNames") or [item["name"]])
    return list(dict.fromkeys(names))
