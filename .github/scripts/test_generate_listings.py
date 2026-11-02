import tempfile
import unittest
from pathlib import Path

from check_closed_jobs import LISTING_FILES
from generate_listings import build, parse_location, parse_posted


def make_document(listing, rows):
    return "before\n" + listing.begin_marker + "\n" + rows + "\n" + listing.end_marker + "\nafter\n"


class ParsingTests(unittest.TestCase):
    def test_parse_posted(self):
        self.assertEqual(parse_posted("Oct 5, 2026"), "2026-10-05")
        self.assertEqual(parse_posted("Jan 1, 2027"), "2027-01-01")
        self.assertEqual(parse_posted("not a date"), "")

    def test_parse_location(self):
        self.assertEqual(parse_location("Toronto, ON"), (False, ["Toronto"], ["ON"]))
        self.assertEqual(parse_location("Remote, Canada"), (True, [], []))
        self.assertEqual(parse_location("Ottawa, ON / Remote, Canada"), (True, ["Ottawa"], ["ON"]))
        self.assertEqual(parse_location("Edmonton, AB / Calgary, AB"), (False, ["Edmonton", "Calgary"], ["AB", "AB"]))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        rows = (
            "| Acme | Developer Intern (Winter 2027) | Toronto, ON | "
            "[![Apply](https://badge)](https://acme.wd1.myworkdayjobs.com/Jobs/job/Toronto/Intern---8-months_R1) | Oct 5, 2026 |\n"
            "| ↳ | Platform Intern | Remote, Canada | Closed🔒 | Oct 4, 2026 |\n"
            "| Beta | 4 months Intern (Summer 2027) | Montreal, QC | "
            "[![Apply](https://badge)](https://example.org/job/2) | Sep 1, 2026 |\n"
        )
        self.root.joinpath(LISTING_FILES[0].path).write_text(make_document(LISTING_FILES[0], rows), encoding="utf-8")
        self.root.joinpath(LISTING_FILES[1].path).write_text(make_document(LISTING_FILES[1], ""), encoding="utf-8")

    def test_projection_fields(self):
        data = build(self.root)
        self.assertEqual(data["count"], 3)
        by_company = {(item["company"], item["role"]): item for item in data["listings"]}

        acme = by_company[("Acme", "Developer Intern (Winter 2027)")]
        self.assertEqual(acme["duration_months"], 8)
        self.assertEqual(acme["work_term"], "Winter 2027")
        self.assertEqual(acme["status"], "open")
        self.assertEqual(acme["posted"], "2026-10-05")
        self.assertEqual(acme["cycle"], "2027")

        platform = by_company[("Acme", "Platform Intern")]
        self.assertTrue(platform["remote"])
        self.assertEqual(platform["status"], "closed")
        self.assertIsNone(platform["url"])
        self.assertIsNone(platform["duration_months"])

        beta = by_company[("Beta", "4 months Intern (Summer 2027)")]
        self.assertEqual(beta["duration_months"], 4)
        self.assertEqual(beta["provinces"], ["QC"])
        self.assertEqual(beta["work_term"], "Summer 2027")

    def test_sorted_newest_first(self):
        data = build(self.root)
        self.assertEqual([item["posted"] for item in data["listings"]],
                         ["2026-10-05", "2026-10-04", "2026-09-01"])


if __name__ == "__main__":
    unittest.main()