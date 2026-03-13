"""
parse_schedule.py

Extracts the fighting schedule from the sportdata schedule PDF.
Returns a list of dicts:
  {time, tatami, category, fighter_1, fighter_2, fight_number}

The schedule PDF typically looks like:

  Tatami 1        Tatami 2        Tatami 3
  09:00  Cat A    09:00  Cat B    09:00  Cat C
         Name1           Name3           Name5
         Name2           Name4           Name6
  09:05  ...
"""

import pdfplumber
import re
from datetime import datetime


_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
_TATAMI_RE = re.compile(r"(tatami|ring|mat|kampffl[äa]che)\s*(\d+)", re.IGNORECASE)


def extract_schedule(pdf_path: str) -> list[dict]:
    """Parse the schedule PDF and return a flat list of fights."""
    fights = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    fights.extend(_parse_schedule_table(table))
            if not fights:
                fights.extend(_parse_schedule_text(page))
    # Deduplicate and sort
    fights = _deduplicate(fights)
    fights.sort(key=_sort_key)
    return fights


# ---------------------------------------------------------------------------
# Table-based parsing
# ---------------------------------------------------------------------------

def _parse_schedule_table(table: list[list]) -> list[dict]:
    fights = []
    if not table:
        return fights

    # Detect header row — look for tatami/ring keywords or time patterns
    header_row_idx = 0
    tatami_cols = {}  # col_index -> tatami_name

    for i, row in enumerate(table):
        cells = [str(c).strip() if c else "" for c in row]
        # Look for Tatami headers
        for j, cell in enumerate(cells):
            m = _TATAMI_RE.search(cell)
            if m:
                tatami_cols[j] = f"Tatami {m.group(2)}"
        if tatami_cols:
            header_row_idx = i
            break

    if not tatami_cols:
        # Try generic: assume multi-column layout with time in first col
        return _parse_schedule_table_generic(table)

    current_time = None
    current_category = None
    current_fight: dict | None = None

    for row in table[header_row_idx + 1:]:
        cells = [str(c).strip() if c else "" for c in row]
        if not any(cells):
            continue

        # Check for a time in the row
        time_match = _TIME_RE.search(cells[0]) if cells else None
        if time_match:
            current_time = time_match.group(1)

        for col_idx, tatami in tatami_cols.items():
            if col_idx >= len(cells):
                continue
            cell = cells[col_idx]
            if not cell:
                continue

            tm = _TIME_RE.search(cell)
            if tm:
                current_time = tm.group(1)
                category_text = _TIME_RE.sub("", cell).strip()
                if category_text:
                    current_category = category_text
                current_fight = {
                    "time": current_time,
                    "tatami": tatami,
                    "category": current_category or "",
                    "fighter_1": "",
                    "fighter_2": "",
                }
                fights.append(current_fight)
            elif current_fight and current_fight["tatami"] == tatami:
                if not current_fight["fighter_1"]:
                    current_fight["fighter_1"] = cell
                elif not current_fight["fighter_2"]:
                    current_fight["fighter_2"] = cell

    return fights


def _parse_schedule_table_generic(table: list[list]) -> list[dict]:
    """
    Fallback for tables without explicit Tatami column headers.
    Expects columns: Fight# | Time | Tatami | Category | Fighter1 | Fighter2
    """
    fights = []
    if not table or len(table) < 2:
        return fights

    # Detect column mapping from first row
    header = [str(c).strip().lower() if c else "" for c in table[0]]
    col = {}
    keywords = {
        "time": ["time", "zeit", "uhrzeit"],
        "tatami": ["tatami", "ring", "mat", "floor"],
        "category": ["category", "kategorie", "discipline", "class"],
        "fighter_1": ["fighter 1", "red", "rot", "athlete 1", "name 1"],
        "fighter_2": ["fighter 2", "blue", "blau", "athlete 2", "name 2"],
    }
    for field, kws in keywords.items():
        for i, h in enumerate(header):
            if any(kw in h for kw in kws):
                col[field] = i
                break

    if "time" not in col:
        return fights

    for row in table[1:]:
        cells = [str(c).strip() if c else "" for c in row]
        if not any(cells):
            continue
        fight = {
            "time": cells[col["time"]] if "time" in col and col["time"] < len(cells) else "",
            "tatami": cells[col["tatami"]] if "tatami" in col and col["tatami"] < len(cells) else "",
            "category": cells[col["category"]] if "category" in col and col["category"] < len(cells) else "",
            "fighter_1": cells[col["fighter_1"]] if "fighter_1" in col and col["fighter_1"] < len(cells) else "",
            "fighter_2": cells[col["fighter_2"]] if "fighter_2" in col and col["fighter_2"] < len(cells) else "",
        }
        if fight["time"] and fight["fighter_1"]:
            fights.append(fight)
    return fights


