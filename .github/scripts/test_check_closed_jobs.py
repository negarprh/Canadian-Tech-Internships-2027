"""Regression checks for job data hidden behind successful ATS page shells."""

import json
import unittest
from unittest.mock import Mock
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


if __name__ == "__main__":
    unittest.main()
