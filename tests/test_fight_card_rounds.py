import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from utils import storage
from utils.parse_ring_schedule import category_lookup, category_timing, extract_ring_fights


class FakePage:
    def __init__(self, text, rows):
        self.text = text
        self.rows = rows

    def extract_text(self):
        return self.text

    def extract_tables(self):
        return [self.rows]


class FakePdf:
    def __init__(self, *pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def legacy_fight(code, number):
    return f"{code} (2)\n#{number} RED FIGHTER (Team,SUI) BLUE FIGHTER (Other,GER)"


def legacy_page(rows, ring="Ring 01"):
    return FakePage(ring, [[ring, "", "", "", ""]] + [
        [time, str(seq), "", legacy_fight(code, 2100 + seq), "Final"]
        for seq, time, code in rows
    ])


def daily_page(codes, start="09:00"):
    text = f"DailySchedule RING 01 2026-03-19 {start}" if start else "DailySchedule RING 01"
    rows = [[f"{seq} {2100 + seq} Final {code} RED RedFighter SUI\nBLUE BlueFighter GER"]
            for seq, code in codes]
    return FakePage(text, rows)


REGISTRATIONS = [
    {"name": "RED FIGHTER", "country": "SUI", "club": "Team", "category_code": "02 LC 106 CH F -42 KG"},
    {"name": "RED FIGHTER", "country": "SUI", "club": "Team", "category_code": "01 PF 048 OC F -55 KG"},
    {"name": "RED FIGHTER", "country": "SUI", "club": "Team", "category_code": "06 LK 327 CH M -45 KG"},
]


class FightCardRoundsTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.events_dir = Path(temp_dir.name) / "events"
        self.patch_sb = patch.object(storage, "_sb", None)
        self.patch_dir = patch.object(storage, "EVENTS_DIR", str(self.events_dir))
        self.patch_sb.start()
        self.patch_dir.start()
        self.addCleanup(self.patch_sb.stop)
        self.addCleanup(self.patch_dir.stop)
        self.event_id = storage.create_event("Mixed timing", REGISTRATIONS)
        self.client = app.test_client()

    def test_discipline_and_age_both_control_short_slots(self):
        for style in ("LC", "KL", "PF"):
            for age in ("CH", "YC", "OC"):
                with self.subTest(style=style, age=age):
                    self.assertEqual((2, 9), category_timing(f"01 {style} 034 {age} M -37 kg"))
            self.assertEqual((3, 12), category_timing(f"01 {style} 034 S M -37 kg"))
        for style in ("LK", "K1", "FC"):
            for age in ("CH", "YC", "OC", "S"):
                with self.subTest(style=style, age=age):
                    self.assertEqual((3, 12), category_timing(f"06 {style} 327 {age} M -45 kg"))
        self.assertEqual((None, 12), category_timing("02LC106"))
        self.assertEqual((2, 9), category_timing("02LC106", category_lookup(REGISTRATIONS)))
        self.assertEqual((3, 12), category_timing("06LK327"))
        self.assertEqual((3, 12), category_timing("02 LC 106 SCH M -42 KG"))
        ambiguous = REGISTRATIONS + [{"category_code": "02 LC 106 S F -42 KG"}]
        self.assertEqual((None, 12), category_timing("02LC106", category_lookup(ambiguous)))

    def test_legacy_mixed_sequence_and_explicit_anchor(self):
        page = legacy_page([
            (1, "09:00 - 09:12", "02 LC 106 CH F -42 kg"),
            (2, "", "06 LK 327 CH M -45 kg"),
            (3, "", "01 PF 048 OC F -55 kg"),
            (4, "10:00 - 10:11", "02 LC 106 YC F -42 kg"),
            (5, "", "07 K1 426 CH M -54 kg"),
        ])
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(page)):
            fights = extract_ring_fights("unused.pdf")
        self.assertEqual([("09:00", "09:12"), ("09:09", "09:21"), ("09:21", "09:30"),
                          ("10:00", "10:11"), ("10:09", "10:21")],
                         [(f["time"], f["time_end"]) for f in fights])
        self.assertEqual([2, 3, 2, 2, 3], [f["rounds"] for f in fights])
        self.assertEqual([False, True, True, False, True], [f["time_estimated"] for f in fights])

    def test_daily_mixed_sequence_and_compact_registration_lookup(self):
        page = daily_page([(1, "02LC106"), (2, "06LK327"), (3, "01PF048"), (4, "03KL999")])
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(page)):
            fights = extract_ring_fights("unused.pdf", registrations=REGISTRATIONS)
        self.assertEqual([("09:00", "09:09"), ("09:09", "09:21"), ("09:21", "09:30"),
                          ("09:30", "09:42")], [(f["time"], f["time_end"]) for f in fights])
        self.assertEqual([2, 3, 2, None], [f["rounds"] for f in fights])
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(daily_page([(1, "02LC106")], start=""))):
            self.assertEqual("", extract_ring_fights("unused.pdf", registrations=REGISTRATIONS)[0]["time"])

    def test_missing_fights_do_not_produce_false_estimates(self):
        page = legacy_page([(1, "09:00 - 09:12", "02 LC 106 CH F -42 kg"),
                            (3, "", "06 LK 327 S M -45 kg"),
                            (4, "", "01 PF 048 OC F -55 kg")])
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(page)):
            fights = extract_ring_fights("unused.pdf")
        self.assertEqual(["09:00", "", ""], [f["time"] for f in fights])
        page = daily_page([(1, "02LC106"), (3, "06LK327"), (4, "01PF048")])
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(page)):
            fights = extract_ring_fights("unused.pdf", registrations=REGISTRATIONS)
        self.assertEqual(["09:00", "", ""], [f["time"] for f in fights])

    def test_independent_rings_and_pages(self):
        first = legacy_page([(1, "09:00 - 09:09", "02 LC 106 CH F -42 kg")])
        second = legacy_page([(1, "11:00 - 11:12", "06 LK 327 S M -45 kg"),
                              (2, "", "01 PF 048 OC F -55 kg")], ring="Ring 02")
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(first, second)):
            fights = extract_ring_fights("unused.pdf")
        self.assertEqual(["09:00", "11:00", "11:12"], [f["time"] for f in fights])

    def test_upload_multiple_files_stores_rows_not_day_rounds(self):
        pdfs = [FakePdf(legacy_page([(1, "09:00 - 09:09", "02 LC 106 CH F -42 kg"),
                                     (2, "", "06 LK 327 CH M -45 kg")])),
                FakePdf(daily_page([(1, "01PF048"), (2, "06LK327")]))]
        with patch("utils.parse_ring_schedule.pdfplumber.open", side_effect=pdfs) as open_pdf:
            response = self.client.post(f"/events/{self.event_id}/upload-day", data={
                "day_name": "Saturday", "day_type": "ring-cards", "rounds": "2",  # obsolete form input is ignored
                "schedule": [(io.BytesIO(b"pdf"), "ring1.pdf"), (io.BytesIO(b"pdf"), "ring2.pdf")],
            }, content_type="multipart/form-data")
        self.assertEqual(302, response.status_code)
        self.assertEqual(2, open_pdf.call_count)
        day_id = storage.get_event(self.event_id)["days"][0]["id"]
        day = storage.get_event_day(self.event_id, day_id)
        self.assertNotIn("rounds", day)
        self.assertEqual(["09:00", "09:09", "09:00", "09:09"], [f["time"] for f in day["rows"]])
        shared = self.client.get(f"/share/{self.event_id}/day/{day_id}")
        self.assertEqual(200, shared.status_code)
        self.assertIn(b"2 \xc3\x97 2 min rounds", shared.data)
        self.assertIn(b"3 \xc3\x97 2 min rounds", shared.data)
        self.assertNotIn(b"id=\"rounds_section\"", self.client.get(f"/events/{self.event_id}").data)
        hero = shared.data.split(b"</div>", 1)[0]
        self.assertNotIn(b"min rounds", hero)
        self.assertIn(b'class="card-details"', shared.data)
        self.assertLess(shared.data.index(b'class="card-details"'), shared.data.index(b"2 \xc3\x97 2 min rounds"))

    def test_legacy_day_ignores_old_rounds_metadata(self):
        rows = [{"ring": "Ring 01", "time": "09:00", "time_end": "09:12", "time_estimated": True,
                 "seq_no": 1, "fight_no": 2101, "category_code": "02 LC 106 CH F -42 kg",
                 "phase": "Final", "fighter1": {"name": "RED FIGHTER", "country": "SUI", "club": "Team"},
                 "fighter2": {"name": "BLUE FIGHTER", "country": "GER", "club": "Other"}}]
        day_id = storage.add_event_day(self.event_id, "Old", "ring-cards", rows)
        # Old JSON day-level metadata is harmless; display is category-based.
        day_file = self.events_dir / self.event_id / "days" / f"{day_id}.json"
        import json
        day = json.loads(day_file.read_text())
        day["rounds"] = 3
        day_file.write_text(json.dumps(day))
        shared = self.client.get(f"/share/{self.event_id}/day/{day_id}")
        self.assertEqual(200, shared.status_code)
        self.assertIn(b"2 \xc3\x97 2 min rounds", shared.data)
        self.assertIn(b"09:00", shared.data)  # Old stored times are not recalculated.


if __name__ == "__main__":
    unittest.main()