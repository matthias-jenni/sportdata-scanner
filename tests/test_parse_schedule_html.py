import os
import tempfile
import unittest

from utils.parse_schedule_html import extract_schedule_html


class ParseScheduleHtmlTests(unittest.TestCase):
    def test_new_schedule_format_parses_categories(self):
        root = os.path.dirname(os.path.dirname(__file__))
        html_path = os.path.join(root, "test-data", "1. Alpen Open 2026 - Match Schedule.html")

        fights = extract_schedule_html(html_path)

        self.assertGreater(len(fights), 0)
        self.assertGreaterEqual(len(fights), 37)

        first = fights[0]
        self.assertEqual("10:30", first["time"])
        self.assertEqual("10:50", first["time_end"])
        self.assertEqual("Ring 1", first["tatami"])
        self.assertEqual("01 PF 013 CH F -27 kg", first["category_code"])
        self.assertEqual("", first["phase"])
        self.assertEqual("", first["fight_no"])

        all_codes = [f["category_code"] for f in fights]
        self.assertFalse(any("Weight control" in code for code in all_codes))
        self.assertFalse(any("Session" in code for code in all_codes))

        self.assertTrue(any(f["category_code"] == "07 K1 403 YJ F -56 kg" for f in fights))

    def test_legacy_schedule_format_still_parses(self):
        legacy_html = """
        <html><body>
          <table class="moduletable">
            <tr>
              <th></th>
              <th class="thcenter">Ring 1</th>
              <th class="thcenter">Ring 2</th>
            </tr>
            <tr>
              <td>10:30</td>
              <td title="01 PF 034 OC M -37 kg"><b>01 PF 034 OC M -37 kg</b><br>10:30 - 10:40 (00:10)</td>
              <td title="01 PF 035 OC M -42 kg"><b>01 PF 035 OC M -42 kg</b><br>10:30 - 10:45 (00:15)</td>
            </tr>
          </table>
        </body></html>
        """

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write(legacy_html)
            tmp_path = fh.name

        try:
            fights = extract_schedule_html(tmp_path)
        finally:
            os.unlink(tmp_path)

        self.assertEqual(2, len(fights))
        self.assertEqual("10:30", fights[0]["time"])
        self.assertEqual("10:40", fights[0]["time_end"])
        self.assertEqual("Ring 1", fights[0]["tatami"])
        self.assertEqual("01 PF 034 OC M -37 kg", fights[0]["category_code"])


if __name__ == "__main__":
    unittest.main()
