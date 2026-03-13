"""
utils/cache.py – persist parsed results to JSON files.

Cache directory: $CACHE_DIR env var, or  <project_root>/cache/
Each entry is a single JSON file named  <slug>.json

Cache entry schema:
{
    "name":         str,       # human-readable label
    "slug":         str,       # filename-safe version of name
    "created":      str,       # ISO-8601 datetime
    "swiss_count":  int,
    "draws_used":   bool,
    "rows":         list[dict],
    "fighter_list": list[dict],
}
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime

_HERE = pathlib.Path(__file__).resolve().parent.parent  # project root
CACHE_DIR = pathlib.Path(os.environ.get("CACHE_DIR", str(_HERE / "cache")))


def _ensure_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(name: str) -> str:
    """Convert a name to a safe lowercase filename slug."""
    slug = name.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "cache"


def default_name() -> str:
    """Return today's date as default cache name, e.g. '2026-03-13'."""
    return datetime.now().strftime("%Y-%m-%d")


def save(
    name: str,
    rows: list[dict],
    fighter_list: list[dict],
    swiss_count: int,
    draws_used: bool,
) -> str:
    """
    Persist a result set.  Returns the slug used as the cache key.
    If a cache with the same slug already exists it is overwritten.
    """
    _ensure_dir()
    slug = _slugify(name)
    entry = {
        "name":         name.strip() or default_name(),
        "slug":         slug,
        "created":      datetime.now().isoformat(timespec="seconds"),
        "swiss_count":  swiss_count,
        "draws_used":   draws_used,
        "rows":         rows,
        "fighter_list": fighter_list,
    }
    path = CACHE_DIR / f"{slug}.json"
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return slug


def load(slug: str) -> dict | None:
    """Load a cache entry by slug. Returns None if not found."""
    path = CACHE_DIR / f"{slug}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_all() -> list[dict]:
    """
    Return all cached entries sorted newest-first.
    Each item contains only metadata (no rows/fighter_list) for fast listing.
    """
    _ensure_dir()
    entries = []
    for p in sorted(CACHE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries.append({
                "slug":        data.get("slug", p.stem),
                "name":        data.get("name", p.stem),
                "created":     data.get("created", ""),
                "swiss_count": data.get("swiss_count", 0),
                "draws_used":  data.get("draws_used", False),
                "row_count":   len(data.get("rows", [])),
            })
        except Exception:
            pass
    return entries


def delete(slug: str) -> bool:
    """Delete a cache entry. Returns True if deleted, False if not found."""
    path = CACHE_DIR / f"{slug}.json"
    if path.exists():
        path.unlink()
        return True
    return False
