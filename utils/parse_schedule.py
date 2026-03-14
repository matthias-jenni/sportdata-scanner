"""
parse_schedule.py

Parses the sportdata schedule PDF.

Actual format (one page = one tatami):
  Row 0: Event title
  Row 1: TatamisXX  (e.g. "Tatami01")
  Row 2: Header: Time | # | | Match | Info
  Row 3+: data rows, e.g.
    ['09:00 - 10:04', '1', '', '01 PF 034 OC M -37 kg (10)\nPool 1/2', '']

Returns a list of:
  {time, time_end, tatami, category_code, category_display, phase, fight_no}
"""

import re
import pdfplumber
from datetime import datetime

_TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-\u2013]\s*(\d{1,2}:\d{2})")
_TATAMI_RE     = re.compile(r"tatami\s*0*(\d+)", re.IGNORECASE)
_AREA_NUM_RE   = re.compile(r"^\d+$")   # bare area number like "1", "2", "12"
# Strip trailing "(N)" count from category codes like "01 PF 034 OC M -37 kg (10)"
_COUNT_RE      = re.compile(r"\s*\(\d+\)\s*$")


def extract_schedule(pdf_path: str) -> list[dict]:
    fights = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            fights.extend(_parse_page(page))
    fights.sort(key=_sort_key)
    return fights


def _parse_page(page) -> list[dict]:
    tables = page.extract_tables()
    if not tables:
        return []

    table = tables[0]  # one table per page

    # --- detect tatami from first few rows (any cell) ---
    tatami = ""
    for row in table[:4]:
        for cell in row:
            m = _TATAMI_RE.search(str(cell or ""))
            if m:
                tatami = f"Tatami {int(m.group(1)):02d}"
                break
        if tatami:
            break

    # --- fallback: scan page text outside the table ---
    if not tatami:
        page_text = page.extract_text() or ""
        m = _TATAMI_RE.search(page_text)
        if m:
            tatami = f"Tatami {int(m.group(1)):02d}"

    # --- fallback: new "Area" format — number is in column 1 of data rows ---
    if not tatami:
        for row in table:
            if not row or len(row) < 2:
                continue
            area = str(row[1] or "").strip()
            if _AREA_NUM_RE.match(area) and _TIME_RANGE_RE.search(str(row[0] or "")):
                tatami = f"Tatami {int(area):02d}"
                break

    fights = []
    for row in table:
        if not row or len(row) < 4:
            continue

        time_cell  = str(row[0] or "").strip()
        match_cell = str(row[3] or "").strip() if len(row) > 3 else ""

        # Must have a time range in column 0
        tm = _TIME_RANGE_RE.search(time_cell)
        if not tm:
            continue

        time_start = tm.group(1)   # e.g. "09:00"
        time_end   = tm.group(2)   # e.g. "10:04"

        # match_cell may be "01 PF 034 OC M -37 kg (10)\nPool 1/2"
        # Split on newline: first part = category + count, rest = phase
        parts = match_cell.split("\n", 1)
        raw_cat = parts[0].strip()
        phase   = parts[1].strip() if len(parts) > 1 else ""

        # Strip participant count "(10)" from end of category
        category_code = _COUNT_RE.sub("", raw_cat).strip()

        if not category_code or category_code == "-":
            continue

        fights.append({
            "time":             time_start,
            "time_end":         time_end,
            "tatami":           tatami,
            "category_code":    category_code,   # e.g. "01 PF 034 OC M -37 kg"
            "category_display": category_code,
            "phase":            phase,            # e.g. "Pool 1/2", "Final"
            "fight_no":         str(row[1] or "").strip(),
        })

    return fights


def _sort_key(fight: dict):
    try:
        return datetime.strptime(fight["time"], "%H:%M")
    except ValueError:
        return datetime.max
