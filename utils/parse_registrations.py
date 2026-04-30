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

# WAKO club-code anchors  –  (WAKOSUI) or (WAKOSUI-ABC)
_WAKO_CODE_RE      = re.compile(r'\(WAKOSUI(?:-[A-Z]{3})?\)')
# Incomplete WAKO code anywhere in the string (no closing ')' for the WAKOSUI group)
_WAKO_PARTIAL_RE   = re.compile(r'\(WAKOSUI(?:-[A-Z]{0,3})?(?![^)]*\))')

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
    return _parse_lines(lines, country_filter=None)


def get_fighters(pdf_path: str, country_filter: str = 'SUI') -> list[dict]:
    """Return fighters from the given country, deduplicated by (name, category_code).
    When duplicates exist, prefer the entry that has club data.
    A second pass fills empty clubs from other categories of the same fighter.
    """
    lines = _extract_lines(pdf_path)
    fighters = _parse_lines(lines, country_filter=country_filter)

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


def _parse_lines(lines: list[str], country_filter: str | None) -> list[dict]:
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

        if country_filter and country != country_filter:
            continue

        # --- Use WAKO club-code as anchor to split club / name ---
        club, name = _extract_club_and_name(lines, i, before)

        if not name:
            continue

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


# ---------------------------------------------------------------------------
# WAKO-code-anchored club / name extraction
# ---------------------------------------------------------------------------

_COUNTRY_NAMES_UPPER = {
    'SWITZERLAND', 'SCHWEIZ', 'SUISSE', 'SVIZZERA',
    'AUSTRIA', 'GERMANY', 'FRANCE', 'ITALY', 'HUNGARY',
    'SLOVAKIA', 'CZECH', 'POLAND', 'SPAIN', 'CROATIA',
    'SERBIA', 'ROMANIA', 'BULGARIA', 'UKRAINE',
}


def _prev_club_text(line: str) -> str:
    """Return `line` if it looks like club-name continuation text.
    Returns '' if the line is a separator line, a skip line, or a
    country-declaration line (we don't want to swallow those).
    """
    if not line or _SEP in line or _SKIP_RE.search(line):
        return ''
    # A bare country marker line (e.g. "SWITZERLAND (SUI)") is not club text.
    # Strip the country abbreviation(s) and see what remains.
    stripped = _COUNTRY_ABBR_RE.sub('', line).strip()
    if not stripped:
        return ''
    # If what remains is just a known country name, skip it.
    if stripped.lower() in SWISS_NAMES or stripped.upper() in {
        'AUSTRIA', 'GERMANY', 'FRANCE', 'ITALY', 'HUNGARY',
        'SLOVAKIA', 'CZECH', 'POLAND', 'SPAIN', 'CROATIA', 'SERBIA',
        'ROMANIA', 'BULGARIA', 'UKRAINE',
    }:
        return ''
    return line


def _name_after_country_noise(s: str) -> str:
    """Like _name_from_raw but skips leading country names/abbreviations first,
    then collects the first run of all-caps name tokens.
    Used to recover first names hidden after 'SWITZERLAND (SUI)' on a line.
    """
    cleaned = _COUNTRY_ABBR_RE.sub('', s).strip()
    tokens = []
    past_country = False
    for tok in cleaned.split():
        tok_clean = tok.rstrip(',-;')
        if tok_clean.upper() in _COUNTRY_NAMES_UPPER:
            past_country = True
            continue
        if past_country and _ALL_CAPS_WORD_RE.match(tok_clean) and len(tok_clean) > 1:
            tokens.append(tok_clean)
        elif tokens:
            break
    return ' '.join(tokens)


