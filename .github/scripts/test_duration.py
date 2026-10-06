import unittest

from duration import explicit_months, extract


class ExtractTests(unittest.TestCase):
    def test_title_and_url_sources(self):
        cases = [
            ("Intern (8 months)", "", "", 8, "title"),
            ("Intern 16months", "", "", 16, "title"),
            ("Intern", "https://x/job/Software---8-months-_R1", "", 8, "url"),
            ("Intern", "", "Duration: 4 months", 4, "description"),
            ("Intern", "", "This internship is a 12-month co-op", 12, "description"),
            ("Intern", "", "4 month internship in Toronto", 4, "description"),
        ]
        for title, url, description, months, source in cases:
            with self.subTest(title=title, url=url, description=description):
                result = extract(title, url, description)
                self.assertIsNotNone(result)
                self.assertEqual(result["duration_months"], months)
                self.assertEqual(result["duration_source"], source)

    def test_ambiguous_or_conflicting_are_unknown(self):
        cases = [
            ("Intern 4-8 months", "", ""),
            ("Intern", "https://x/job/Data---4--8--12-months-_R1", ""),
            ("Intern", "https://x/job/Data---4/8/12-months", ""),
            ("Intern", "", "Duration: 4 to 8 months"),
            ("Intern (8 months)", "", "Duration: 4 months"),
            ("Intern", "https://x/job/8-months", "Duration: 4 months"),
        ]
        for title, url, description in cases:
            with self.subTest(title=title, url=url, description=description):
                self.assertIsNone(extract(title, url, description))

    def test_non_duration_context_is_ignored(self):
        descriptions = [
            "Applications close in 8 months",
            "Deadline: 4 months from now",
            "Start Date: May 2027",
            "Term: September 2027 (4 months)",
            "Posted 6 months ago",
            "Duration: 25 months",
            "Duration: 0 months",
        ]
        for description in descriptions:
            with self.subTest(description=description):
                self.assertIsNone(extract("Developer Intern", "", description))

    def test_plural_helper(self):
        self.assertEqual(explicit_months("Intern, 8 months"), {8})
        self.assertEqual(explicit_months("1 month term"), {1})
        self.assertEqual(explicit_months("no duration here"), set())
        self.assertEqual(explicit_months(""), set())


if __name__ == "__main__":
    unittest.main()