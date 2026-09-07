"""Find definitely closed internship postings without reformatting listing files.

The checker is deliberately conservative: an inaccessible or inconclusive page is
reported as UNKNOWN and is left untouched.  A row is changed only when the ATS
returns a permanent missing status, redirects a known ATS listing to its search
page, or displays an explicit closed-posting message.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Literal
from urllib.parse import urlencode, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


REPORT = Path("link-check-report.md")


@dataclass(frozen=True)
class ListingFile:
    """A README and the exact marker pair that bounds its listings table."""

    path: Path
    begin_marker: str
    end_marker: str


LISTING_FILES = (
    ListingFile(
        Path("README.md"),
        "<!-- BEGIN:INTERNSHIPS_TABLE -->",
        "<!-- END:INTERNSHIPS_TABLE -->",
    ),
    ListingFile(
        Path("README-2026.md"),
        "<!-- BEGIN:INTERNSHIPS_2026_TABLE -->",
        "<!-- END:INTERNSHIPS_2026_TABLE -->",
    ),
)

# This expression matches only the Apply control itself. Its replacement is
# intentionally a single table-cell value, so no pipes, whitespace, rows, or
# other listing-file content can be reformatted by this script.
APPLY_LINK = re.compile(
    r"\[!\[Apply\]\([^)]+?\)\]\((https?://[^)\s]+)\)", re.IGNORECASE
)

Status = Literal["CLOSED", "OPEN", "UNKNOWN"]

# These are explicit messages from ATS error/closed pages. Keep the phrases
# specific: generic words such as "closed" or "unavailable" create false
# positives on otherwise valid career pages.
CLOSED_PHRASES = {
    "generic": (
        "this position has been filled",
        "position has been filled",
        "this job posting is no longer active",
        "this job posting has expired",
        "this posting has closed",
        "this job is no longer available",
        "this job is no longer posted",
        "this job is closed",
        "this position is closed",
        "this position is no longer available",
        "this role is no longer available",
        "this vacancy is no longer available",
        "this opening is no longer available",
        "no longer accepting applications",
        "no longer accepting candidates",
        "requisition closed",
        "job requisition is no longer available",
        "job posting is no longer available",
        "the job you are looking for is no longer available",
        "the job you are trying to view is no longer available",
        "the requisition you are looking for is no longer available",
        "the job posting you are looking for does not exist",
        "this job posting does not exist",
        "this job no longer exists",
        "this position no longer exists",
        "this requisition no longer exists",
        "this requisition does not exist",
        "this opportunity no longer exists",
        "this opportunity does not exist",
        "the page you are looking for doesn't exist",
        "the page you are looking for does not exist",
        "looks like this job no longer exists",
    ),
    "workday": (
        "job closed",
        "job posting has closed",
        "job application is no longer available",
        "this job does not exist",
        "the job you are looking for does not exist",
    ),
    "greenhouse": ("this job is no longer available",),
    "lever": ("this job is no longer available",),
    "ashby": ("this job is no longer available",),
    "eightfold": ("this job is no longer available",),
    "successfactors": ("this job is no longer available",),
}

# A redirect alone is only meaningful for sites where the original URL is an
# ATS job detail route. Do not classify redirects for generic company sites:
# those often redirect valid applications to an SSO, locale, or consent page.
KNOWN_ATS = {
    "workday",
    "greenhouse",
    "lever",
    "ashby",
    "eightfold",
    "icims",
    "smartrecruiters",
    "successfactors",
}
SEARCH_PATH_PARTS = ("/search", "/jobsearch", "/jobs/search", "/careers/search")
LOCALE_SEGMENT = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}


class VisibleText(HTMLParser):
    """Collect readable text while ignoring scripts and styles."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


@dataclass(frozen=True)
class CheckResult:
    status: Status
    reason: str


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(UA)
    return session


def make_workday_session() -> requests.Session:
    """Create a session for Workday's JSON API without browser headers.

    Workday's API may reject the browser Accept-Language profile even while its
    public SPA shell returns 200, so this session intentionally retains
    requests' default User-Agent and sends only the desired response type.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"Accept": "application/json"})
    return session


def make_public_api_session() -> requests.Session:
    """Create a session for public ATS JSON endpoints."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"Accept": "application/json"})
    return session


def ats_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "workday" in host or "myworkdayjobs" in host:
        return "workday"
    if "greenhouse" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq" in host:
        return "ashby"
    if host.endswith(".oraclecloud.com"):
        return "oracle"
    if "eightfold" in host:
        return "eightfold"
    if "icims.com" in host:
        return "icims"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "successfactors" in host or "sapfioritalent" in host:
        return "successfactors"
    return "generic"


