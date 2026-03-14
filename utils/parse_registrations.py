"""
parse_registrations.py

Parses the sportdata registrations PDF (printed webpage).
"""
from __future__ import annotations

import re
import pdfplumber

# The person-icon character that separates name from category in the PDF
_SEP = '\uf007'

# Country abbreviation in parentheses, e.g. (SUI) (AUT) (GER)
_COUNTRY_ABBR_RE = re.compile(r'\(([A-Z]{2,3})\)')

# Skip lines that are page metadata
_SKIP_RE = re.compile(
    r'https?://|SET-ONLINE|UBERSICHT|NENNUNGEN GESAMT|TOTAL ATHLETES'
    r'|^TEAM\s|Suchen:|^\s*\d+/\d+\s*$',
    re.IGNORECASE,
)

SWISS_ABBREVS = {'SUI'}
SWISS_NAMES   = {'switzerland', 'schweiz', 'suisse', 'svizzera'}


def extract_fighters(pdf_path: str) -> list[dict]:
    """Return ALL fighters from the registrations PDF."""
    lines = _extract_lines(pdf_path)
    return _parse_lines(lines, swiss_only=False)


def get_swiss_fighters(pdf_path: str) -> list[dict]:
    """Return Swiss fighters only, deduplicated by (name, category_code).
    When duplicates exist, prefer the entry that has club data.
    A second pass fills empty clubs from other categories of the same fighter.
    """
    lines = _extract_lines(pdf_path)
    fighters = _parse_lines(lines, swiss_only=True)

    # Merge duplicates per (name, category): keep best club (non-empty wins)
    best: dict[tuple, dict] = {}
    for f in fighters:
        key = (f['name'].lower(), f['category_code'].lower())
        if key not in best or (not best[key]['club'] and f['club']):
            best[key] = f
    unique = list(best.values())

    # Second pass: propagate club from any other category of the same fighter
    name_to_club: dict[str, str] = {}
    for f in unique:
        if f['club']:
            name_to_club[f['name'].lower()] = f['club']
    for f in unique:
        if not f['club']:
            f['club'] = name_to_club.get(f['name'].lower(), '')

    return unique


def _extract_lines(pdf_path: str) -> list[str]:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            lines.extend(text.splitlines())
    return lines


def _parse_lines(lines: list[str], swiss_only: bool) -> list[dict]:
    fighters = []
    n = len(lines)
    current_country = ''   # carries forward from the last seen country header

    for i, raw_line in enumerate(lines):
        # Update the running country from any line (section headers, etc.)
        line_country = _country_from_text(raw_line)
        if line_country:
            current_country = line_country

        if _SEP not in raw_line:
            continue
        line = raw_line.strip()
        if _SKIP_RE.search(line):
            continue

        parts = line.split(_SEP, 1)
        before        = parts[0].strip()   # club prefix + fighter name
        category_raw  = parts[1].strip()   # category code (uppercase)

        if not category_raw:
            continue

        # --- extract country from the "before" part first ---
        country = _country_from_text(before)

        # --- if not on this line, check ±2 surrounding lines ---
        if not country:
            window_start = max(0, i - 2)
            window_end   = min(n, i + 3)
            for ctx_line in lines[window_start:window_end]:
                country = _country_from_text(ctx_line)
                if country:
                    break

        # --- fall back to the running country (handles Switzerland-only PDFs
        #     where (SUI) appears once in a section header, not on every line) ---
        if not country:
            country = current_country

        if swiss_only and country not in SWISS_ABBREVS:
            continue

        # --- extract name: rightmost uppercase words before the icon ---
        name = _extract_name(before)

        # --- fallback: name is split across adjacent lines ---
        # Small Switzerland-only PDF layout:
        #   prev_line: CLUB(CODE- LASTNAME
        #   sep_line:  \uf007 CATEGORY   (or CLUB(CODE) \uf007 CATEGORY)
        #   next_line: CODE) FIRSTNAME
        if not name:
            prev_line = lines[i - 1] if i > 0 else ''
            next_line = lines[i + 1] if i + 1 < n else ''
            prev_tokens = _caps_tail(prev_line)
            next_tokens = _caps_after_paren(next_line)
            name = ' '.join(prev_tokens + next_tokens)

        if not name:
            continue

        # --- clean up club ---
        club = _extract_club(before, name)

        # Normalise category code to lowercase to match schedule codes
        category_code = category_raw.strip().rstrip('.')

        fighters.append({
            'name':          name,
            'category_code': category_code,
            'category':      category_code,     # alias used by matcher
            'country':       country,
            'club':          club,
        })

    return fighters


_ALL_CAPS_WORD_RE = re.compile(r"^[A-ZÄÖÜ][A-ZÄÖÜ\-\']+$")

