"""
parse_ring_schedule.py

Parses the sportdata ring-schedule PDF (fight cards with two named fighters).

Each table row in col3 has the format:
    06 LK 327 YJ M -45 kg (2)
    #2101 CEBUC DAVID_MARIAN (CLUBUL SPORTIV GTC,ROU)
    VIOREL LOZONSCHI (WAKO MOLDOVA,MDA)

Fighter 1 (starts with #NNNN) → red corner
Fighter 2 (remaining lines)   → blue corner

Returns a list of fight-card dicts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pdfplumber

_TIME_RE     = re.compile(r"(\d{1,2}:\d{2})\s*[-\u2013]\s*(\d{1,2}:\d{2})")
_RING_RE     = re.compile(r"(ring|tatami|tatmi)\s*(\d+)", re.IGNORECASE)
_CAT_RE      = re.compile(r"^\d+\s+[A-Z0-9]{2}\s+\d+", re.IGNORECASE)
_FIGHT_NO_RE = re.compile(r"^#(\d+)\s+(.+)")
# A complete fighter entry ends with (CLUB,CC) where CC = 2-3 upper-case letters
_COUNTRY_END_RE = re.compile(r",([A-Z]{2,3})\)\s*$")

FIGHT_DURATION_MIN = 12





_DAILY_FIGHT_RE = re.compile(
    r'^(\d+)\s+(\d+)\s+([\w/]+)\s+([\dA-Z\-\+,]+)\s+RED\s+(.+?)\s+([A-Z]{3})\s*\nBLUE\s*(.*?)(?:\s+([A-Z]{3}))?$'
)

def _split_camel(name: str) -> str:
    # "KarakusMehmetGokturk" -> "Karakus Mehmet Gokturk"
    return re.sub(r'([a-z])([A-Z])', r'\1 \2', name).replace('_', ' ')

def extract_ring_fights(pdf_path: str) -> list[dict]:
    """Return all fight cards parsed from the ring-schedule PDF."""
    fights: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return fights
            
        first_page_text = pdf.pages[0].extract_text()
        if first_page_text and "DailySchedule" in first_page_text:
            return _parse_daily_schedule(pdf)
            
        for page in pdf.pages:
            _parse_page(page, fights)
            
    return fights

def _parse_daily_schedule(pdf) -> list[dict]:
    fights = []
    for page in pdf.pages:
        text = page.extract_text()

        venue_m = re.search(r'\b(RING|TATAMI|TATMI)\s*0*(\d+)\b', text or "", re.IGNORECASE)
        session_m = re.search(r'SESSION\s+\d+\s+([RT])\s*0*(\d+)', text or "", re.IGNORECASE)
        time_m = re.search(r'\d{4}-\d{2}-\d{2}\s*(\d{2}:\d{2})', text)

        if venue_m:
            venue_label = "Tatami" if venue_m.group(1).upper() in ("TATAMI", "TATMI") else "Ring"
            current_ring = f"{venue_label} {int(venue_m.group(2)):02d}"
        elif session_m:
            venue_label = "Ring" if session_m.group(1).upper() == "R" else "Tatami"
            current_ring = f"{venue_label} {int(session_m.group(2)):02d}"
        else:
            current_ring = "Ring 01"
            
        ring_start = None
        if time_m:
            try:
                ring_start = datetime.strptime(time_m.group(1), "%H:%M")
            except ValueError:
                pass
                
        tables = page.extract_tables()
        if not tables:
            continue
            
        for row in tables[0]:
            cell = row[0] if row else ""
            if not cell:
                continue
                
            m = _DAILY_FIGHT_RE.match(cell)
            if not m:
                continue
                
            seq_no = int(m.group(1))
            fight_no = int(m.group(2))
            phase = m.group(3)
            category_code = m.group(4)
            f1_name_raw = m.group(5).strip()
            f1_country = m.group(6).strip()
            f2_name_raw = m.group(7).strip()
            f2_country = m.group(8) or ""
            
            f1_name = _split_camel(f1_name_raw).upper()
            f2_name = _split_camel(f2_name_raw).upper() if f2_name_raw else ""
            
            time_str = ""
            time_end_str = ""
            if ring_start:
                est = ring_start + timedelta(minutes=(seq_no - 1) * FIGHT_DURATION_MIN)
                time_str = est.strftime("%H:%M")
                time_end_str = (est + timedelta(minutes=FIGHT_DURATION_MIN)).strftime("%H:%M")
            
            fights.append({
                "ring": current_ring,
                "time": time_str,
                "time_end": time_end_str,
                "time_estimated": bool(ring_start),
                "seq_no": seq_no,
                "fight_no": fight_no,
                "category_code": category_code,
                "phase": phase,
                "fighter1": {
                    "name": f1_name,
                    "club": "",
                    "country": f1_country,
                    "color": "red"
                },
                "fighter2": {
                    "name": f2_name,
                    "club": "",
                    "country": f2_country,
                    "color": "blue"
                }
            })
    return fights

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_page(page, fights: list[dict]) -> None:
    tables = page.extract_tables()
    if not tables:
        return

    table = tables[0]
    current_ring = ""
    # Ring start time and index used for time estimation
    ring_start: datetime | None = None
    ring_seq = 0

    for row in table:
        if not row:
            continue

        col0 = (row[0] or "").strip()
        col1 = (row[1] or "").strip() if len(row) > 1 else ""
        col3 = (row[3] or "").strip() if len(row) > 3 else ""
        col4 = (row[4] or "").strip() if len(row) > 4 else ""

        # --- Detect ring header ---
        m = _RING_RE.search(col0)
        if m and not _TIME_RE.search(col0):
            venue_label = "Tatami" if m.group(1).lower() in ("tatami", "tatmi") else "Ring"
            current_ring = f"{venue_label} {int(m.group(2)):02d}"
            ring_start = None
            ring_seq = 0
            continue

        # --- Skip header / title rows ---
        if col0 in ("Time", "") and col1 in ("#", ""):
            if not col3 or col3 == "Match":
                continue

        # --- Parse time ---
        time_str = ""
        time_end_str = ""
        time_estimated = False
        m_time = _TIME_RE.search(col0)
        if m_time:
            time_str     = m_time.group(1)
            time_end_str = m_time.group(2)
            # Track ring start for estimation fallback
            if ring_start is None:
                try:
                    ring_start = datetime.strptime(time_str, "%H:%M")
                    ring_seq = int(col1) if col1.isdigit() else 1
                except ValueError:
                    pass

        # --- Parse match cell ---
        if not col3:
            continue

        lines = [l.strip() for l in col3.split("\n") if l.strip()]
        if not lines:
            continue

        # First line must look like a category code
        if not _CAT_RE.match(lines[0]):
            continue

        category_raw = re.sub(r"\s*\(\d+\)\s*$", "", lines[0]).strip()
        category_code = category_raw.upper()

        # Parse fighters from remaining lines
        f1_name, f1_club, f1_country = "", "", ""
        f2_name, f2_club, f2_country = "", "", ""
        fight_no = None

        # Join all fighter lines into one string, then split on the boundary
        # between fighter1 (starts with #NNNN) and fighter2.
        fighter_lines = lines[1:]
        if not fighter_lines:
            continue

        # Find fight number and accumulate fighter text
        combined = " ".join(fighter_lines)
        m_fn = _FIGHT_NO_RE.match(combined)
        if m_fn:
            fight_no = int(m_fn.group(1))
            rest = m_fn.group(2)
        else:
            rest = combined

        # Split rest into fighter1 and fighter2.
        # Fighter1 ends at the first complete (CLUB,CC) pattern.
        # Then fighter2 is the remainder.
        f1_text, f2_text = _split_fighters(rest)

        f1_name, f1_club, f1_country = _parse_fighter(f1_text)
        if f2_text:
            f2_name, f2_club, f2_country = _parse_fighter(f2_text)

        if not f1_name:
            continue

        # Estimate time if missing
        if not time_str and ring_start is not None and col1.isdigit():
            seq = int(col1)
            est = ring_start + timedelta(minutes=(seq - ring_seq) * FIGHT_DURATION_MIN)
            time_str = est.strftime("%H:%M")
            time_end_str = (est + timedelta(minutes=FIGHT_DURATION_MIN)).strftime("%H:%M")
            time_estimated = True

        seq_no = int(col1) if col1.isdigit() else None

        fights.append({
            "ring":           current_ring,
            "time":           time_str,
            "time_end":       time_end_str,
            "time_estimated": time_estimated,
            "seq_no":         seq_no,
            "fight_no":       fight_no,
            "category_code":  category_code,
            "phase":          col4,
            "fighter1": {
                "name":    f1_name,
                "club":    f1_club,
                "country": f1_country,
                "color":   "red",
            },
            "fighter2": {
                "name":    f2_name,
                "club":    f2_club,
                "country": f2_country,
                "color":   "blue",
            },
        })


def _split_fighters(text: str) -> tuple[str, str]:
    """
    Split 'FIGHTER1 (CLUB,CC) FIGHTER2 (CLUB2,CC2)' into two parts.
    We look for the first ,XX) or ,XXX) that ends a fighter entry.
    """
    m = re.search(r",[A-Z]{2,3}\)", text)
    if m:
        split_pos = m.end()
        return text[:split_pos].strip(), text[split_pos:].strip()
    return text.strip(), ""


def _parse_fighter(text: str) -> tuple[str, str, str]:
    """
    Parse 'LASTNAME FIRSTNAME (CLUB NAME,COUNTRY)' into (name, club, country).
    Handles underscores in names (PDF artefact for spaces).
    """
    text = text.strip()
    if not text:
        return "", "", ""

    m = re.match(r"^(.*?)\s*\((.+),([A-Z]{2,3})\)\s*$", text)
    if m:
        raw_name = m.group(1).strip().replace("_", " ")
        club     = m.group(2).strip()
        country  = m.group(3).strip()
        return raw_name, club, country

    # Fallback: no parenthesis found
    return text.replace("_", " "), "", ""


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("_", " ").replace("-", " ")).strip()


def find_swiss_fights(
    fights: list[dict],
    swiss_fighters: list[dict],
    club_filter: str = "",
) -> list[dict]:
    """
    Return fight cards where at least one corner belongs to a Swiss fighter
    (optionally filtered by club). Adds 'our_color' key ('red'|'blue'|'both').
    """
    # Build set of normalised names to match against
    if club_filter:
        cf = club_filter.lower()
        targets = [f for f in swiss_fighters if cf in f.get("club", "").lower()]
    else:
        targets = swiss_fighters

    target_names = {_normalise(f["name"]) for f in targets}
    if not target_names:
        return []

    result = []
    for fight in fights:
        n1 = _normalise(fight["fighter1"]["name"])
        n2 = _normalise(fight["fighter2"]["name"])

        hit1 = _name_matches(n1, target_names)
        hit2 = _name_matches(n2, target_names)

        if not hit1 and not hit2:
            continue

        card = dict(fight)
        if hit1 and hit2:
            card["our_color"] = "both"
        elif hit1:
            card["our_color"] = "red"
        else:
            card["our_color"] = "blue"
        result.append(card)

    result.sort(key=lambda c: (
        c["time"] or "99:99",
        c["ring"],
        c["seq_no"] or 0,
    ))
    return result


def _name_matches(norm_name: str, target_names: set[str]) -> bool:
    """Fuzzy name match: exact, substring, or token-subset."""
    if norm_name in target_names:
        return True
    tokens_q = set(norm_name.split())
    for t in target_names:
        if (t and t in norm_name) or (norm_name and norm_name in t):
            return True
        tokens_t = set(t.split())
        shorter = tokens_q if len(tokens_q) <= len(tokens_t) else tokens_t
        longer  = tokens_t if shorter is tokens_q else tokens_q
        if shorter and shorter <= longer:
            return True
    return False
