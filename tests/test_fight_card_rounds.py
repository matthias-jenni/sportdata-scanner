import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from utils import storage
from utils.parse_ring_schedule import extract_ring_fights


class FakePage:
    def __init__(self, text, rows):
        self.text = text
        self.rows = rows

    def extract_text(self):
        return self.text

    def extract_tables(self):
        return [self.rows]


class FakePdf:
    def __init__(self, page):
        self.pages = [page]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FightCardRoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.events_dir = Path(self.temp_dir.name) / "events"
        self.patch_sb = patch.object(storage, "_sb", None)
        self.patch_dir = patch.object(storage, "EVENTS_DIR", str(self.events_dir))
        self.patch_sb.start()
        self.patch_dir.start()
        self.addCleanup(self.patch_sb.stop)
        self.addCleanup(self.patch_dir.stop)
        self.event_id = storage.create_event("Rounds test", [{"name": "RED FIGHTER", "country": "SUI", "club": "Team"}])
        self.client = app.test_client()

    def upload(self, rounds, file_count=1):
        return self.client.post(
            f"/events/{self.event_id}/upload-day",
            data={
                "day_name": "Saturday",
                "day_type": "ring-cards",
                "rounds": rounds,
                "schedule": [(io.BytesIO(b"pdf"), f"ring{i}.pdf") for i in range(file_count)],
            },
            content_type="multipart/form-data",
        )

    @staticmethod
    def legacy_page():
        fight = "06 LK 327 YJ M -45 kg (2)\n#2101 RED FIGHTER (Team,SUI) BLUE FIGHTER (Other,GER)"
        return FakePage("Ring 01", [
            ["Ring 01", "", "", "", ""],
            ["09:00 - 09:12", "1", "", fight, "Final"],
            ["", "2", "", fight.replace("#2101", "#2102"), "Final"],
        ])

    @staticmethod
    def daily_page(start=True):
        text = "DailySchedule RING 01 2026-03-19 09:00" if start else "DailySchedule RING 01"
        row = "{} {} Final 06LK327 RED RedFighter SUI\nBLUE BlueFighter GER"
        return FakePage(text, [[row.format(1, 2101)], [row.format(2, 2102)]])

    def test_legacy_estimates_and_explicit_times(self):
        for rounds, expected_end, next_time, next_end in (
            (2, "09:12", "09:09", "09:18"),
            (3, "09:12", "09:12", "09:24"),
        ):
            with self.subTest(rounds=rounds), patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(self.legacy_page())):
                first, second = extract_ring_fights("unused.pdf", rounds=rounds)
                self.assertEqual(("09:00", expected_end, False), (first["time"], first["time_end"], first["time_estimated"]))
                self.assertEqual((next_time, next_end, True), (second["time"], second["time_end"], second["time_estimated"]))

    def test_daily_estimates_and_missing_start(self):
        for rounds, end, next_time, next_end in (
            (2, "09:09", "09:09", "09:18"),
            (3, "09:12", "09:12", "09:24"),
        ):
            with self.subTest(rounds=rounds), patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(self.daily_page())):
                first, second = extract_ring_fights("unused.pdf", rounds=rounds)
                self.assertEqual(("09:00", end), (first["time"], first["time_end"]))
                self.assertEqual((next_time, next_end), (second["time"], second["time_end"]))
                self.assertTrue(second["time_estimated"])
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(self.daily_page(start=False))):
            self.assertEqual("", extract_ring_fights("unused.pdf", rounds=2)[0]["time"])

    def test_invalid_rounds_rejected_before_parsing(self):
        with self.assertRaises(ValueError):
            extract_ring_fights("unused.pdf", rounds=4)
        for rounds in ("4", "0", "two"):
            with self.subTest(rounds=rounds):
                response = self.upload(rounds)
                self.assertEqual(302, response.status_code)
                self.assertEqual([], storage.get_event(self.event_id)["days"])

    def test_missing_rounds_defaults_to_existing_twelve_minute_slots(self):
        with patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(self.legacy_page())):
            response = self.client.post(
                f"/events/{self.event_id}/upload-day",
                data={"day_name": "Default", "day_type": "ring-cards", "schedule": (io.BytesIO(b"pdf"), "ring.pdf")},
                content_type="multipart/form-data",
            )
        self.assertEqual(302, response.status_code)
        day_id = storage.get_event(self.event_id)["days"][0]["id"]
        day = storage.get_event_day(self.event_id, day_id)
        self.assertEqual(3, day["rounds"])
        self.assertEqual("09:12", day["rows"][1]["time"])

    def test_upload_uses_one_choice_for_all_files_and_displays_it(self):
        for rounds, expected_time, expected_slot in (("2", "09:09", "~9 min"), ("3", "09:12", "~12 min")):
            with self.subTest(rounds=rounds), patch("utils.parse_ring_schedule.pdfplumber.open", return_value=FakePdf(self.legacy_page())) as open_pdf:
                response = self.upload(rounds, file_count=2)
                self.assertEqual(302, response.status_code)
                self.assertEqual(2, open_pdf.call_count)
            day_id = storage.get_event(self.event_id)["days"][-1]["id"]
            day = storage.get_event_day(self.event_id, day_id)
            self.assertEqual(int(rounds), day["rounds"])
            self.assertEqual(4, len(day["rows"]))
            self.assertTrue(all(row["time"] == expected_time for row in day["rows"][1::2]))
            shared = self.client.get(f"/share/{self.event_id}/day/{day_id}")
            self.assertEqual(200, shared.status_code)
            self.assertIn(f"{rounds} × 2 min rounds".encode(), shared.data)
            self.assertIn(expected_slot.encode(), shared.data)

    def test_existing_days_do_not_claim_round_count(self):
        day_id = storage.add_event_day(self.event_id, "Old", "ring-cards", rows=[])
        response = self.client.get(f"/share/{self.event_id}/day/{day_id}")
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b"min rounds", response.data)


if __name__ == "__main__":
    unittest.main()