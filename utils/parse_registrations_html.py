"""
parse_registrations_html.py

Parses the sportdata registrations page saved as HTML
(File → Save Page As from your browser).

The page structure has one table row per registration entry:
  <tr class="dctabrowwhite"> or <tr class="dctabrowgreen">
    <td> ClubName(WakoCode), COUNTRY (CC) </td>   -- col 0
    <td> ... </td>                                 -- col 1 (flag/empty)
    <td> Lastname Firstname </td>                  -- col 2
    ...
    <td><nobr> 01 PF 091 V M +94 kg </nobr></td>  -- col 6
  </tr>
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup

_COUNTRY_RE = re.compile(r'\(([A-Z]{2,3})\)\s*$')
_SWISS_ABBREVS = {'SUI'}
_SWISS_NAMES   = {'switzerland', 'schweiz', 'suisse', 'svizzera'}

# Rows to skip: summary lines contain "Nennungen gesamt" or "Total athletes"
_SUMMARY_RE = re.compile(r'nennungen gesamt|total athletes', re.IGNORECASE)


def get_fighters_html(html_path: str, country_filter: str = 'SUI') -> list[dict]:
    """Return fighters matching country_filter from a saved sportdata HTML page."""
    fighters = _parse_html(html_path, country_filter=country_filter)

    # Deduplicate by (name, category): keep first occurrence
    seen: set[tuple] = set()
    unique = []
    for f in fighters:
        key = (f['name'].lower(), f['category_code'].lower())
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # Propagate club across entries for the same fighter
    name_to_club: dict[str, str] = {}
    for f in unique:
        if f['club']:
            name_to_club[f['name'].lower()] = f['club']
    for f in unique:
        if not f['club']:
            f['club'] = name_to_club.get(f['name'].lower(), '')

    return unique


def extract_fighters_html(html_path: str) -> list[dict]:
    """Return ALL fighters from a saved sportdata HTML page."""
    return _parse_html(html_path, country_filter=None)


def _matches_country(text: str, country_filter: str) -> bool:
    """Return True if the cell text matches the given country string or code."""
    if not country_filter:
        return True
    
    text_stripped = text.strip()
    
    # Check for abbreviation, e.g. "(SUI)" or "(GER)"
    m = re.search(r'\(([A-Z]{2,3})\)', text_stripped)
    if m:
        if m.group(1).upper() == country_filter.upper():
            return True
            
    # For Swiss edge case, still treat mapping for known names
    if country_filter.upper() == 'SUI':
        text_lower = text_stripped.lower()
        if any(name in text_lower for name in _SWISS_NAMES):
            return True
            
    # Alternatively check if country_filter is present exactly in the text
    return country_filter.lower() in text_stripped.lower()


def _extract_club(team_text: str) -> str:
    """
    Clean up the team/club cell text.
    Input examples:
      'A. Gil Kenpo Karate Academy(WakoSUI-ADB)  SWITZERLAND (SUI)'
      'Flex Kickboxing Baden(WakoSUI-AAD), SWITZERLAND (SUI)'
    Returns: 'A. Gil Kenpo Karate Academy(WakoSUI-ADB)'
    """
    # Remove leading/trailing whitespace and non-breaking spaces
    text = team_text.replace('\xa0', ' ').strip()
    # Strip trailing country info: ", COUNTRY (CC)" or " COUNTRY (CC)"
    text = re.sub(r'[,\s]+[A-Z][A-Za-z\s]+\([A-Z]{2,3}\)\s*$', '', text).strip()
    # Strip trailing country flag text leftover
    text = re.sub(r'\s+[A-Z]{2,3}\s*$', '', text).strip()
    return text


def _parse_html(html_path: str, country_filter: str | None) -> list[dict]:
    with open(html_path, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    fighters = []
    for row in soup.find_all('tr', class_=['dctabrowwhite', 'dctabrowgreen']):
        cells = row.find_all('td', recursive=False)
        if len(cells) < 7:
            continue

        team_text = cells[0].get_text(' ', strip=True).replace('\xa0', ' ').strip()
        name_text = cells[2].get_text(' ', strip=True).strip()
        cat_text  = cells[6].get_text(' ', strip=True).strip()

        # Skip summary/subtotal rows
        if _SUMMARY_RE.search(team_text) or _SUMMARY_RE.search(cat_text):
            continue

        # Need a name and a category
        if not name_text or not cat_text:
            continue

        # Extract country
        country = ''
        m = re.search(r'\(([A-Z]{2,3})\)', team_text)
        if m:
            country = m.group(1)

        if country_filter and not _matches_country(team_text, country_filter):
            continue

        # Normalise: uppercase name and category to match PDF-based code paths
        name = name_text.upper()
        category_code = cat_text.upper()

        club = _extract_club(team_text)

        fighters.append({
            'name':          name,
            'category_code': category_code,
            'category':      category_code,
            'country':       country,
            'club':          club,
        })

    return fighters
