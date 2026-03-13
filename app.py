"""
app.py – Sportdata Swiss Fighter Timetable
==========================================
Upload two PDFs (registrations + schedule) and get back a
chronological timetable of all Swiss fighters.
"""

import io
import os
import traceback

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
    session,
)

from utils.parse_registrations import get_swiss_fighters
from utils.parse_schedule import extract_schedule

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# --- helper ----------------------------------------------------------------


import re as _re
_CAT_NUM_RE = _re.compile(r"\d{2}\s+([A-Z]{2})\s+(\d{3})", _re.IGNORECASE)


def _cat_key(s: str):
    """
    Extract the unique category key: (discipline_prefix, 3-digit-number).
    E.g. '01 PF 034 OC M -37 KG'  ->  ('pf', '034')
         '02 LC 109 OC F -55 kg'  ->  ('lc', '109')
    Returns None if not parseable.
    """
    m = _CAT_NUM_RE.search(s)
    if m:
        return (m.group(1).lower(), m.group(2))
    return None


def _cats_match(reg_cat: str, sched_code: str) -> bool:
    """Match by exact (discipline, 3-digit-number) key."""
    a = _cat_key(reg_cat)
    b = _cat_key(sched_code)
    return a is not None and b is not None and a == b


def _match(fighters: list[dict], schedule: list[dict]) -> list[dict]:
    """
    The schedule has NO individual fighter names — only category codes per
    time-slot. We match Swiss fighters by the (discipline, 3-digit-number)
    key extracted from their category code.
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

        for sw in matched_swiss:
            rows.append({
                "time":     slot["time"],
                "time_end": slot.get("time_end", ""),
                "tatami":   slot["tatami"],
                "category": sched_code,
                "phase":    slot.get("phase", ""),
                "name":     sw["name"],
                "club":     sw.get("club", ""),
                "country":  sw.get("country", ""),
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
    return render_template("index.html")


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
        reg_path = tmp / "registrations.pdf"
        sched_path = tmp / "schedule.pdf"
        reg_file.save(str(reg_path))
        sched_file.save(str(sched_path))
        from utils.parse_registrations import extract_fighters, get_swiss_fighters
        from utils.parse_schedule import extract_schedule
        all_fighters = extract_fighters(str(reg_path))
        swiss = get_swiss_fighters(str(reg_path))
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
            reg_path = tmp / "registrations.pdf"
            sched_path = tmp / "schedule.pdf"
            reg_file.save(str(reg_path))
            sched_file.save(str(sched_path))

            swiss_fighters = get_swiss_fighters(str(reg_path))
            schedule = extract_schedule(str(sched_path))
            rows = _match(swiss_fighters, schedule)

        if not swiss_fighters:
            flash("No Swiss fighters found in the registrations PDF. "
                  "Check that the file is correct and that the country column "
                  "contains 'SUI', 'Switzerland' or similar.", "warning")

        return render_template(
            "result.html",
            rows=rows,
            swiss_count=len(swiss_fighters),
            fighter_list=swiss_fighters,
        )

    except Exception:
        flash(f"Error processing PDFs: {traceback.format_exc()}", "error")
        return redirect(url_for("index"))


@app.route("/download-pdf", methods=["POST"])
def download_pdf():
    """Render the result HTML to a PDF and stream it back."""
    try:
        import json
        from weasyprint import HTML

        rows_json = request.form.get("rows_json", "[]")
        swiss_count = int(request.form.get("swiss_count", 0))
        fighter_list_json = request.form.get("fighter_list_json", "[]")

        rows = json.loads(rows_json)
        fighter_list = json.loads(fighter_list_json)

        html_str = render_template(
            "result.html",
            rows=rows,
            swiss_count=swiss_count,
            fighter_list=fighter_list,
            pdf_mode=True,
        )
        pdf_bytes = HTML(string=html_str).write_pdf()
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="swiss_timetable.pdf",
        )
    except Exception:
        flash(f"PDF export failed: {traceback.format_exc()}", "error")
        return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