def readable_text(html: str) -> str:
    parser = VisibleText()
    try:
        parser.feed(html)
        parser.close()
        content = " ".join(parser.parts)
    except Exception:
        # A malformed page should not become a failed check merely because its
        # HTML cannot be parsed; matching the raw response is still safe.
        content = html
    return re.sub(r"\s+", " ", unescape(content)).casefold()


def closed_phrase(text: str, provider: str) -> str | None:
    for phrase in CLOSED_PHRASES["generic"] + CLOSED_PHRASES.get(provider, ()):
        if phrase.casefold() in text:
            return phrase
    return None


def workday_api_url(url: str) -> str | None:
    """Convert supported public Workday URLs to their authoritative JSON URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]

    if host.endswith("myworkdaysite.com"):
        # /recruiting/{tenant}/{site}/job/{location}/{slug_and_id}
        if len(parts) < 5 or parts[0].casefold() != "recruiting" or parts[3].casefold() != "job":
            return None
        tenant, site, job_parts = parts[1], parts[2], parts[4:]
    else:
        # {tenant}.wdN.myworkdayjobs.com/[locale]/{site}/job/{location}/{slug_and_id}
        tenant = host.split(".")[0]
        if parts and LOCALE_SEGMENT.fullmatch(parts[0]):
            parts = parts[1:]
        if len(parts) < 3 or parts[1].casefold() != "job":
            return None
        site, job_parts = parts[0], parts[2:]

    api_path = f"/wday/cxs/{tenant}/{site}/job/" + "/".join(job_parts)
    return urlunparse((parsed.scheme, parsed.netloc, api_path, "", "", ""))


def check_workday_api(session: requests.Session, url: str) -> CheckResult | None:
    """Classify a Workday link using the data source used by its own frontend."""
    endpoint = workday_api_url(url)
    if endpoint is None:
        return None

    try:
        response = session.get(endpoint, allow_redirects=True, timeout=(10, 30))
    except requests.RequestException as error:
        return CheckResult("UNKNOWN", f"Workday API failed: {type(error).__name__}")

    if response.status_code in {404, 410}:
        return CheckResult("CLOSED", f"Workday API HTTP {response.status_code}")

    try:
        payload = response.json()
    except requests.JSONDecodeError:
        payload = None

    if response.status_code == 200:
        job = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if isinstance(job, dict) and job.get("id") and job.get("title"):
            return CheckResult("OPEN", "Workday API returned jobPostingInfo")
        return CheckResult("UNKNOWN", "Workday API returned 200 without a job posting")

    # Deleted or no-longer-public Workday requisitions return this structured
    # application error while their public SPA page misleadingly remains 200.
    if response.status_code == 403 and isinstance(payload, dict):
        error_code = str(payload.get("errorCode", "")).casefold()
        message = str(payload.get("message", "")).casefold()
        if error_code == "s22" and message == "permission denied":
            return CheckResult("CLOSED", "Workday API S22: permission denied")

    return CheckResult("UNKNOWN", f"Workday API HTTP {response.status_code} (inconclusive)")


def greenhouse_api_url(url: str) -> str | None:
    """Convert a public Greenhouse job URL to its public Job Board API URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return None
    if len(parts) != 3 or parts[1].casefold() != "jobs" or not parts[2].isdigit():
        return None

    return urlunparse(
        ("https", "boards-api.greenhouse.io", f"/v1/boards/{parts[0]}/jobs/{parts[2]}", "", "", "")
    )