# ---------------------------------------------------------------------------
# Text-based parsing fallback
# ---------------------------------------------------------------------------

def _parse_schedule_text(page) -> list[dict]:
    """
    Fallback: extract words with bounding boxes and reconstruct columns.
    Works well for multi-column schedule layouts that pdfplumber can't table-parse.
    """
    fights = []
    words = page.extract_words(keep_blank_chars=False)
    if not words:
        return fights

    # Group words into lines (same y-position ± 3 pts)
    lines: dict[int, list] = {}
    for w in words:
        y = round(w["top"] / 3) * 3
        lines.setdefault(y, []).append(w)

    # Detect tatami column x-positions from "Tatami N" headers
    tatami_x_map: dict[float, str] = {}  # x_center -> name
    for y_key in sorted(lines):
        row_words = lines[y_key]
        row_text = " ".join(w["text"] for w in row_words)
        if _TATAMI_RE.search(row_text):
            for i, w in enumerate(row_words):
                m = _TATAMI_RE.search(w["text"])
                if m:
                    x_center = (w["x0"] + w["x1"]) / 2
                    tatami_x_map[x_center] = f"Tatami {m.group(2)}"

    if not tatami_x_map:
        # No tatami headers — try simple line scanning
        return _parse_schedule_text_simple(page)

    tatami_xs = sorted(tatami_x_map)
    col_width = min(
        tatami_xs[i + 1] - tatami_xs[i] for i in range(len(tatami_xs) - 1)
    ) if len(tatami_xs) > 1 else 200

    def closest_tatami(x: float) -> str:
        best = min(tatami_xs, key=lambda tx: abs(tx - x))
        if abs(best - x) < col_width * 0.6:
            return tatami_x_map[best]
        return ""

    current_time = {}  # tatami -> time
    current_cat = {}   # tatami -> category
    current_f1 = {}    # tatami -> fighter_1

    for y_key in sorted(lines):
        row_words = sorted(lines[y_key], key=lambda w: w["x0"])
        row_text = " ".join(w["text"] for w in row_words)
        if _TATAMI_RE.search(row_text):
            continue  # header row

        # Group words by tatami column
        col_words: dict[str, list[str]] = {}
        for w in row_words:
            x = (w["x0"] + w["x1"]) / 2
            tatami = closest_tatami(x)
            if tatami:
                col_words.setdefault(tatami, []).append(w["text"])

        for tatami, wds in col_words.items():
            cell = " ".join(wds).strip()
            if not cell:
                continue
            tm = _TIME_RE.search(cell)
            if tm:
                current_time[tatami] = tm.group(1)
                cat = _TIME_RE.sub("", cell).strip()
                if cat:
                    current_cat[tatami] = cat
                current_f1[tatami] = ""
            elif tatami in current_time:
                if not current_f1.get(tatami):
                    current_f1[tatami] = cell
                else:
                    fights.append({
                        "time": current_time[tatami],
                        "tatami": tatami,
                        "category": current_cat.get(tatami, ""),
                        "fighter_1": current_f1[tatami],
                        "fighter_2": cell,
                    })
                    current_f1[tatami] = ""

    return fights


def _parse_schedule_text_simple(page) -> list[dict]:
    """Last-resort: scan lines for time + fighter pattern."""
    text = page.extract_text() or ""
    fights = []
    pattern = re.compile(
        r"(?P<time>\d{1,2}:\d{2})\s+"
        r"(?P<tatami>Tatami\s*\d+|Ring\s*\d+|Mat\s*\d+)\s+"
        r"(?P<category>[^\n]+?)\s{2,}"
        r"(?P<fighter_1>[A-Za-zÀ-ÿ ,\-]+?)\s{2,}"
        r"(?P<fighter_2>[A-Za-zÀ-ÿ ,\-]+)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        fights.append({
            "time": m.group("time"),
            "tatami": m.group("tatami").strip(),
            "category": m.group("category").strip(),
            "fighter_1": m.group("fighter_1").strip(),
            "fighter_2": m.group("fighter_2").strip(),
        })
    return fights


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deduplicate(fights: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for f in fights:
        key = (f["time"], f["tatami"], f["fighter_1"], f["fighter_2"])
        if key not in seen and f["time"]:
            seen.add(key)
            out.append(f)
    return out


def _sort_key(fight: dict):
    try:
        return datetime.strptime(fight["time"], "%H:%M")
    except ValueError:
        return datetime.max
