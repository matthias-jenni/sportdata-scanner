"""
parse_registrations.py

Extracts all fighters from the sportdata registrations PDF.
Returns a list of dicts: {name, category, country, club}

The PDF typically has one row per fighter with columns like:
  Nr | Name | Nation | Club | Category | ...

We do a best-effort column detection and also fall back to
keyword scanning so the parser is robust against layout shifts.
"""

import pdfplumber
import re


# Country identifiers that indicate Switzerland
SWISS_IDENTIFIERS = {"sui", "switzerland", "schweiz", "suisse", "svizzera", "ch"}


def _is_swiss(value: str) -> bool:
    return value.strip().lower() in SWISS_IDENTIFIERS


def extract_fighters(pdf_path: str) -> list[dict]:
    """Return a list of all fighters in the PDF."""
    fighters = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    fighters.extend(_parse_table(table))
            else:
                # Fall back: try to detect rows from raw text lines
                fighters.extend(_parse_text_lines(page))
    return fighters


def get_swiss_fighters(pdf_path: str) -> list[dict]:
    """Return only the Swiss fighters, deduped by (name, category)."""
    all_fighters = extract_fighters(pdf_path)
    swiss = [f for f in all_fighters if _is_swiss(f.get("country", ""))]
    # Deduplicate
    seen = set()
    unique = []
    for f in swiss:
        key = (f["name"].lower(), f["category"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_header(h: str) -> str:
    return h.strip().lower().replace(" ", "_") if h else ""


_HEADER_MAP = {
    # Name
    "name": "name",
    "athlete": "name",
    "fighter": "name",
    "vorname_name": "name",
    # Country / Nation
    "nation": "country",
    "country": "country",
    "land": "country",
    "nat": "country",
    # Category
    "category": "category",
    "kategorie": "category",
    "discipline": "category",
    "class": "category",
    "klasse": "category",
    "disziplin": "category",
    # Club
    "club": "club",
    "verein": "club",
    "team": "club",
}


def _map_col(raw: str) -> str | None:
    return _HEADER_MAP.get(_normalise_header(raw))


def _parse_table(table: list[list]) -> list[dict]:
    if not table or len(table) < 2:
        return []

    # Detect header row (first non-empty row)
    header_row = None
    data_start = 0
    for i, row in enumerate(table):
        if any(cell and _map_col(cell) for cell in row if cell):
            header_row = row
            data_start = i + 1
            break

    if header_row is None:
        # No recognisable header — try positional heuristics (see below)
        return _parse_table_positional(table)

    col_index = {}
    for idx, cell in enumerate(header_row):
        if cell:
            mapped = _map_col(cell)
            if mapped and mapped not in col_index:
                col_index[mapped] = idx

    fighters = []
    for row in table[data_start:]:
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        entry = {}
        for field, idx in col_index.items():
            if idx < len(row) and row[idx]:
                entry[field] = str(row[idx]).strip()
            else:
                entry[field] = ""
        if entry.get("name"):
            fighters.append(entry)
    return fighters


def _parse_table_positional(table: list[list]) -> list[dict]:
    """
    Fallback: guess columns by position for tables without recognisable headers.
    Sportdata typically: Nr | Name | Nation | Club | Category
    """
    fighters = []
    for row in table:
        cells = [str(c).strip() if c else "" for c in row]
        if len(cells) < 3:
            continue
        # Skip rows that look like section headers (all caps, short)
        if cells[0].isdigit() or re.match(r"^\d+\.$", cells[0]):
            name = cells[1] if len(cells) > 1 else ""
            country = cells[2] if len(cells) > 2 else ""
            club = cells[3] if len(cells) > 3 else ""
            category = cells[4] if len(cells) > 4 else ""
            if name:
                fighters.append({"name": name, "country": country,
                                  "club": club, "category": category})
    return fighters


def _parse_text_lines(page) -> list[dict]:
    """
    Very simple line-based fallback used when the page has no table structure.
    Tries to detect repeated patterns like:  1. Fighter Name  SUI  Club  Category
    """
    text = page.extract_text() or ""
    fighters = []
    pattern = re.compile(
        r"^\s*\d+[\.\)]\s+(?P<name>[A-Za-zÀ-ÿ ,\-]+?)\s{2,}"
        r"(?P<country>[A-Za-z]{2,3})\s{2,}"
        r"(?P<club>[A-Za-zÀ-ÿ ,\-\.]+?)\s{2,}"
        r"(?P<category>.+)$",
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        fighters.append({
            "name": m.group("name").strip(),
            "country": m.group("country").strip(),
            "club": m.group("club").strip(),
            "category": m.group("category").strip(),
        })
    return fighters
