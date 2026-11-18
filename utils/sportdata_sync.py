import re
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TIMETABLE_URL = "https://www.sportdata.org/setglinc/dtm/dtm_timetable.php"


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    s.verify = False
    return s


def fetch_day_times(sportdata_event_id: str, day: str) -> dict:
    """
    Fetch scheduled times from the sportdata timetable for a given day.

    Returns:
        {
          "by_fight_no": {"1101": {"time": "10:01", "time_end": "10:13"}},
          "error": None | str
        }
    """
    params = {"eventid": sportdata_event_id, "system": "kickboxing", "day": day}
    try:
        r = _session().get(_TIMETABLE_URL, params=params, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        return {"by_fight_no": {}, "error": str(exc)}

    if "Verify You are Human" in r.text:
        return {"by_fight_no": {}, "error": "blocked_by_captcha"}

    soup = BeautifulSoup(r.text, "html.parser")
    by_fight_no: dict = {}

    for link in soup.find_all("a", href=re.compile(r"popup_auslosung_main")):
        m = re.match(r"#(\d+)", link.get_text(strip=True))
        if not m:
            continue
        fight_no = m.group(1)

        # Time lives in the same <tr> as the fight-number link, formatted:
        # duration · start · end  e.g. "00:12 · 10:01 · 10:13"
        row = link.find_parent("tr")
        if not row:
            continue
        tm = re.search(
            r"\d{1,2}:\d{2}\s*·\s*(\d{1,2}:\d{2})\s*·\s*(\d{1,2}:\d{2})",
            row.get_text(" "),
        )
        if tm:
            by_fight_no[fight_no] = {"time": tm.group(1), "time_end": tm.group(2)}

    return {"by_fight_no": by_fight_no, "error": None}
