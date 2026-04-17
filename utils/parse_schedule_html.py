"""
parse_schedule_html.py

Parses the sportdata timetable page saved as HTML
(File → Save Page As from your browser).

The page structure (one <table class="moduletable">):
  - Date header row: <tr><th colspan="N" id="YYYY-MM-DD">…</th></tr>
  - Ring header row: <tr><th></th><th class="thcenter">Ring 1</th> …</tr>
  - Time rows: <tr><td>10:30</td> <td rowspan="2" title="01 PF 034 …">
                 <b>01 PF 034 …</b><br>10:30 - 10:40 (00:10)<br>… </td> …</tr>

Returns a list of dicts (identical shape to parse_schedule.extract_schedule):
  {time, time_end, tatami, category_code, category_display, phase, fight_no}

Notes:
  - `phase` and `fight_no` are always "" – the HTML timetable does not carry
    pool/round information.
  - Tatami labels are taken directly from the column headers (e.g. "Ring 1").
  - `rowspan` is tracked with a virtual grid so column offsets stay correct
    when cells span multiple time-rows.
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

# Matches a category code: digits, 2-letter discipline, digits
# e.g. "01 PF 034 OC M -37 kg" or "02 LC 1129 S M -63 kg"
_CAT_CODE_RE = re.compile(r"\d+\s+[A-Z]{2}\s+\d+", re.IGNORECASE)

# Matches "HH:MM - HH:MM" inside cell text
_TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")

# Matches a bare time "HH:MM" in the first <td> of a time row
_TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")


def extract_schedule_html(html_path: str) -> list[dict]:
    """Return schedule slots from a saved sportdata timetable HTML page."""
    with open(html_path, encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh, "html.parser")

    table = soup.find("table", class_="moduletable")
    if table is None:
        return []

    fights: list[dict] = []

    # ring_names[col_index] = display name, e.g. "Ring 1"
    # Re-detected each time a ring-header row is found (supports multi-day HTML)
    ring_names: dict[int, str] = {}

    # Virtual grid: tracks how many more rows each column is still occupied by
    # a rowspan from a previously-seen cell.
    # grid[col_index] = (remaining_extra_rows, td_element)
    grid: dict[int, tuple[int, Tag]] = {}

    current_time: str = ""

    rows = table.find_all("tr", recursive=False)
    # tbody is sometimes present
    if not rows:
        tbody = table.find("tbody")
        if tbody:
            rows = tbody.find_all("tr", recursive=False)

    for tr in rows:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue

        # ------------------------------------------------------------------ #
        # 1. Ring-header row detection                                        #
        # ------------------------------------------------------------------ #
        thcenter_cells = [c for c in cells if c.name == "th" and "thcenter" in c.get("class", [])]
        if thcenter_cells:
            ring_names = {}
            col = 0
            for cell in cells:
                if cell.name == "th" and "thcenter" in cell.get("class", []):
                    ring_names[col] = cell.get_text(strip=True)
                col += 1
            # Reset the virtual grid for the new day/section
            grid = {}
            current_time = ""
            continue

        # ------------------------------------------------------------------ #
        # 2. Date / metadata header rows – skip                               #
        # ------------------------------------------------------------------ #
        if cells and cells[0].name == "th":
            continue

        # ------------------------------------------------------------------ #
        # 3. Time + fight rows                                                #
        # ------------------------------------------------------------------ #
        if not ring_names:
            # Haven't seen a ring header yet
            continue

        # Build the "resolved" column list by merging real cells with virtual
        # cells carried over from rowspans in earlier rows.
        # `resolved[col_index]` = the <td> that owns this column for this row.
        # `new_cols` = column indices where a brand-new cell appears this row;
        #   carried-over rowspan cells are excluded so we don't emit duplicates.
        resolved: dict[int, Tag] = {}
        new_cols: set[int] = set()

        # First pass: expose carried-over rowspan cells (not new this row)
        for col, (remaining, td) in list(grid.items()):
            resolved[col] = td
            if remaining <= 1:
                del grid[col]
            else:
                grid[col] = (remaining - 1, td)

        # Second pass: place actual cells from this row into free columns
        real_col_cursor = 0
        for cell in cells:
            # Skip to the next free column
            while real_col_cursor in resolved:
                real_col_cursor += 1
            resolved[real_col_cursor] = cell
            new_cols.add(real_col_cursor)  # only emit fights for new cells

            # Register rowspan for subsequent rows
            rs = int(cell.get("rowspan", 1))
            if rs > 1:
                grid[real_col_cursor] = (rs - 1, cell)

            cs = int(cell.get("colspan", 1))
            real_col_cursor += cs

        # Column 0 is the time label
        time_cell = resolved.get(0)
        if time_cell is not None:
            m = _TIME_RE.match(time_cell.get_text())
            if m:
                current_time = m.group(1)

        if not current_time:
            continue

        # Columns 1…N are ring slots — only emit for brand-new cells
        for col_idx, ring_name in ring_names.items():
            if col_idx not in new_cols:
                continue  # carried-over rowspan; already emitted on first row
            td = resolved.get(col_idx)
            if td is None:
                continue

            title = (td.get("title") or "").strip()
            # A fight cell has a title matching a category code pattern
            if not _CAT_CODE_RE.search(title):
                continue

            # The title IS the category code (sportdata puts the full code there)
            category_code = title

            # Extract time_end from inline text "HH:MM - HH:MM (HH:MM)"
            cell_text = td.get_text(separator=" ")
            tm = _TIME_RANGE_RE.search(cell_text)
            time_end = tm.group(2) if tm else ""

            fights.append({
                "time":             current_time,
                "time_end":         time_end,
                "tatami":           ring_name,
                "category_code":    category_code,
                "category_display": category_code,
                "phase":            "",
                "fight_no":         "",
            })

    fights.sort(key=_sort_key)
    return fights


def _sort_key(fight: dict):
    try:
        return datetime.strptime(fight["time"], "%H:%M")
    except ValueError:
        return datetime.max
