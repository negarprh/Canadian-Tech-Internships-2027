"""Regression checks for job data hidden behind successful ATS page shells."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests

import check_closed_jobs as checker


ASHBY = "https://jobs.ashbyhq.com/zip/2bc7327b-1c06-418a-beeb-bec1dd70480e/"
ORACLE = "https://eezy.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/20508"


def response(payload=None, status=200, url=ORACLE, text=""):
    result = Mock(status_code=status, url=url, text=text)
    result.json.return_value = payload
    return result


class OracleTests(unittest.TestCase):
    def check(self, reply):
        api = Mock()
        api.get.return_value = reply
        browser = Mock()
        result = checker.check_url(browser, ORACLE + "?utm_source=repo", public_api_session=api)
        browser.get.assert_not_called()
        query = parse_qs(urlparse(api.get.call_args.args[0]).query)
        self.assertEqual(query["finder"], ['ById;Id="20508",siteNumber=CX'])
        return result.status

    def test_matching_job_is_open_even_with_closed_phrase_in_description(self):
        self.assertEqual(self.check(response({"items": [{
            "Id": "20508", "Title": "Intern", "ExternalDescriptionStr": "this job is closed",
        }]})), "OPEN")

    def test_explicit_empty_result_is_closed(self):
        self.assertEqual(self.check(response({"items": [], "count": 0, "hasMore": False})), "CLOSED")

    def test_incomplete_or_wrong_job_results_are_unknown(self):
        for payload in [None, [], {}, {"items": []},
                        {"items": [], "count": 0, "hasMore": True},
                        {"items": [{"Id": "999", "Title": "Other job"}]}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.check(response(payload)), "UNKNOWN")

    def test_http_errors_and_invalid_json_are_unknown(self):
        for code in [403, 404, 410, 429, 500]:
            with self.subTest(code=code):
                self.assertEqual(self.check(response(status=code)), "UNKNOWN")
        reply = response()
        reply.json.side_effect = requests.JSONDecodeError("invalid", "", 0)
        self.assertEqual(self.check(reply), "UNKNOWN")

    def test_timeout_and_unsupported_route_are_unknown(self):
        api = Mock()
        api.get.side_effect = requests.Timeout()
        self.assertEqual(checker.check_oracle_api(api, ORACLE).status, "UNKNOWN")
        api.reset_mock()
        self.assertEqual(checker.check_oracle_api(api, ORACLE.replace('/job/', '/jobs/')).status, "UNKNOWN")
        api.get.assert_not_called()


class AshbyTests(unittest.TestCase):
    def check(self, payload, url=ASHBY):
        html = '<script>window.__appData = ' + json.dumps(payload) + '; doSomething();</script>'
        browser = Mock()
        browser.get.return_value = response(url=url, text=html)
        return checker.check_url(browser, ASHBY, public_api_session=Mock()).status

    def test_null_posting_is_closed(self):
        self.assertEqual(self.check({"maintenanceMode": False, "posting": None}), "CLOSED")

    def test_open_including_unlisted_and_application_links(self):
        for listed in [True, False]:
            for suffix in ["", "application?utm_source=repo"]:
                with self.subTest(listed=listed, suffix=suffix):
                    self.assertEqual(self.check({"maintenanceMode": False, "posting": {
                        "id": ASHBY.rstrip('/').split('/')[-1], "title": "Intern",
                        "isListed": listed, "descriptionHtml": "this job is closed",
                    }}, ASHBY + suffix), "OPEN")

    def test_missing_state_maintenance_and_wrong_job_are_unknown(self):
        for payload in [{}, {"posting": None}, {"maintenanceMode": True, "posting": None},
                        {"maintenanceMode": False},
                        {"maintenanceMode": False, "posting": {"id": "other", "title": "Intern"}}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.check(payload), "UNKNOWN")

    def test_board_redirect_is_not_null_job_closure(self):
        self.assertEqual(self.check({"maintenanceMode": False, "posting": None},
                                   "https://jobs.ashbyhq.com/zip"), "UNKNOWN")

    def test_shell_or_broken_json_is_unknown(self):
        for html in ["<html></html>", "<script>window.__appData = {broken}</script>"]:
            self.assertEqual(checker.check_ashby_page(ASHBY, html).status, "UNKNOWN")


class SmartRecruitersTests(unittest.TestCase):
    url = "https://jobs.smartrecruiters.com/GDMSI/744000124175808"
    expired = '<button class="is-disabled" disabled>Sorry, this job has expired</button>'

    def check(self, html="", page_status=200, api_status=200, error=None):
        api = Mock()
        api.get.return_value = response({"id": "744000124175808", "name": "Intern"},
                                       status=api_status)
        browser = Mock()
        browser.get.return_value = response(status=page_status, url=self.url, text=html)
        browser.get.side_effect = error
        return checker.check_url(browser, self.url, public_api_session=api).status

    def test_expired_page_overrides_retained_api_details(self):
        self.assertEqual(self.check(self.expired), "CLOSED")

    def test_open_page_with_api_details(self):
        self.assertEqual(self.check('<button>I\'m interested</button>'), "OPEN")

    def test_description_closure_phrase_does_not_override_api(self):
        self.assertEqual(self.check('<p>This position has been filled is an example message.</p>'
                                    '<button>I\'m interested</button>'), "OPEN")

    def test_french_expired_application_control(self):
        self.assertEqual(self.check('<button disabled>Désolé, ce poste a expiré</button>'), "CLOSED")

    def test_expired_text_outside_disabled_control_is_not_closure(self):
        for html in ['<p>Sorry, this job has expired</p>',
                     '<script>' + self.expired + '</script>',
                     '<button>Sorry, this job has expired</button>']:
            with self.subTest(html=html):
                self.assertEqual(self.check(html), "OPEN")

    def test_expired_control_with_nested_markup(self):
        self.assertEqual(self.check('<button disabled><span>Sorry, this job</span>\n'
                                    ' has expired</button>'), "CLOSED")

    def test_unavailable_page_does_not_trust_retained_api_details(self):
        for code in [403, 429, 500]:
            with self.subTest(code=code):
                self.assertEqual(self.check(page_status=code), "UNKNOWN")
        self.assertEqual(self.check(error=requests.Timeout()), "UNKNOWN")

    def test_missing_posting_and_api_failure_fallback(self):
        for code in [404, 410]:
            self.assertEqual(self.check(api_status=code), "CLOSED")
            self.assertEqual(self.check(page_status=code), "CLOSED")
        self.assertEqual(self.check(self.expired, api_status=503), "CLOSED")
        self.assertEqual(self.check(api_status=503), "UNKNOWN")


class PhenomTests(unittest.TestCase):
    url = "https://careers.tranetechnologies.com/global/en/job/JR-7608/2027-BrainBox-AI-Intern"

    def check(self, payload, status=200):
        html = (
            '<script>phApp.ddo = ' + json.dumps(payload) + ';</script>'
            '<div ph-page-state="expired" class="hide job-expired-view">'
            'Unfortunately, we are no longer accepting applications.</div>'
        )
        browser = Mock()
        browser.get.return_value = response(status=status, url=self.url, text=html)
        return checker.check_url(browser, self.url, public_api_session=Mock()).status

    def test_open_job_with_hidden_expired_template(self):
        self.assertEqual(self.check({"jobDetail": {"status": 200, "data": {"job": {
            "jobId": "JR-7608", "title": "2027 BrainBox AI Intern", "postingStatus": "OPEN",
        }}}}), "OPEN")

    def test_inconclusive_data_does_not_use_hidden_template(self):
        for payload in [None, {}, {"jobDetail": {"status": 500}},
                        {"jobDetail": {"status": 200, "data": {"job": {
                            "jobId": "OTHER", "title": "Other job", "postingStatus": "OPEN",
                        }}}}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.check(payload), "UNKNOWN")
        self.assertEqual(checker.check_phenom_page(self.url, "phApp.ddo = {broken").status, "UNKNOWN")

    def test_gone_job_still_closes(self):
        self.assertEqual(self.check({"jobDetail": {
            "status": 200, "hits": 0, "totalHits": 0, "data": {},
        }}, status=410), "CLOSED")

    def test_other_sites_still_detect_visible_closed_message(self):
        browser = Mock()
        browser.get.return_value = response(url="https://example.com/job/1",
                                           text="This job is no longer available")
        self.assertEqual(checker.check_url(browser, "https://example.com/job/1",
                                          public_api_session=Mock()).status, "CLOSED")


class CareersApiTests(unittest.TestCase):
    bamboo = "https://example.bamboohr.com/careers/64"
    hibob = "https://example.careers.hibob.com/jobs/3eda8396-7ba9-42bf-889c-8e586b7e105e"

    def check(self, url, reply):
        browser, api = Mock(), Mock()
        api.get.return_value = reply
        result = checker.check_url(browser, url + "/?utm_source=repo", public_api_session=api)
        browser.get.assert_not_called()
        return result.status, api.get

    def test_bamboo_checks_the_requested_opening(self):
        job = {"jobOpeningName": "Intern", "jobOpeningShareUrl": self.bamboo}
        for state, expected in [("Open", "OPEN"), ("Filled", "CLOSED"), ("Canceled", "CLOSED"),
                                ("On Hold", "UNKNOWN")]:
            with self.subTest(state=state):
                reply = response({"result": {"jobOpening": {**job, "jobOpeningStatus": state}}})
                status, get = self.check(self.bamboo + "/apply", reply)
                self.assertEqual(status, expected)
                self.assertEqual(get.call_args.args, (self.bamboo + "/detail",))
        job.update(jobOpeningShareUrl=self.bamboo + "0", jobOpeningStatus="Filled")
        self.assertEqual(self.check(self.bamboo, response({"result": {"jobOpening": job}}))[0],
                         "UNKNOWN")

    def test_hibob_uses_public_posting_as_referer(self):
        reply = response({"data": {"jobAd": {"title": "Intern", "applicationForm": {"id": "91"}}}})
        status, get = self.check(self.hibob, reply)
        self.assertEqual(status, "OPEN")
        self.assertEqual(get.call_args.args, (self.hibob.replace("/jobs/", "/api/job-ad/")
                                              + "/application-form",))
        self.assertEqual(get.call_args.kwargs["headers"]["Referer"], self.hibob)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_only_provider_missing_responses_close(self):
        for url, missing in [(self.bamboo, {"type": "not_found"}),
                             (self.hibob, {"message": "Could not find resource"})]:
            for code, expected in [(200, "UNKNOWN"), (404, "CLOSED"), (410, "CLOSED"),
                                   (302, "UNKNOWN"), (403, "UNKNOWN"), (429, "UNKNOWN"), (503, "UNKNOWN")]:
                with self.subTest(url=url, code=code):
                    self.assertEqual(self.check(url, response(missing, status=code))[0], expected)
            for reply in [response({}, status=404), response({"result": [], "data": []}),
                          response({"data": {"jobAd": {"title": "Intern"}}})]:
                self.assertEqual(self.check(url, reply)[0], "UNKNOWN")
            bad_json = response(status=404)
            bad_json.json.side_effect = requests.JSONDecodeError("html", "", 0)
            self.assertEqual(self.check(url, bad_json)[0], "UNKNOWN")
            api = Mock()
            api.get.side_effect = requests.Timeout()
            self.assertEqual(checker.check_url(Mock(), url, public_api_session=api).status, "UNKNOWN")

    def test_board_and_lookalike_hosts_are_not_job_endpoints(self):
        for url in [self.bamboo.rsplit("/", 1)[0], self.hibob.rsplit("/", 1)[0]]:
            status, get = self.check(url, response(status=404))
            self.assertEqual(status, "UNKNOWN")
            get.assert_not_called()
        self.assertEqual(checker.ats_name("https://notbamboohr.com/careers/64"), "generic")
        self.assertEqual(checker.ats_name("https://careers.hibob.com.example.org"), "generic")


class MissingJobRedirectTests(unittest.TestCase):
    postings = [
        ("https://apply.workable.com/zaber", "/j/EAB5877DC3/", "not_found=true"),
        ("https://ats.rippling.com/example/jobs", "/e52b9e0d-0c04-42be-8da6-288203e18e52",
         "rr_message=job_not_found"),
        ("https://app.careerpuck.com/job-board/lyft", "/job/8796054002", "notFound=8796054002"),
    ]

    def check(self, url, final, code=200):
        browser = Mock()
        browser.get.return_value = response(url=final, status=code)
        return checker.check_url(browser, url, public_api_session=Mock()).status

    def test_only_matching_board_and_missing_job_marker_close(self):
        for board, path, marker in self.postings:
            url = board + path
            for final, code, expected in [
                (board + "/?" + marker, 200, "CLOSED"), (url, 200, "OPEN"),
                (url, 410, "CLOSED"), (url, 403, "UNKNOWN"),
                (board, 200, "UNKNOWN"), (board + "?" + marker, 503, "UNKNOWN"),
                (board + "?notFound=123", 200, "UNKNOWN"),
                (board + "/other?" + marker, 200, "UNKNOWN"),
                ("https://auth.example.org/login?" + marker, 404, "UNKNOWN"),
            ]:
                with self.subTest(url=url, final=final, code=code):
                    self.assertEqual(self.check(url, final, code), expected)
            self.assertEqual(self.check(board, board + "?" + marker), "UNKNOWN")

    def test_rippling_locale_preserves_job_and_board_identity(self):
        board, path, marker = self.postings[1]
        localized = board.replace(".com/", ".com/en-CA/")
        self.assertEqual(self.check(board + path, localized + path), "OPEN")
        self.assertEqual(self.check(board + path, localized + "?" + marker), "CLOSED")
        self.assertEqual(self.check(localized + path, localized + "?" + marker), "CLOSED")
        other = localized.replace("/example/", "/another-company/")
        self.assertEqual(self.check(board + path, other + "?" + marker), "UNKNOWN")


class ListingUpdateTests(unittest.TestCase):
    def test_only_confirmed_closed_badge_is_replaced_in_both_tables(self):
        closed = "https://jobs.example.com/Co-op-(Waterdown,-CAN)-ON/1430128900/"
        urls = [closed, "https://example.com/open", "https://example.com/unknown"]
        badge = "[![Apply](https://img.shields.io/badge/Apply)]"
        rows = [f"| Employer | Intern | Toronto | {badge}({url}) | Nov 1 |\n" for url in urls]
        statuses = dict(zip(urls, ["CLOSED", "OPEN", "UNKNOWN"]))

        def check(_session, url, *_args):
            return checker.CheckResult(statuses[url], "fixture")

        with TemporaryDirectory() as tmp:
            files = [checker.ListingFile(Path(tmp) / f.path, f.begin_marker, f.end_marker)
                     for f in checker.LISTING_FILES]
            originals = []
            for f in files:
                original = rows[0] + f.begin_marker + "\n" + "".join(rows) + f.end_marker + "\n"
                originals.append(original)
                f.path.write_text(original, encoding="utf-8")
            with (patch.object(checker, "LISTING_FILES", files),
                  patch.object(checker, "REPORT", Path(tmp) / "report.md"),
                  patch.object(checker.time, "sleep"),
                  patch.object(checker, "check_url", side_effect=check) as check_mock):
                checker.main()
            self.assertEqual(check_mock.call_count, len(urls))
            for f, original in zip(files, originals):
                before, table = original.split(f.begin_marker)
                expected = before + f.begin_marker + table.replace(f"{badge}({closed})", "Closed🔒")
                self.assertEqual(f.path.read_text(encoding="utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