def _name_from_raw(raw: str) -> str:
    """Extract fighter name = contiguous run of ALL-CAPS words, ignoring
    country abbreviations and country names that may follow the WAKO code."""
    cleaned = _COUNTRY_ABBR_RE.sub('', raw).strip()
    tokens = []
    for tok in cleaned.split():
        tok_clean = tok.rstrip(',-;')
        if tok_clean.upper() in _COUNTRY_NAMES_UPPER:
            break   # stop at country name (e.g. SWITZERLAND after the code)
        if _ALL_CAPS_WORD_RE.match(tok_clean) and len(tok_clean) > 1:
            tokens.append(tok_clean)
        elif tokens:
            break   # stop at first non-caps token after we've started collecting
    return ' '.join(tokens)


def _extract_club_and_name(lines: list, i: int, before: str) -> tuple[str, str]:
    """Use the WAKO club code as an anchor to cleanly separate club and name.

    Three layout variants observed in the PDF:

    A) Code + name on the same "before" line:
         before = 'WOHLEN(WAKOSUI-AAA) PACE SILVIO'
       → club = prev_club_text + 'WOHLEN(WAKOSUI-AAA)', name = 'PACE SILVIO'

    B) Code is split across before / next line:
         before = 'NIPPON BERN(WAKOSUI- GASSER ISABEL'
         next   = 'AAS), SWITZERLAND (SUI)'
       → reassemble, then split at code end

    C) before is empty; code+name are on next line (fully split layout):
         before = ''
         next   = 'ACADEMY(WAKOSUI-ADB), CRESCINI FRANK'
       → club from next up to code, name from next after code
       (or split-name: lastname in prev, firstname in next-next)
    """
    n = len(lines)
    prev1 = lines[i - 1].strip() if i > 0 else ''
    prev2 = lines[i - 2].strip() if i > 1 else ''
    next1 = lines[i + 1].strip() if i + 1 < n else ''

    # ------------------------------------------------------------------ A
    m = _WAKO_CODE_RE.search(before)
    if m:
        club_tail = before[:m.end()].strip().rstrip(',').strip()
        name_raw  = before[m.end():].strip().lstrip(',').strip()
        if name_raw:
            # Name follows the code on the same line; prev1 (if not sep) = club prefix
            p1 = _prev_club_text(prev1)
            parts = [x for x in [p1, club_tail] if x]
            club = ' '.join(parts)
            name = _name_from_raw(name_raw)
            # If only one word recovered, the first name may be on the next line
            # (e.g. next1 = 'SWITZERLAND (SUI) MIDAS')
            if name and ' ' not in name:
                extra = _name_after_country_noise(next1)
                if extra:
                    name = name + ' ' + extra
            return club, name
        else:
            # Name is split: last name in prev, first name in next
            club = club_tail
            name = ' '.join(_caps_tail(prev1) + _caps_after_paren(next1))
            return club, name

    # ------------------------------------------------------------------ B
    # Partial WAKO code in before (no closing ')') — split across lines,
    # possibly with fighter-name text interleaved between the partial code
    # and the line break.
    m_partial = _WAKO_PARTIAL_RE.search(before)
    if m_partial:
        # Check if next line starts with the code completion: e.g. "AAS),"
        m_close = re.match(r'([A-Z]{0,3}\))', next1)
        if m_close:
            # Reconstruct the complete code from the two pieces
            complete_code = before[m_partial.start():m_partial.end()] + m_close.group(1)
            # Text between the partial code and EOL in before = interleaved name
            name_fragment_before = before[m_partial.end():].strip()
            # Text after the closing on next line = further name / country info
            name_fragment_next = next1[m_close.end():].strip().lstrip(',').strip()
            name_raw = (name_fragment_before + ' ' + name_fragment_next).strip()
            name = _name_from_raw(name_raw)
            club_tail = (before[:m_partial.start()] + complete_code).strip().rstrip(',').strip()
            p1 = _prev_club_text(prev1)
            parts = [x for x in [p1, club_tail] if x]
            club = ' '.join(parts)
            return club, name
        # Simpler split: no interleaving, just complete the code via concatenation
        combined = (before + next1).strip()
        m = _WAKO_CODE_RE.search(combined)
        if m:
            club_tail = combined[:m.end()].strip().rstrip(',').strip()
            name_raw  = combined[m.end():].strip().lstrip(',').strip()
            name = _name_from_raw(name_raw)
            p1 = _prev_club_text(prev1)
            parts = [x for x in [p1, club_tail] if x]
            club = ' '.join(parts)
            return club, name

    # ------------------------------------------------------------------ C
    if not before:
        m = _WAKO_CODE_RE.search(next1)
        if m:
            club_tail = next1[:m.end()].strip().rstrip(',').strip()
            name_raw  = next1[m.end():].strip().lstrip(',').strip()
            if name_raw:
                name = _name_from_raw(name_raw)
                # If name only got a fragment (e.g. just last name), try extending from next2
                if name and ' ' not in name:
                    next2 = lines[i + 2].strip() if i + 2 < n else ''
                    extra = _name_from_raw(_COUNTRY_ABBR_RE.sub('', next2).strip())
                    if extra:
                        name = name + ' ' + extra
                p1 = _prev_club_text(prev1)
                parts = [x for x in [p1, club_tail] if x]
                club = ' '.join(parts)
                return club, name
            else:
                # Fully split: lastname in prev1, firstname in lines[i+2]
                next2 = lines[i + 2].strip() if i + 2 < n else ''
                name = ' '.join(_caps_tail(prev1) + _caps_after_paren(next2))
                return club_tail, name

    # ------------------------------------------------------------------ fallback
    name = _extract_name(before)
    club = _extract_club(before, name) if name else ''
    return club, name

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
    # Additional Swiss city/region names that appear in club names
    'BADEN', 'BRUGG', 'WETTINGEN', 'ZURZACH', 'AARGAU', 'SOLOTHURN',
    'FRIBOURG', 'FREIBURG', 'NEUCHATEL', 'SION', 'SIERRE', 'VISP',
    'SCHAFFHAUSEN', 'FRAUENFELD', 'KREUZLINGEN', 'WEINFELDEN',
    'USTER', 'BUELACH', 'DIELSDORF', 'HORGEN', 'MEILEN', 'PFAEFFIKON',
    'DIETIKON', 'SCHLIEREN', 'REGENSDORF', 'OPFIKON', 'KLOTEN',
    'OSTERMUNDIGEN', 'MURI', 'KONIZ', 'ZOLLIKOFEN', 'BURGDORF',
    'LANGENTHAL', 'BIEL', 'LYSS', 'AARBERG', 'LYSS', 'GRENCHEN',
    'OLTEN', 'AARAU', 'LENZBURG', 'BRUGG', 'RHEINFELDEN',
    'LIESTAL', 'ARLESHEIM', 'ARAU', 'ALLSCHWIL', 'REINACH',
    'ARTH', 'ZUG', 'BAAR', 'CHAM', 'STEINHAUSEN', 'ROTKREUZ',
    'EMMEN', 'KRIENS', 'HORW', 'STANS', 'SARNEN',
    'GLARUS', 'UZNACH', 'RAPPERSWIL', 'WIL', 'GOSSAU',
    'ROMANSHORN', 'ARBON', 'RORSCHACH', 'ALTENRHEIN',
    'RHEIN', 'LIMMAT', 'REUSS', 'REGION', 'NORD', 'SUD', 'OST', 'WEST',
    'NORD', 'SUED', 'OST', 'WEST',
    # Common generic club words that prefix fighter names
    'CLUB', 'KLUB', 'SPORT', 'SPORTS', 'TEAM', 'ACADEMY',
    'KARATE', 'KICKBOXING', 'BOXEN', 'BOXING', 'FITNESS',
    'KAMPFKUNST', 'KAMPFSPORT', 'MARTIAL', 'ARTS', 'MUAY', 'THAI',
    'INTERNATIONAL', 'NATIONAL', 'REGIONAL',
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
    name = ' '.join(name_tokens)
    # Backstop: if the name consumed ALL of the cleaned text, there's nothing
    # left to be the club — meaning this line is club-only (split-line format)
    # and the name actually lives on an adjacent line.
    if name and name == cleaned:
        return ''
    return name


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
