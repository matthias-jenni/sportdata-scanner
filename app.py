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

from utils.parse_registrations import get_fighters
from utils.parse_registrations_html import get_fighters_html
from utils.parse_schedule import extract_schedule
from utils.parse_schedule_html import extract_schedule_html
from utils.parse_draws import extract_draws, pool_for_fighter
from utils.flags import get_flag_emoji
from utils import cache as _cache


def _load_fighters(path: str, country_filter: str) -> list[dict]:
    """Dispatch to the correct parser based on file extension."""
    if path.lower().endswith(('.html', '.htm')):
        return get_fighters_html(path, country_filter)
    return get_fighters(path, country_filter)


def _load_schedule(path: str) -> list[dict]:
    """Dispatch to the correct schedule parser based on file extension."""
    if path.lower().endswith(('.html', '.htm')):
        return extract_schedule_html(path)
    return extract_schedule(path)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

@app.template_filter('flag')
def flag_filter(country_code):
    return get_flag_emoji(country_code)

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


from utils import storage as _storage
from flask import send_from_directory

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.svg', mimetype='image/svg+xml')

@app.route("/", methods=["GET"])
def index():
    events = _storage.get_events()
    return render_template("index.html", events=events)

@app.route("/events/new", methods=["GET", "POST"])
def new_event():
    if request.method == "POST":
        name = request.form.get("event_name", "").strip()
        reg_file = request.files.get("registrations")
        
        if not name:
            flash("Event name is required.", "error")
            return redirect(url_for("new_event"))
        if not reg_file or not reg_file.filename:
            flash("Please upload the registrations PDF/HTML.", "error")
            return redirect(url_for("new_event"))
            
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            ext = _re.sub(r'.*\.', '.', reg_file.filename.lower()) or '.pdf'
            reg_path = tmp / f"registrations{ext}"
            reg_file.save(str(reg_path))
            
            try:
                # Load all fighters (empty filter)
                all_fighters = _load_fighters(str(reg_path), "")
                event_id = _storage.create_event(name, all_fighters, filename=reg_file.filename)
                flash("Event created successfully.", "success")
                return redirect(url_for("event_admin", event_id=event_id))
            except Exception as e:
                flash(f"Error processing files: {str(e)}", "error")
                return redirect(url_for("new_event"))
                
    return render_template("event_new.html")

@app.route("/events/<event_id>", methods=["GET"])
def event_admin(event_id):
    event = _storage.get_event(event_id)
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("index"))
    return render_template("event_admin.html", event=event)

@app.route("/events/<event_id>/upload-day", methods=["POST"])
def upload_day(event_id):
    event = _storage.get_event(event_id)
    if not event:
        return "Event not found", 404
        
    day_name = request.form.get("day_name", "").strip()
    day_type = request.form.get("day_type", "categories")
    sched_files = [f for f in request.files.getlist("schedule") if f and f.filename]
    draws_file = request.files.get("draws")  # only for categories
    
    if not day_name:
        flash("Day name is required.", "error")
        return redirect(url_for("event_admin", event_id=event_id))
    if not sched_files:
        flash("Please upload at least one schedule file.", "error")
        return redirect(url_for("event_admin", event_id=event_id))
        
    import tempfile, pathlib
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            sched_path = None
            if day_type != "ring-cards":
                sched_file = sched_files[0]
                ext = _re.sub(r'.*\.', '.', sched_file.filename.lower()) or '.pdf'
                sched_path = tmp / f"schedule{ext}"
                sched_file.save(str(sched_path))
            
            raw_draws = None
            if day_type == "categories" and draws_file and draws_file.filename:
                extd = _re.sub(r'.*\.', '.', draws_file.filename.lower()) or '.pdf'
                draws_path = tmp / f"draws{extd}"
                draws_file.save(str(draws_path))
                raw_draws = extract_draws(str(draws_path))
                
            if day_type == "categories":
                schedule = _load_schedule(str(sched_path))
                _storage.add_event_day(event_id, day_name, day_type, rows=schedule, raw_draws=raw_draws)
            elif day_type == "ring-cards":
                from utils.parse_ring_schedule import extract_ring_fights
                ring_fights = []
                for idx, rf in enumerate(sched_files):
                    exts = _re.sub(r'.*\.', '.', rf.filename.lower()) or '.pdf'
                    sched_path_multi = tmp / f"schedule_{idx}{exts}"
                    rf.save(str(sched_path_multi))
                    parsed = extract_ring_fights(str(sched_path_multi))
                    ring_fights.extend(parsed)
                print("Extracted fights:", len(ring_fights))
                _storage.add_event_day(event_id, day_name, day_type, rows=ring_fights)
            elif day_type == "results":
                from utils.parse_results import extract_results_html
                results_data = extract_results_html(str(sched_path))
                _storage.add_event_day(event_id, day_name, day_type, rows=results_data)
                
            flash(f"Day '{day_name}' uploaded successfully.", "success")
            return redirect(url_for("event_admin", event_id=event_id))
    except Exception as e:
        flash(f"Error processing schedule: {str(e)}", "error")
        return redirect(url_for("event_admin", event_id=event_id))