# Single-word place/country names that appear before the fighter name
_SKIP_WORDS = {
    'AUSTRIA', 'GERMANY', 'FRANCE', 'ITALY', 'ITALIA', 'HUNGARY',
    'SLOVAKIA', 'CZECH', 'POLAND', 'SPAIN', 'CROATIA', 'SERBIA',
    'ROMANIA', 'BULGARIA', 'UKRAINE', 'SWITZERLAND', 'SCHWEIZ',
    'SUISSE', 'SVIZZERA',
    # Common Swiss / German city names that appear as club prefixes
    'BERN', 'ZURICH', 'ZUERICH', 'BASEL', 'GENEVA', 'GENF',
    'LAUSANNE', 'LUZERN', 'LUCERNE', 'WINTERTHUR', 'AARAU',
    'THUN', 'BIEL', 'CHUR', 'ST', 'WIEN', 'GRAZ', 'LINZ',
    'SALZBURG', 'INNSBRUCK', 'KLAGENFURT', 'BERLIN', 'MUNICH',
    'MUENCHEN', 'HAMBURG', 'COLOGNE', 'KOELN', 'FRANKFURT',
    'WIEN', 'BUDAPEST', 'PRAGUE', 'WARSAW', 'ROME', 'MILANO',
    'TORINO', 'NAPOLI', 'PARIS', 'LYON', 'MADRID', 'BARCELONA',
    'SEKTION', 'ABTEILUNG', 'SECTION',
}


def _caps_tail(s: str) -> list[str]:
    """
    Returns the single rightmost all-caps name token from a line,
    stopping (and discarding) if we hit a word containing '(' (= club code).
    Only one token is returned — that's enough for the surname fallback.
    """
    tokens = s.strip().split()
    for tok in reversed(tokens):
        tok_clean = tok.rstrip(',-;')
        if '(' in tok_clean:
            break  # reached club-code word, give up
        if _ALL_CAPS_WORD_RE.match(tok_clean) and len(tok_clean) > 1 and tok_clean not in _SKIP_WORDS:
            return [tok_clean]  # return just the rightmost name word
        else:
            break  # non-caps word — stop
    return []


def _caps_after_paren(s: str) -> list[str]:
    """
    All-caps name tokens from a line, taken from AFTER the first ')' if one
    exists, or from the whole line if there is no ')'.
    Handles 'ADB) CRESCINI FRANK' → ['CRESCINI', 'FRANK']
    and 'VERENA' → ['VERENA'].
    """
    idx = s.find(')')
    tail = s[idx + 1:] if idx >= 0 else s
    tokens = []
    for tok in tail.split():
        tok_clean = tok.rstrip(',-;')
        if _ALL_CAPS_WORD_RE.match(tok_clean) and len(tok_clean) > 1 and tok_clean not in _SKIP_WORDS:
            tokens.append(tok_clean)
    return tokens


def _extract_name(before: str) -> str:
    """
    Name = rightmost run of ALL-CAPS words in the "before" string,
    excluding known country/city names and club keywords.
    We collect words from right to left and stop at the first non-name token.
    """
    # Remove content in parentheses (club abbreviations)
    cleaned = re.sub(r'\([^)]*\)', ' ', before).strip()
    tokens = cleaned.split()
    name_tokens = []
    for tok in reversed(tokens):
        tok_clean = tok.rstrip(',-')
        if _ALL_CAPS_WORD_RE.match(tok_clean) and len(tok_clean) > 1:
            if tok_clean.upper() in _SKIP_WORDS:
                break
            name_tokens.insert(0, tok_clean)
        else:
            break
    return ' '.join(name_tokens)


def _country_from_text(text: str) -> str:
    """Return 3-letter country abbreviation if found in text, else empty string."""
    m = _COUNTRY_ABBR_RE.search(text)
    if m:
        return m.group(1).upper()
    text_lower = text.lower()
    for name in SWISS_NAMES:
        if name in text_lower:
            return 'SUI'
    return ''


def _extract_club(before: str, name: str) -> str:
    """Extract club = everything before the fighter name.

    The raw format is:  CLUB_NAME(CODE)[,] FIGHTER_NAME
    e.g. 'SWITZERLAND(WAKOSUI), ENZL JAN'  -> 'SWITZERLAND(WAKOSUI)'
         'POWER SPORT CLUB(WAKOSUI-ABY), NAME' -> 'POWER SPORT CLUB(WAKOSUI-ABY)'
    Country/city words are intentional parts of the club name and must not
    be stripped.
    """
    idx = before.rfind(name)
    if idx > 0:
        club = before[:idx].strip().strip(',').strip()
        # Discard partial lines where the club code was cut off onto a prev line
        # (e.g. 'ABY) NAME' or 'AAA) NAME').
        if club.startswith(')') or re.match(r'^[A-Z]{2,4}\)', club):
            return ''
        return club
    return ''