def smartrecruiters_api_url(url: str) -> str | None:
    """Convert a public SmartRecruiters job URL to its public Posting API URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host != "jobs.smartrecruiters.com" or len(parts) != 2:
        return None

    company, posting = parts
    return urlunparse(
        ("https", "api.smartrecruiters.com", f"/v1/companies/{company}/postings/{posting}", "", "", "")
    )


def check_public_ats_api(session: requests.Session, url: str, provider: str) -> CheckResult | None:
    """Classify public Greenhouse and SmartRecruiters postings via their APIs.

    These APIs expose only published job postings.  A 404/410 is therefore a
    high-confidence closure signal; every other non-success status remains
    inconclusive and falls back to the public posting page.
    """
    endpoint = {
        "greenhouse": greenhouse_api_url,
        "smartrecruiters": smartrecruiters_api_url,
    }.get(provider, lambda _: None)(url)
    if endpoint is None:
        return None

    try:
        response = session.get(endpoint, allow_redirects=True, timeout=(10, 30))
    except requests.RequestException as error:
        return CheckResult("UNKNOWN", f"{provider} API failed: {type(error).__name__}")

    if response.status_code in {404, 410}:
        return CheckResult("CLOSED", f"{provider} API HTTP {response.status_code}")
    if response.status_code != 200:
        return CheckResult("UNKNOWN", f"{provider} API HTTP {response.status_code} (inconclusive)")

    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return CheckResult("UNKNOWN", f"{provider} API returned invalid JSON")

    if not isinstance(payload, dict):
        return CheckResult("UNKNOWN", f"{provider} API returned 200 without a job posting")

    # Greenhouse calls this field "title"; SmartRecruiters calls it "name".
    title = payload.get("title") or payload.get("name")
    if title and (payload.get("id") or payload.get("uuid")):
        return CheckResult("OPEN", f"{provider} API returned a published job")
    return CheckResult("UNKNOWN", f"{provider} API returned 200 without a job posting")


def check_oracle_api(session: requests.Session, url: str) -> CheckResult:
    """Oracle's UI treats an empty site-scoped detail result as job-expired."""
    parsed = urlparse(url)
    route = re.fullmatch(
        r"/hcmUI/CandidateExperience/[^/]+/sites/([\w-]+)/job/(\d+)(?:/apply)?/?",
        parsed.path,
    )
    if route is None:
        return CheckResult("UNKNOWN", "Unsupported Oracle job route")
    site, job_id = route.groups()
    query = urlencode({"onlyData": "true", "finder": f'ById;Id="{job_id}",siteNumber={site}'})
    endpoint = urlunparse(
        (parsed.scheme, parsed.netloc,
         "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails", "", query, "")
    )
    try:
        response = session.get(endpoint, allow_redirects=True, timeout=(10, 30))
    except requests.RequestException as error:
        return CheckResult("UNKNOWN", f"Oracle API failed: {type(error).__name__}")
    if response.status_code != 200:
        return CheckResult("UNKNOWN", f"Oracle API HTTP {response.status_code} (inconclusive)")
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        return CheckResult("UNKNOWN", "Oracle API returned invalid JSON")
    if isinstance(payload, dict):
        items = payload.get("items")
        if items == [] and payload.get("count") == 0 and payload.get("hasMore") is False:
            return CheckResult("CLOSED", "Oracle API returned no posting for this job and site")
        if isinstance(items, list) and any(
            isinstance(job, dict) and str(job.get("Id")) == job_id and job.get("Title")
            for job in items
        ):
            return CheckResult("OPEN", "Oracle API returned the requested job")
    return CheckResult("UNKNOWN", "Oracle API returned an unrecognized job response")


def check_ashby_page(url: str, html: str) -> CheckResult:
    """Check the posting directly: unlisted jobs can still accept applications."""
    parsed = urlparse(url)
    route = re.fullmatch(
        r"/[^/]+/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})(?:/application)?/?",
        parsed.path,
    )
    if parsed.hostname != "jobs.ashbyhq.com" or route is None:
        return CheckResult("UNKNOWN", "Unsupported Ashby job route")
    match = re.search(r"\bwindow\.__appData\s*=\s*", html)
    if match:
        try:
            payload, _ = json.JSONDecoder().raw_decode(html[match.end():])
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("maintenanceMode") is False:
            if "posting" in payload and payload["posting"] is None:
                return CheckResult("CLOSED", "Ashby page explicitly returned posting: null")
            posting = payload.get("posting")
            if (
                isinstance(posting, dict)
                and str(posting.get("id", "")).casefold() == route[1].casefold()
                and posting.get("title")
            ):
                return CheckResult("OPEN", "Ashby page returned the requested posting")
    return CheckResult("UNKNOWN", "Ashby page has no conclusive posting data")


def redirect_is_search_or_landing(original: str, final: str, provider: str) -> bool:
    if provider not in KNOWN_ATS or original == final:
        return False

    source = urlparse(original)
    target = urlparse(final)
    if source.hostname != target.hostname:
        return False

    path = target.path.rstrip("/").casefold()
    return (
        any(part in path for part in SEARCH_PATH_PARTS)
        or path in {"", "/", "/en-us", "/en-ca", "/fr-ca", "/jobs", "/careers"}
    )


