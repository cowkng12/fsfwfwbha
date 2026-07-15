from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any

GIFT_HINTS = {
    "surge boat",
    "b-day candle",
    "evil eye",
    "money pot",
    "instant ramen",
    "vice cream",
    "liberty figure",
    "plush pepe",
    "loot bag",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan exported MRKT HAR/JSON files and find likely gift catalog responses.")
    parser.add_argument("paths", nargs="+", help="HAR/JSON file or directory paths to scan")
    return parser.parse_args()


def main() -> None:
    candidates: list[tuple[int, str, str, str]] = []
    for raw_path in parse_args().paths:
        path = Path(raw_path).expanduser()
        for file in files(path):
            candidates.extend(scan_file(file))
    if not candidates:
        print("No gift-like responses found.")
        return
    for score, source, url, summary in sorted(candidates, reverse=True):
        print(f"[score={score}] {source}")
        if url:
            print(f"  url: {url}")
        print(f"  {summary}")


def files(path: Path):
    if path.is_file():
        yield path
        return
    for pattern in ("*.har", "*.json"):
        yield from path.rglob(pattern)


def scan_file(path: Path) -> list[tuple[int, str, str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        print(f"skip {path}: {exc}")
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "log" in data:
        return scan_har(path, data)
    result = score_json(data)
    return [(result[0], str(path), "", result[1])] if result[0] > 0 else []


def scan_har(path: Path, data: dict[str, Any]) -> list[tuple[int, str, str, str]]:
    matches: list[tuple[int, str, str, str]] = []
    entries = data.get("log", {}).get("entries", [])
    for index, entry in enumerate(entries):
        response = entry.get("response", {})
        content = response.get("content", {})
        text = content.get("text") or ""
        if content.get("encoding") == "base64":
            try:
                text = base64.b64decode(text).decode("utf-8")
            except Exception:
                continue
        try:
            parsed = json.loads(text)
        except Exception:
            continue
        score, summary = score_json(parsed)
        if score > 0:
            request = entry.get("request", {})
            url = request.get("url", "")
            matches.append((score, f"{path}#entry{index}", url, summary))
    return matches


def score_json(value: Any) -> tuple[int, str]:
    text = json.dumps(value, ensure_ascii=False).lower()
    hints = [hint for hint in GIFT_HINTS if hint in text]
    items = value if isinstance(value, list) else first_list(value)
    keys = sorted({key for item in (items or [])[:20] if isinstance(item, dict) for key in item.keys()})
    title_like = count_title_like(items or [])
    score = len(hints) * 10 + min(title_like, 20)
    summary = f"hints={hints or '-'} items={len(items) if items is not None else '?'} title_like={title_like} keys={keys[:16]}"
    return score, summary


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


def count_title_like(items: list[Any]) -> int:
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("title") or item.get("name") or item.get("collectionName") or item.get("giftName")
        if isinstance(value, str) and re.search(r"[A-Za-zА-Яа-я]", value):
            count += 1
    return count


if __name__ == "__main__":
    main()