@app.route("/events/<event_id>/days/<day_id>/delete", methods=["POST"])
def delete_day(event_id, day_id):
    if not _storage.get_event(event_id):
        return "Event not found", 404
        
    try:
        _storage.delete_event_day(event_id, day_id)
        flash("Day deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting day: {str(e)}", "error")
        
    return redirect(url_for("event_admin", event_id=event_id))

@app.route("/share/<event_id>", methods=["GET"])

def event_public(event_id):
    event = _storage.get_event(event_id)
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("index"))
    return render_template("event_public.html", event=event)

@app.route("/share/<event_id>/day/<day_id>", methods=["GET"])
def share_day(event_id, day_id):
    event = _storage.get_event(event_id)
    day = _storage.get_event_day(event_id, day_id)
    if not event or not day:
        return "Event or Day not found", 404
        
    country_filter = request.args.get("country", "").strip()
    club_filter = request.args.get("club", "").strip()
    
    all_fighters = _storage.get_event_registrations(event_id)
    team_fighters = []
    for f in all_fighters:
        if country_filter and country_filter.lower() not in f.get("country", "").lower():
            continue
        if club_filter and club_filter.lower() not in f.get("club", "").lower():
            continue
        team_fighters.append(f)
        
    day_type = day.get("type", "categories")
    
    if day_type == "categories":
        draws = day.get("raw_draws")
        schedule = day.get("rows", [])
        matched = _match(team_fighters, schedule, draws)
        return render_template("result.html", 
                               rows=matched, 
                               swiss_count=len(team_fighters), 
                               fighter_list=team_fighters,
                               draws_used=bool(draws),
                               event_name=event["name"],
                               event_id=event["id"],
                               day_name=day["name"],
                               club_filter=club_filter,
                               country_filter=country_filter)
                               
    elif day_type == "ring-cards":
        ring_fights = day.get("rows", [])
        from utils.parse_ring_schedule import find_swiss_fights
        matched = find_swiss_fights(ring_fights, team_fighters, "")
        return render_template("ring_result.html",
                               cards=matched,
                               swiss_count=len(team_fighters),
                               total_fights=len(matched),
                               event_name=event["name"],
                               event_id=event["id"],
                               day_name=day["name"],
                               club_filter=club_filter,
                               country_filter=country_filter)
                               
    elif day_type == "results":
        results_rows = day.get("rows", [])
        medals_only = request.args.get("medals_only", "1") == "1"
        
        filtered_results = []
        for r in results_rows:
            if country_filter and country_filter.lower() not in r.get("country", "").lower():
                continue
            if club_filter and club_filter.lower() not in r.get("club", "").lower():
                continue
            if medals_only and r.get("placement", 99) > 3:
                continue
            filtered_results.append(r)
            
        from collections import defaultdict
        grouped = defaultdict(list)
        for r in filtered_results:
            grouped[r["category_code"]].append(r)
            
        return render_template("day_results.html",
                               grouped_results=dict(grouped),
                               event_name=event["name"],
                               event_id=event["id"],
                               day_name=day["name"],
                               club_filter=club_filter,
                               country_filter=country_filter,
                               medals_only=medals_only)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
