"""
utils/cache.py – persist parsed results.

Backend selection (automatic):
  • If SUPABASE_URL + SUPABASE_KEY env vars are set → Supabase Postgres
  • Otherwise → local JSON files in $CACHE_DIR (default: <project_root>/cache/)

Supabase table (run once in your Supabase SQL editor):

    CREATE TABLE caches (
        slug         TEXT PRIMARY KEY,
        name         TEXT        NOT NULL,
        created      TIMESTAMPTZ NOT NULL DEFAULT now(),
        swiss_count  INTEGER     NOT NULL DEFAULT 0,
        draws_used   BOOLEAN     NOT NULL DEFAULT false,
        rows         JSONB       NOT NULL DEFAULT '[]',
        fighter_list JSONB       NOT NULL DEFAULT '[]'
    );

Cache entry schema (both backends):
{
    "name":         str,
    "slug":         str,
    "created":      str,   # ISO-8601
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

# ---------------------------------------------------------------------------
# Supabase client (only initialised when env vars are present)
# ---------------------------------------------------------------------------
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
_TABLE = "caches"

_sb = None  # supabase Client or None
if _SUPABASE_URL and _SUPABASE_KEY:
    try:
        from supabase import create_client  # type: ignore
        _sb = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    except Exception as exc:  # pragma: no cover
        print(f"[cache] Supabase init failed, falling back to local files: {exc}")

# ---------------------------------------------------------------------------
# Local-file fallback
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = pathlib.Path(os.environ.get("CACHE_DIR", str(_HERE / "cache")))


def _ensure_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "cache"


def default_name() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(
    name: str,
    rows: list[dict],
    fighter_list: list[dict],
    swiss_count: int,
    draws_used: bool,
    club_filter: str = "",
    type: str = "timetable",
) -> str:
    """Persist a result set. Returns the slug. Overwrites any existing entry with the same name."""
    slug = _slugify(name)
    entry = {
        "type":         type,
        "name":         name.strip() or default_name(),
        "slug":         slug,
        "created":      datetime.now().isoformat(timespec="seconds"),
        "swiss_count":  swiss_count,
        "draws_used":   draws_used,
        "club_filter":  club_filter,
        "rows":         rows,
        "fighter_list": fighter_list,
    }
    if _sb:
        # Delete any existing row with the same name (slug may have drifted)
        _sb.table(_TABLE).delete().eq("name", entry["name"]).execute()
        _sb.table(_TABLE).upsert(entry).execute()
    else:
        _ensure_dir()
        # Delete any existing files whose stored name matches (case-insensitive)
        name_lower = entry["name"].lower()
        for old in CACHE_DIR.glob("*.json"):
            try:
                old_data = json.loads(old.read_text(encoding="utf-8"))
                if old_data.get("name", "").lower() == name_lower and old.stem != slug:
                    old.unlink()
            except Exception:
                pass
        path = CACHE_DIR / f"{slug}.json"
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return slug


def load(slug: str) -> dict | None:
    """Load a cache entry by slug. Returns None if not found."""
    if _sb:
        res = _sb.table(_TABLE).select("*").eq("slug", slug).maybe_single().execute()
        return res.data if res.data else None
    else:
        path = CACHE_DIR / f"{slug}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def list_all() -> list[dict]:
    """Return all cached entries sorted newest-first (metadata only, no rows)."""
    if _sb:
        res = (
            _sb.table(_TABLE)
            .select("slug,name,created,swiss_count,draws_used,rows")
            .order("created", desc=True)
            .execute()
        )
        out = []
        for d in (res.data or []):
            out.append({
                "slug":        d["slug"],
                "name":        d["name"],
                "created":     d.get("created", ""),
                "swiss_count": d.get("swiss_count", 0),
                "draws_used":  d.get("draws_used", False),
                "row_count":   len(d.get("rows") or []),
            })
        return out
    else:
        _ensure_dir()
        entries = []
        for p in sorted(CACHE_DIR.glob("*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                entries.append({
                    "slug":        data.get("slug", p.stem),
                    "name":        data.get("name", p.stem),
                    "created":     data.get("created", ""),
                    "swiss_count": data.get("swiss_count", 0),
                    "draws_used":  data.get("draws_used", False),
                    "club_filter": data.get("club_filter", ""),
                    "type":        data.get("type", "timetable"),
                    "row_count":   len(data.get("rows", [])),
                })
            except Exception:
                pass
        return entries


def delete(slug: str) -> bool:
    """Delete a cache entry. Returns True if deleted, False if not found."""
    if _sb:
        res = _sb.table(_TABLE).delete().eq("slug", slug).execute()
        return bool(res.data)
    else:
        path = CACHE_DIR / f"{slug}.json"
        if path.exists():
            path.unlink()
            return True
        return False
