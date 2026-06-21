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

# Matches a category code: digits, 2-char discipline token, digits
# e.g. "01 PF 034 OC M -37 kg", "07 K1 403 YJ F -56 kg"
_CAT_CODE_RE = re.compile(r"\d+\s+[A-Z0-9]{2}\s+\d+", re.IGNORECASE)

# Matches "HH:MM - HH:MM" inside cell text
_TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")

# Matches a bare time "HH:MM" in the first <td> of a time row
_TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")


def extract_schedule_html(html_path: str) -> list[dict]:
    """Return schedule slots from a saved sportdata timetable HTML page."""
    with open(html_path, encoding="utf-8", errors="replace") as fh:
        soup = BeautifulSoup(fh, "html.parser")

    # New timetable structure used by newer sportdata pages.
    if _looks_like_new_schedule_html(soup):
        fights = _extract_schedule_html_new(soup)
        if fights:
            return fights

    # Fallback for legacy saved timetable pages.
    return _extract_schedule_html_legacy(soup)


def _looks_like_new_schedule_html(soup: BeautifulSoup) -> bool:
    table = soup.find("table", class_="schedule")
    if table is None:
        return False
    return table.find("td", class_="time-cell") is not None


def _extract_schedule_html_new(soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table", class_="schedule")
    if table is None:
        return []

    fights: list[dict] = []
    ring_names = _extract_new_ring_names(table)

    if not ring_names:
        return []

    grid: dict[int, tuple[int, Tag]] = {}
    current_time: str = ""

    tbody = table.find("tbody")
    rows = tbody.find_all("tr", recursive=False) if tbody else table.find_all("tr", recursive=False)

    for tr in rows:
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue

        resolved: dict[int, Tag] = {}
        new_cols: set[int] = set()

        for col, (remaining, td) in list(grid.items()):
            resolved[col] = td
            if remaining <= 1:
                del grid[col]
            else:
                grid[col] = (remaining - 1, td)

        real_col_cursor = 0
        for cell in cells:
            while real_col_cursor in resolved:
                real_col_cursor += 1

            resolved[real_col_cursor] = cell
            new_cols.add(real_col_cursor)

            rs = int(cell.get("rowspan", 1))
            if rs > 1:
                grid[real_col_cursor] = (rs - 1, cell)

            cs = int(cell.get("colspan", 1))
            real_col_cursor += cs

        time_cell = resolved.get(0)
        if time_cell is not None:
            m = _TIME_RE.match(time_cell.get_text(strip=True))
            if m:
                current_time = m.group(1)

        if not current_time:
            continue

        for col_idx, ring_name in ring_names.items():
            if col_idx not in new_cols:
                continue

            td = resolved.get(col_idx)
            if td is None:
                continue

            category_code = _extract_category_from_new_cell(td)
            if not category_code:
                continue

            time_end = _extract_new_time_end(td)
            fights.append({
                "time": current_time,
                "time_end": time_end,
                "tatami": ring_name,
                "category_code": category_code,
                "category_display": category_code,
                "phase": "",
                "fight_no": "",
            })

    fights.sort(key=_sort_key)
    return fights


def _extract_new_ring_names(table: Tag) -> dict[int, str]:
    ring_names: dict[int, str] = {}
    thead = table.find("thead")
    if thead is None:
        return ring_names

    header_row = thead.find("tr")
    if header_row is None:
        return ring_names

    col = 0
    for cell in header_row.find_all("th", recursive=False):
        text = cell.get_text(strip=True)
        cs = int(cell.get("colspan", 1))
        if col > 0 and text:
            ring_names[col] = text
        col += cs

    return ring_names


def _extract_category_from_new_cell(td: Tag) -> str:
    title = td.find("div", class_="match-title")
    if title is None:
        return ""

    category = title.get_text(" ", strip=True)
    if not category:
        return ""
    if not _CAT_CODE_RE.search(category):
        return ""
    return category


def _extract_new_time_end(td: Tag) -> str:
    info = td.find("div", class_="cell-info")
    if info is None:
        return ""

    info_text = info.get_text(" ", strip=True)
    if not info_text:
        return ""

    # New format is typically: "MM:SS · HH:MM · HH:MM".
    parts = [p.strip() for p in info_text.split("·") if p.strip()]
    if parts:
        m = _TIME_RE.match(parts[-1])
        if m:
            return m.group(1)

    # Fallback when separators vary.
    times = re.findall(r"\b\d{1,2}:\d{2}\b", info_text)
    if len(times) >= 2:
        return times[-1]
    return ""


def _extract_schedule_html_legacy(soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table", class_="moduletable")
    if table is None:
        return []

    fights: list[dict] = []

    ring_names: dict[int, str] = {}
    grid: dict[int, tuple[int, Tag]] = {}
    current_time: str = ""

    rows = table.find_all("tr", recursive=False)
    if not rows:
        tbody = table.find("tbody")
        if tbody:
            rows = tbody.find_all("tr", recursive=False)

    for tr in rows:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue

        thcenter_cells = [c for c in cells if c.name == "th" and "thcenter" in c.get("class", [])]
        if thcenter_cells:
            ring_names = {}
            col = 0
            for cell in cells:
                if cell.name == "th" and "thcenter" in cell.get("class", []):
                    ring_names[col] = cell.get_text(strip=True)
                col += 1
            grid = {}
            current_time = ""
            continue

        if cells and cells[0].name == "th":
            continue

        if not ring_names:
            continue

        resolved: dict[int, Tag] = {}
        new_cols: set[int] = set()

        for col, (remaining, td) in list(grid.items()):
            resolved[col] = td
            if remaining <= 1:
                del grid[col]
            else:
                grid[col] = (remaining - 1, td)

        real_col_cursor = 0
        for cell in cells:
            while real_col_cursor in resolved:
                real_col_cursor += 1
            resolved[real_col_cursor] = cell
            new_cols.add(real_col_cursor)

            rs = int(cell.get("rowspan", 1))
            if rs > 1:
                grid[real_col_cursor] = (rs - 1, cell)

            cs = int(cell.get("colspan", 1))
            real_col_cursor += cs

        time_cell = resolved.get(0)
        if time_cell is not None:
            m = _TIME_RE.match(time_cell.get_text())
            if m:
                current_time = m.group(1)

        if not current_time:
            continue

        for col_idx, ring_name in ring_names.items():
            if col_idx not in new_cols:
                continue
            td = resolved.get(col_idx)
            if td is None:
                continue

            title = (td.get("title") or "").strip()
            if not _CAT_CODE_RE.search(title):
                continue

            category_code = title
            cell_text = td.get_text(separator=" ")
            tm = _TIME_RANGE_RE.search(cell_text)
            time_end = tm.group(2) if tm else ""

            fights.append({
                "time": current_time,
                "time_end": time_end,
                "tatami": ring_name,
                "category_code": category_code,
                "category_display": category_code,
                "phase": "",
                "fight_no": "",
            })

    fights.sort(key=_sort_key)
    return fights


def _sort_key(fight: dict):
    try:
        return datetime.strptime(fight["time"], "%H:%M")
    except ValueError:
        return datetime.max
