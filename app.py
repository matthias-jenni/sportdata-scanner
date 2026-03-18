"""
app.py – Sportdata Swiss Fighter Timetable
==========================================
Upload two PDFs (registrations + schedule) and get back a
chronological timetable of all Swiss fighters.
"""
from __future__ import annotations

import os
import traceback

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)

from utils.parse_registrations import get_swiss_fighters
from utils.parse_registrations_html import get_swiss_fighters_html
from utils.parse_schedule import extract_schedule
from utils.parse_draws import extract_draws, pool_for_fighter
from utils import cache as _cache


def _load_swiss_fighters(path: str) -> list[dict]:
    """Dispatch to the correct parser based on file extension."""
    if path.lower().endswith(('.html', '.htm')):
        return get_swiss_fighters_html(path)
    return get_swiss_fighters(path)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# --- helper ----------------------------------------------------------------


import re as _re
# Matches any variant:
#   normal:    '01 PF 034 OC M -37 KG'  -> ('pf', '034')
#   openweight: '001 PF 0460 GC J M'    -> ('pf', '0460')
# Strategy: anchor on the DISCIPLINE (exactly 2 uppercase letters between
# two whitespace-separated groups of digits), then capture both surrounding
# digit groups so the full category number is preserved.
_CAT_NUM_RE = _re.compile(r"(\d+)\s+([A-Z]{2})\s+(\d+)", _re.IGNORECASE)


def _cat_key(s: str):
    """
    Extract the unique category key: (discipline_prefix, category_number).
    E.g. '01 PF 034 OC M -37 KG'   ->  ('pf', '034')
         '02 LC 109 OC F -55 kg'   ->  ('lc', '109')
         '001 PF 0460 GC J M'      ->  ('pf', '0460')
    Returns None if not parseable.
    """
    m = _CAT_NUM_RE.search(s)
    if m:
        return (m.group(2).lower(), m.group(3))
    return None


def _cats_match(reg_cat: str, sched_code: str) -> bool:
    """Match by exact (discipline, 3-digit-number) key."""
    a = _cat_key(reg_cat)
    b = _cat_key(sched_code)
    return a is not None and b is not None and a == b


_POOL_PHASE_RE = _re.compile(r"^Pool\s+(\d+)/", _re.IGNORECASE)


def _pool_num_from_phase(phase: str):
    """Return the pool number (int) from 'Pool N/M', or None for finals/empty."""
    m = _POOL_PHASE_RE.match(phase or "")
    return int(m.group(1)) if m else None


