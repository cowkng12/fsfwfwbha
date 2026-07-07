import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).parent / "catalogs"
BLOCKED_COLLECTION_MODELS = {
    ("swiss watch", "gameboy"),
    ("smiss watch", "gameboy"),
}
COLLECTION_QUALITY_RULES = {
    "liberty figure": {
        "require_model": True,
        "models": {
            "homeland",
            "maga",
            "marilyn",
            "ifather",
            "moonwalker",
        },
        "search_models": {
            "Homeland",
            "MAGA",
            "Marilyn",
            "iFather",
            "Moonwalker",
        },
        "backdrops": {
            "amber",
            "aquamarine",
            "azure blue",
            "black",
            "crimson",
            "cyberpunk",
            "electric purple",
            "electric indigo",
            "emerald",
            "fuchsia",
            "gold",
            "lavender",
            "magenta",
            "malachite",
            "mint green",
            "neon blue",
            "onyx black",
            "pacific green",
            "platinum",
            "pure gold",
            "purple",
            "ruby",
            "sapphire",
            "satin gold",
            "silver",
            "violet",
            "white",
        },
    }
}


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


def blocked_collection_model_pairs() -> set[tuple[str, str]]:
    return BLOCKED_COLLECTION_MODELS


def collection_quality_rules() -> dict[str, dict[str, Any]]:
    return COLLECTION_QUALITY_RULES


def priority_collection_search_models(collection_name: str | None) -> list[str]:
    rule = COLLECTION_QUALITY_RULES.get(_normalize_name(collection_name))
    if not rule:
        return []
    return sorted(rule.get("search_models") or rule["models"])


def collection_requires_priority_model(collection_name: str | None) -> bool:
    rule = COLLECTION_QUALITY_RULES.get(_normalize_name(collection_name))
    return bool(rule and rule.get("require_model"))


def is_blocked_collection_model(collection_name: str | None, model_name: str | None) -> bool:
    return (_normalize_name(collection_name), _normalize_name(model_name)) in BLOCKED_COLLECTION_MODELS


def has_collection_quality_rules(collection_name: str | None) -> bool:
    return _normalize_name(collection_name) in COLLECTION_QUALITY_RULES


def is_priority_collection_model(collection_name: str | None, model_name: str | None) -> bool:
    rule = COLLECTION_QUALITY_RULES.get(_normalize_name(collection_name))
    return bool(rule and _normalize_name(model_name) in rule["models"])


def is_priority_collection_backdrop(collection_name: str | None, backdrop_name: str | None) -> bool:
    rule = COLLECTION_QUALITY_RULES.get(_normalize_name(collection_name))
    return bool(rule and _normalize_name(backdrop_name) in rule["backdrops"])


def has_collection_specific_quality(collection_name: str | None, model_name: str | None, backdrop_name: str | None) -> bool:
    if collection_requires_priority_model(collection_name):
        return is_priority_collection_model(collection_name, model_name)
    return is_priority_collection_model(collection_name, model_name) or is_priority_collection_backdrop(collection_name, backdrop_name)


def _normalize_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())
