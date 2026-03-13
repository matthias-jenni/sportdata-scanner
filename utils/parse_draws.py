"""
parse_draws.py

Parses the sportdata draws PDF.
"""
from __future__ import annotations

import re
import pdfplumber

_CAT_CODE_RE = re.compile(
    r"(\d{2}\s+[A-Z]{2}\s+\d{3}(?:\s+[^\n\[]+?)?)\s*(?:\[|\n|$)",
    re.IGNORECASE,
)
_POOL_CELL_RE = re.compile(r"pool\s*[\n\s]+(\d+)\s*/\s*\d+", re.IGNORECASE)
_SEED_RE      = re.compile(r"^\(\*\d+\)\s*")
_SKIP_RE      = re.compile(
    r"^(?:final|semifinal|bronze|gold|copyright|\(c\)sportdata|wako world cup"
    r"|ring\s*\d|pool\s*\d|license:|^\s*\[\d+\]\s*$)",
    re.IGNORECASE,
)


def extract_draws(pdf_path: str) -> dict:
    """
    Returns {normalised_category_code: {normalised_fighter_name: pool_number}}.
    """
    result: dict[str, dict[str, int]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            _parse_page(page, result)
    return result


def _parse_page(page, result: dict) -> None:
    tables = page.extract_tables()
    if not tables or not tables[0]:
        return

    header_row = tables[0][0]   # always row 0 of table 1
    if not header_row:
        return

    # --- Category code from col 0 ---
    cat_cell = str(header_row[0] or "").strip()
    cat_m = _CAT_CODE_RE.search(cat_cell)
    if not cat_m:
        return
    raw_cat = cat_m.group(1).strip()
    # Remove trailing count like "(10)" or "[10]"
    raw_cat = re.sub(r"\s*[\(\[]\d+[\)\]]\s*$", "", raw_cat).strip()
    cat_key = _normalise(raw_cat)

    # --- Pool number from col 2 ---
    pool_cell = str(header_row[2] or "").strip() if len(header_row) > 2 else ""
    pool_m = _POOL_CELL_RE.search(pool_cell)
    if not pool_m:
        return
    pool_num = int(pool_m.group(1))

    # --- Fighter names from raw text ---
    text = page.extract_text() or ""
    bucket = result.setdefault(cat_key, {})

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _SKIP_RE.match(line):
            continue

        # Must contain a country abbreviation inside parens at the end:
        # "Name One (Club,SUI)" or "(*1) Name (Club,SUI)"
        if not re.search(r"\([^)]*,[A-Z]{2,3}\)\s*(?:\*.*)?$", line):
            continue

        # Strip seed marker
        line = _SEED_RE.sub("", line).strip()

        # Extract name = everything before the first " ("
        paren_idx = line.find(" (")
        if paren_idx <= 0:
            continue
        name = line[:paren_idx].strip()
        if not name or len(name) < 2:
            continue

        bucket[_normalise(name)] = pool_num


def _normalise(s: str) -> str:
    """Lowercase and collapse whitespace."""
    return re.sub(r"\s+", " ", s.strip().lower())


def pool_for_fighter(draws: dict, category_code: str, fighter_name: str) -> int | None:
    """
    Look up which pool a fighter is in.

    fighter_name comes from registrations (ALL CAPS, e.g. "ENZL JAN").
    draws keys are mixed case normalised (e.g. "enzl jan").
    We compare lowercase and also try last-name-first permutations.
    """
    if not draws:
        return None

    cat_key = _normalise(category_code)
    bucket = draws.get(cat_key)
    if not bucket:
        return None

    name_norm = _normalise(fighter_name)

    # 1. Exact normalised match
    if name_norm in bucket:
        return bucket[name_norm]

    # 2. Substring match in either direction
    for stored, pool in bucket.items():
        if stored in name_norm or name_norm in stored:
            return pool

    # 3. Token-set match: all tokens of the shorter name present in the longer
    tokens_q   = set(name_norm.split())
    for stored, pool in bucket.items():
        tokens_s = set(stored.split())
        shorter  = tokens_q if len(tokens_q) <= len(tokens_s) else tokens_s
        longer   = tokens_s if shorter is tokens_q else tokens_q
        if len(shorter) >= 1 and shorter <= longer:
            return pool

    return None
