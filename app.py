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


def _match(fighters: list[dict], schedule: list[dict]) -> list[dict]:
    """
    Cross-reference Swiss fighters with the schedule.

    For each fight in the schedule, check if either fighter_1 or fighter_2
    matches a Swiss fighter (by name substring, case-insensitive) OR if the
    fight's category matches a category in which a Swiss fighter is registered.

    Returns a flat list of rows ready for display.
    """
    swiss_names_lower = {f["name"].lower() for f in fighters}
    swiss_cats_lower = {f["category"].lower() for f in fighters}

    # Build a lookup: category_lower -> list of Swiss fighters
    cat_to_swiss: dict[str, list[dict]] = {}
    for f in fighters:
        cat_to_swiss.setdefault(f["category"].lower(), []).append(f)

    rows = []
    for fight in schedule:
        cat_lower = fight["category"].lower()
        f1_lower = fight["fighter_1"].lower()
        f2_lower = fight["fighter_2"].lower()

        matched_fighters: list[dict] = []

        # Method 1: direct name match
        for swiss in fighters:
            sname = swiss["name"].lower()
            if sname in f1_lower or f1_lower in sname:
                matched_fighters.append({**swiss, "corner": "Red"})
            elif sname in f2_lower or f2_lower in sname:
                matched_fighters.append({**swiss, "corner": "Blue"})

        # Method 2: category match (covers cases where names differ slightly)
        if not matched_fighters:
            any_name_match = any(
                sn in f1_lower or sn in f2_lower or
                f1_lower in sn or f2_lower in sn
                for sn in swiss_names_lower
            )
            if not any_name_match and cat_lower in swiss_cats_lower:
                for swiss in cat_to_swiss.get(cat_lower, []):
                    matched_fighters.append({**swiss, "corner": "?"})

        for mf in matched_fighters:
            rows.append({
                "time": fight["time"],
                "tatami": fight["tatami"],
                "category": fight["category"] or mf["category"],
                "name": mf["name"],
                "club": mf.get("club", ""),
                "corner": mf.get("corner", ""),
                "opponent": (
                    fight["fighter_2"] if mf.get("corner") == "Red"
                    else fight["fighter_1"]
                ),
            })

    # Deduplicate by (time, tatami, name)
    seen = set()
    unique = []
    for r in rows:
        key = (r["time"], r["tatami"], r["name"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort chronologically
    from datetime import datetime

    def sort_key(r):
        try:
            return datetime.strptime(r["time"], "%H:%M")
        except ValueError:
            return datetime.max

    unique.sort(key=sort_key)
    return unique


# --- routes ----------------------------------------------------------------


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


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
