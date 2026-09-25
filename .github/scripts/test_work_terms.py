import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

import requests

import backfill_work_terms as backfill
from check_closed_jobs import LISTING_FILES
from format_table import format_listings
from work_terms import STATE, classify, load_state


class ClassificationTests(unittest.TestCase):
    def test_reliable_evidence(self):
        cases = [
            ("Software Intern (Summer 2027)", "", "summer"),
            ("Winter 2027 Co-op", "", "winter"),
            ("Internship Fall 2027", "", "fall"),
            ("Intern", "Start Date: May 2027", "summer"),
            ("Intern", "EXPECTED START: JANUARY 2027", "winter"),
            ("Intern", "Internship begins September 8, 2027", "fall"),
            ("Intern", "Term: May-August 2027", "summer"),
            ("Intern", "Co-op starts May 6th, 2027", "summer"),
            ("Intern", "Work term: Summer 2027", "summer"),
            ("Intern", "Summer 2027 Internship", "summer"),
        ]
        for title, description, term in cases:
            with self.subTest(description=description, title=title):
                result, _ = classify(title, description)
                self.assertEqual((result["work_term"], result["start_year"]), (term, 2027))

    def test_precision(self):
        descriptions = [
            "Applications close May 2027", "Application deadline: Summer 2027",
            "Graduation date: May 2027", "Must graduate Summer 2027",
            "Posted May 2027", "Posting date: Summer 2027",
            "Our conference takes place September 2027", "Copyright 2027",
            "Start Date: May", "Start Date: February 30, 2027",
            "Term: Fall/Winter 2027", "Term: Summer 2027 or Winter 2028",
            "Start Date: May 2027\nExpected start: January 2027",
            "Term: Summer 2027\nTerm: Fall 2027", "Term: December-January 2027",
            "Summer 2027 Internship application deadline", "", "May 2027",
            "You previously completed a Summer 2027 internship",
            "Internship starts after graduation in May 2027",
        ]
        for description in descriptions:
            with self.subTest(description=description):
                self.assertIsNone(classify("Developer Intern", description)[0])
        for title in ["Intern Fall/Winter 2027", "Intern Summer 2027 / Fall 2027", "Summer Intern",
                      "Intern Summer 2027 graduates", "Intern Summer 2027-2028"]:
            with self.subTest(title=title):
                self.assertIsNone(classify(title)[0])

    def test_title_description_conflict(self):
        self.assertIsNone(classify("Summer 2027 Intern", "Start Date: January 2027")[0])
        self.assertIsNone(classify("Summer 2027 Intern", "Start Date: May or September 2027")[0])
        self.assertIsNone(classify("Summer 2027 Intern", "Term: September 2027 (4 months)")[0])
        self.assertIsNone(classify("Intern", "Summer 2027 Internship alumni event")[0])


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for listing in LISTING_FILES:
            table = "\n| Company | Role | Location | Apply | Date Posted |\n|---|---|---|---|---|\n"
            if listing == LISTING_FILES[0]:
                table += "| Acme | Developer Intern | Toronto | [![Apply](https://badge)](https://example.org/job/1) | Jan 1 |\n"
            (self.root / listing.path).write_text("before\n" + listing.begin_marker + table + listing.end_marker + "\nafter\n", encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_dry_run_zero_writes_and_review(self):
        before = self.snapshot()
        result = backfill.run(self.root, fetch=True, fetcher=lambda *_: ("fetched", "Start Date: May 6, 2027"))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(result["summary"]["newly_classifiable"], 1)
        self.assertEqual(result["classifications_by_term"], {"Summer 2027": 1})
        self.assertEqual(result["review"][0]["evidence"], "Start Date: May 6, 2027")

    def test_cli_default_is_dry_run(self):
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(Path(backfill.__file__).resolve())],
                                cwd=self.root, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["summary"]["processed"], 1)
        self.assertEqual(before, self.snapshot())

    def test_duplicate_identities_are_not_classified(self):
        path = self.root / "README.md"
        text = path.read_text().replace("Developer Intern", "Summer 2027 Intern")
        line = next(line for line in text.splitlines() if line.startswith("| Acme"))
        text = text.replace(line, line + "\n" + line.replace("/job/1", "/job/2"))
        path.write_text(text, encoding="utf-8")
        result = backfill.run(self.root)
        self.assertEqual(result["summary"]["ambiguous_identity"], 1)
        self.assertEqual(result["summary"]["newly_classifiable"], 0)

    def test_cursor_pages_a_dry_run(self):
        first = backfill.run(self.root)
        second = backfill.run(self.root, after_key=first["next_after_key"])
        self.assertEqual(second["summary"]["processed"], 0)
        self.assertFalse((self.root / STATE).exists())

    def test_failed_url_does_not_stop_next_job(self):
        path = self.root / "README.md"
        text = path.read_text()
        row = "| Other | Winter 2027 Intern | Montreal | Closed🔒 | Jan 2 |\n"
        path.write_text(text.replace(LISTING_FILES[0].end_marker, row + LISTING_FILES[0].end_marker), encoding="utf-8")
        result = backfill.run(self.root, fetch=True, fetcher=lambda *_: ("fetch_failure", "HTTP 410"))
        self.assertEqual(result["summary"]["processed"], 2)
        self.assertEqual(result["summary"]["fetch_failures"], 1)
        self.assertEqual(result["summary"]["newly_classifiable"], 1)

    def test_formatter_still_runs_without_third_party_dependencies(self):
        subprocess.run([sys.executable, "-B", "-S", str(Path(backfill.__file__).with_name("format_table.py").resolve())],
                       cwd=self.root, capture_output=True, text=True, check=True)

    def test_apply_render_original_title_and_idempotence(self):
        fetcher = Mock(return_value=("fetched", "Start Date: May 2027"))
        backfill.run(self.root, apply=True, fetch=True, fetcher=fetcher)
        self.assertIn("| Developer Intern (Summer 2027) |", (self.root / "README.md").read_text())
        entry = next(iter(load_state(self.root / STATE)["jobs"].values()))
        self.assertEqual(entry["original_title"], "Developer Intern")
        before = self.snapshot()
        result = backfill.run(self.root, apply=True, fetch=True, fetcher=fetcher)
        self.assertEqual(result["summary"]["already_classified"], 1)
        self.assertEqual(before, self.snapshot())
        fetcher.assert_called_once()
        format_listings(self.root)
        once = self.snapshot()
        format_listings(self.root)
        self.assertEqual(once, self.snapshot())

    def test_escaped_pipes_and_continuation_company(self):
        path = self.root / "README.md"
        path.write_text(path.read_text().replace("| Acme |", "| Acme \\| Labs |").replace(
            "Developer Intern", "Developer \\| Data Intern"), encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        backfill.run(self.root, apply=True, fetch=True, fetcher=lambda *_: ("fetched", "Start Date: May 2027"))
        after = path.read_text(encoding="utf-8")
        self.assertEqual(after, before.replace("Developer \\| Data Intern", "Developer \\| Data Intern (Summer 2027)"))

    def test_failure_checkpoint_retry_and_closed_row(self):
        fetcher = Mock(return_value=("fetch_failure", "HTTP 404"))
        result = backfill.run(self.root, apply=True, fetch=True, fetcher=fetcher)
        self.assertEqual(result["summary"]["fetch_failures"], 1)
        self.assertNotIn("Unknown", (self.root / "README.md").read_text())
        backfill.run(self.root, apply=True, fetch=True, fetcher=fetcher)
        fetcher.assert_called_once()
        backfill.run(self.root, fetch=True, retry_unclassified=True, fetcher=fetcher)
        self.assertEqual(fetcher.call_count, 2)
        path = self.root / "README.md"
        path.write_text(path.read_text().replace("[![Apply](https://badge)](https://example.org/job/1)", "Closed🔒"), encoding="utf-8")
        self.assertEqual(backfill.run(self.root)["summary"]["skipped_checkpoint"], 1)

    def test_batches_resume_and_title_only_never_fetches(self):
        path = self.root / "README.md"
        text = path.read_text().replace("Developer Intern", "Summer 2027 Intern")
        text = text.replace(LISTING_FILES[0].end_marker, "| Other | Winter 2027 Intern | Montreal | Closed🔒 | Jan 2 |\n" + LISTING_FILES[0].end_marker)
        path.write_text(text, encoding="utf-8")
        fetcher = Mock(side_effect=AssertionError("Unexpected network"))
        first = backfill.run(self.root, apply=True, fetch=True, batch_size=1, fetcher=fetcher)
        self.assertEqual(first["summary"]["deferred"], 1)
        second = backfill.run(self.root, apply=True, fetch=True, batch_size=1, fetcher=fetcher)
        self.assertEqual(second["summary"]["newly_classifiable"], 1)
        self.assertEqual(second["summary"]["already_classified"], 1)


class FetchTests(unittest.TestCase):
    url = "https://job-boards.greenhouse.io/acme/jobs/123"

    def fetcher(self, payload=None, status=200):
        fetcher = backfill.Fetcher()
        response = Mock(status_code=status)
        response.iter_content.return_value = [json.dumps(payload).encode()]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        fetcher.session = Mock()
        fetcher.session.get.return_value = response
        return fetcher

    def test_identity_and_cached_response(self):
        fetcher = self.fetcher({"id": 123, "title": "Developer Intern", "content": "<p>Start Date: May 2027</p>"})
        self.assertEqual(fetcher(self.url, "Developer Intern")[0], "fetched")
        self.assertEqual(fetcher(self.url, "Developer Intern")[0], "fetched")
        fetcher.session.get.assert_called_once()
        self.assertEqual(fetcher(self.url, "Different Intern")[0], "fetch_failure")
        self.assertFalse(fetcher.session.get.call_args.kwargs["allow_redirects"])

    def test_failures_and_wrong_posting(self):
        for status in [301, 403, 404, 410, 429, 500]:
            self.assertEqual(self.fetcher(status=status)(self.url, "Intern")[0], "fetch_failure")
        self.assertEqual(self.fetcher({"id": 999, "title": "Intern", "content": "Summer 2027"})(self.url, "Intern")[0], "fetch_failure")
        fetcher = self.fetcher()
        fetcher.session.get.side_effect = requests.Timeout("timeout")
        self.assertEqual(fetcher(self.url, "Intern")[0], "fetch_failure")
        fetcher = self.fetcher()
        self.assertEqual(fetcher("https://example.org/job", "Intern")[0], "unsupported")
        fetcher.session.get.assert_not_called()
        self.assertEqual(fetcher("https://[invalid", "Intern")[0], "fetch_failure")

    def test_workday_path_identity(self):
        url = "https://acme.wd1.myworkdayjobs.com/Jobs/job/Toronto/Intern_R123"
        payload = {"jobPostingInfo": {"id": "123", "title": "Intern", "externalPath": "/job/Toronto/Intern_R123",
                                      "jobDescription": "<p>Expected start: January 2027</p>"}}
        self.assertEqual(self.fetcher(payload)(url, "Intern")[0], "fetched")
        payload["jobPostingInfo"]["externalPath"] = "/job/Toronto/Intern_R999"
        self.assertEqual(self.fetcher(payload)(url, "Intern")[0], "fetch_failure")


if __name__ == "__main__":
    unittest.main()