def _match(fighters: list[dict], schedule: list[dict], draws: dict | None = None) -> list[dict]:
    """
    Match Swiss fighters to schedule slots by (discipline, 3-digit-number) key.

    When *draws* is provided, pool rounds are filtered so each fighter only
    appears in their own pool's slot. Finals / Pool-winner / empty phases are
    always included regardless.  If a fighter's category is not found in draws
    at all, all their slots are included (we have no pool info to filter on).
    """
    # Build lookup: cat_key -> list of Swiss fighters
    cat_to_swiss: dict[tuple, list[dict]] = {}
    for f in fighters:
        key = _cat_key(f.get("category", ""))
        if key:
            cat_to_swiss.setdefault(key, []).append(f)

    rows = []
    for slot in schedule:
        sched_code = slot.get("category_code", "")
        if not sched_code:
            continue
        sched_key = _cat_key(sched_code)
        if not sched_key:
            continue

        matched_swiss: list[dict] = cat_to_swiss.get(sched_key, [])
        slot_pool = _pool_num_from_phase(slot.get("phase", ""))

        for sw in matched_swiss:
            # Determine which pool this fighter belongs to (if draws available)
            fighter_pool = None
            if draws:
                fighter_pool = pool_for_fighter(draws, sched_code, sw["name"])

            # Filter pool rounds: if we know the fighter's pool, skip slots
            # for other pools.  Finals / Pool-winner / empty = always include.
            if draws and fighter_pool is not None and slot_pool is not None:
                if fighter_pool != slot_pool:
                    continue

            rows.append({
                "time":       slot["time"],
                "time_end":   slot.get("time_end", ""),
                "tatami":     slot["tatami"],
                "category":   sched_code,
                "phase":      slot.get("phase", ""),
                "name":       sw["name"],
                "club":       sw.get("club", ""),
                "country":    sw.get("country", ""),
                "pool":       fighter_pool,
                "pool_label": f"Pool {fighter_pool}" if fighter_pool is not None else "",
            })

    # Deduplicate by (time, tatami, name)
    seen = set()
    unique = []
    for r in rows:
        key = (r["time"], r["tatami"], r["name"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(r)

    from datetime import datetime
    unique.sort(key=lambda r: (
        datetime.strptime(r["time"], "%H:%M") if ":" in r["time"] else datetime.max
    ))
    return unique


# --- routes ----------------------------------------------------------------


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", caches=_cache.list_all(), default_name=_cache.default_name())


@app.route("/debug", methods=["POST"])
def debug():
    """Upload both PDFs and get raw JSON output from both parsers."""
    import json, tempfile, pathlib
    reg_file = request.files.get("registrations")
    sched_file = request.files.get("schedule")
    if not reg_file or not sched_file:
        return "Upload both files", 400
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        reg_ext = _re.sub(r'.*\.', '.', reg_file.filename.lower()) or '.pdf'
        reg_path = tmp / f"registrations{reg_ext}"
        sched_path = tmp / "schedule.pdf"
        reg_file.save(str(reg_path))
        sched_file.save(str(sched_path))
        from utils.parse_registrations import extract_fighters, get_swiss_fighters
        from utils.parse_schedule import extract_schedule
        # For debug, always try PDF extractor; HTML path still shows swiss fighters
        ext = reg_path.suffix.lower()
        if ext in ('.html', '.htm'):
            all_fighters = get_swiss_fighters_html(str(reg_path))
        else:
            all_fighters = extract_fighters(str(reg_path))
        swiss = _load_swiss_fighters(str(reg_path))
        schedule = extract_schedule(str(sched_path))
    result = {
        "registrations_total": len(all_fighters),
        "registrations_sample": all_fighters[:20],
        "swiss_fighters": swiss,
        "schedule_total": len(schedule),
        "schedule_sample": schedule[:30],
    }
    return app.response_class(
        json.dumps(result, indent=2, ensure_ascii=False),
        mimetype="application/json",
    )


@app.route("/process", methods=["POST"])
def process():
    reg_file = request.files.get("registrations")
    sched_file = request.files.get("schedule")

    if not reg_file or reg_file.filename == "":
        flash("Please upload the registrations PDF.", "error")
        return redirect(url_for("index"))
    if not sched_file or sched_file.filename == "":
        flash("Please upload the schedule PDF.", "error")
        return redirect(url_for("index"))

    import tempfile, pathlib

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            reg_ext = _re.sub(r'.*\.', '.', reg_file.filename.lower()) or '.pdf'
            reg_path = tmp / f"registrations{reg_ext}"
            sched_path = tmp / "schedule.pdf"
            reg_file.save(str(reg_path))
            sched_file.save(str(sched_path))

            draws_file = request.files.get("draws")
            draws = {}
            if draws_file and draws_file.filename:
                draws_path = tmp / "draws.pdf"
                draws_file.save(str(draws_path))
                draws = extract_draws(str(draws_path))

            swiss_fighters = _load_swiss_fighters(str(reg_path))

            # Optional club filter
            club_filter = request.form.get('club_filter', '').strip()
            if club_filter:
                cf_lower = club_filter.lower()
                swiss_fighters = [
                    f for f in swiss_fighters
                    if cf_lower in f.get('club', '').lower()
                ]
            schedule = extract_schedule(str(sched_path))
            rows = _match(swiss_fighters, schedule, draws)

        if not swiss_fighters:
            flash("No Swiss fighters found in the registrations PDF. "
                  "Check that the file is correct and that the country column "
                  "contains 'SUI', 'Switzerland' or similar.", "warning")

        # ---- persist to cache -----------------------------------------------
        cache_name = request.form.get("cache_name", "").strip() or _cache.default_name()
        slug = _cache.save(
            name=cache_name,
            rows=rows,
            fighter_list=swiss_fighters,
            swiss_count=len(swiss_fighters),
            draws_used=bool(draws),
            club_filter=club_filter,
        )
        # -----------------------------------------------------------------------

        return render_template(
            "result.html",
            rows=rows,
            swiss_count=len(swiss_fighters),
            fighter_list=swiss_fighters,
            draws_used=bool(draws),
            draws_count=len(draws),
            schedule_count=len(schedule),
            cache_name=cache_name,
            cache_slug=slug,
            club_filter=club_filter,
        )

    except Exception:
        flash(f"Error processing PDFs: {traceback.format_exc()}", "error")
        return redirect(url_for("index"))


@app.route("/cache/<slug>", methods=["GET"])
def load_cache(slug):
    entry = _cache.load(slug)
    if entry is None:
        flash(f"Cache '{slug}' not found.", "error")
        return redirect(url_for("index"))
    return render_template(
        "result.html",
        rows=entry["rows"],
        swiss_count=entry["swiss_count"],
        fighter_list=entry["fighter_list"],
        draws_used=entry["draws_used"],
        cache_name=entry["name"],
        cache_slug=slug,
        cache_created=entry.get("created", ""),
        club_filter=entry.get("club_filter", ""),
    )


@app.route("/cache/<slug>/delete", methods=["POST"])
def delete_cache(slug):
    _cache.delete(slug)
    flash(f"Cache deleted.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