def check_url(
    session: requests.Session,
    url: str,
    workday_session: requests.Session | None = None,
    public_api_session: requests.Session | None = None,
) -> CheckResult:
    """Return CLOSED only when there is a high-confidence closure signal."""
    provider = ats_name(url)
    if provider == "oracle":
        return check_oracle_api(public_api_session or make_public_api_session(), url)
    workday_result = None
    if provider == "workday":
        workday_result = check_workday_api(workday_session or make_workday_session(), url)
        if workday_result is not None and workday_result.status != "UNKNOWN":
            return workday_result

    public_api_result = check_public_ats_api(
        public_api_session or make_public_api_session(), url, provider
    )
    if public_api_result is not None and public_api_result.status != "UNKNOWN":
        return public_api_result

    try:
        # GET is required even when HEAD returns 200: most ATS closure messages
        # exist only in the page body, and several providers reject HEAD.
        response = session.get(url, allow_redirects=True, timeout=(10, 30))
    except requests.RequestException as error:
        if workday_result is not None:
            return workday_result
        return CheckResult("UNKNOWN", f"Request failed: {type(error).__name__}")

    # iCIMS occasionally serves a CloudFront human-verification page (405) to
    # browser-like headers, even though its public job endpoint remains
    # available. Retrying only that provider with requests' default headers
    # distinguishes a real 410 closed posting from that verification page.
    if provider == "icims" and response.status_code == 405:
        try:
            response = requests.get(url, allow_redirects=True, timeout=(10, 30))
        except requests.RequestException as error:
            return CheckResult("UNKNOWN", f"iCIMS fallback failed: {type(error).__name__}")

    if response.status_code in {404, 410}:
        return CheckResult("CLOSED", f"HTTP {response.status_code}")
    if response.status_code in {401, 403, 408, 429} or response.status_code >= 500:
        return CheckResult("UNKNOWN", f"HTTP {response.status_code} (not treated as closed)")
    if response.status_code >= 400:
        return CheckResult("UNKNOWN", f"HTTP {response.status_code} (inconclusive)")

    if provider == "ashby":
        # Board/login pages can also have null posting data; validate the final route.
        return check_ashby_page(response.url, response.text)

    if redirect_is_search_or_landing(url, response.url, provider):
        return CheckResult("CLOSED", f"Redirected to ATS search/landing page: {response.url}")

    phrase = closed_phrase(readable_text(response.text), provider)
    if phrase:
        return CheckResult("CLOSED", f"Closed-page message: {phrase!r} ({provider})")
    if workday_result is not None:
        # A Workday 200 HTML response is only its SPA shell, not proof that the
        # requisition exists. Preserve the API's inconclusive result instead.
        return workday_result
    if public_api_result is not None:
        return public_api_result
    return CheckResult("OPEN", "No closure signal")


def only_expected_replacements(before: str, after: str, replacements: int) -> bool:
    """Ensure this script cannot make a formatting-only listing-file diff."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    if len(before_lines) != len(after_lines):
        return False

    made = 0
    for old_line, new_line in zip(before_lines, after_lines):
        if old_line == new_line:
            continue
        expected_line, line_replacements = APPLY_LINK.subn("Closed🔒", old_line)
        if not line_replacements or new_line != expected_line:
            return False
        made += line_replacements
    return made == replacements


def table_bounds(document: str, listing_file: ListingFile) -> tuple[int, int]:
    """Return the table-content bounds, failing safely on bad/missing markers."""
    begin_count = document.count(listing_file.begin_marker)
    end_count = document.count(listing_file.end_marker)
    if begin_count != 1 or end_count != 1:
        raise RuntimeError(
            f"{listing_file.path}: expected one begin and one end marker, "
            f"found {begin_count} begin and {end_count} end"
        )

    start = document.find(listing_file.begin_marker)
    content_start = start + len(listing_file.begin_marker)
    finish = document.find(listing_file.end_marker, content_start)
    return content_start, finish


def main() -> None:
    session = make_session()
    workday_session = make_workday_session()
    public_api_session = make_public_api_session()
    report_lines = ["# Link Check Report", ""]
    total_changed = 0
    checked_urls: dict[str, CheckResult] = {}

    for listing_file in LISTING_FILES:
        original = listing_file.path.read_text(encoding="utf-8")
        content_start, finish = table_bounds(original, listing_file)
        table = original[content_start:finish]
        changed = 0
        report_lines.extend((f"## {listing_file.path}", ""))

        def replace_if_closed(match: re.Match[str]) -> str:
            nonlocal changed
            url = match.group(1)
            result = checked_urls.get(url)
            if result is None:
                time.sleep(0.5)  # polite rate limiting across job boards
                result = check_url(session, url, workday_session, public_api_session)
                checked_urls[url] = result
            report_lines.append(f"- {url} → {result.status} ({result.reason})")
            if result.status == "CLOSED":
                changed += 1
                return "Closed🔒"
            return match.group(0)

        updated_table = APPLY_LINK.sub(replace_if_closed, table)
        updated = original[:content_start] + updated_table + original[finish:]
        if changed and not only_expected_replacements(original, updated, changed):
            raise RuntimeError(
                f"Refusing to write {listing_file.path}: unexpected non-link change detected"
            )
        if changed:
            listing_file.path.write_text(updated, encoding="utf-8")
            total_changed += changed

    REPORT.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    if total_changed:
        print(f"Marked {total_changed} closed posting(s). See link-check-report.md.")
    else:
        print("No high-confidence closed postings found. See link-check-report.md.")


if __name__ == "__main__":
    main()
