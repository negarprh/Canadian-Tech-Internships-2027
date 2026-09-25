"""Manual historical backfill. Default: stdout-only dry run, no persistent writes."""
import sys
sys.dont_write_bytecode = True

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from check_closed_jobs import (LISTING_FILES, VisibleText, greenhouse_api_url,
                               make_public_api_session, table_bounds, workday_api_url)
from work_terms import STATE, classify, label, load_state, rows, save_state


def plain_description(html):
    # Preserve paragraph/list boundaries so an unrelated date cannot gain a start label.
    parser = VisibleText()
    parser.feed(re.sub(r"</?(?:p|div|li|br|h[1-6])\b[^>]*>", "\n", html, flags=re.I))
    return "".join(parser.parts)


def normalized_title(title):
    return re.sub(r"\W+", " ", title).strip().casefold()


class Fetcher:
    """One request at a time, one-second spacing; no redirects, retries or bypasses."""
    def __init__(self):
        self.session = make_public_api_session()
        # The closure checker's retries can honor unbounded Retry-After values.
        # A bounded historical batch instead records failure for explicit later retry.
        self.session.mount("https://", HTTPAdapter(max_retries=0))
        self.cache = {}
        self.last_request = 0.0

    def __call__(self, url, title):
        try:
            host = (urlparse(url).hostname or "").lower()
            endpoint = greenhouse_api_url(url)
            provider = "greenhouse"
            if endpoint is None and (host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com")):
                endpoint, provider = workday_api_url(url), "workday"
        except ValueError:
            return "fetch_failure", "Malformed posting URL"
        if not endpoint or urlparse(endpoint).scheme != "https":
            return "unsupported", "No supported authoritative posting endpoint"
        if endpoint not in self.cache:
            time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                # Stream with size and wall-time limits to avoid slow/oversized responses.
                with self.session.get(endpoint, timeout=(5, 15), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}")
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 2_000_000 or time.monotonic() - self.last_request > 25:
                            raise ValueError("Response exceeded size/time limit")
                        chunks.append(chunk)
                    payload = json.loads(b"".join(chunks))
                self.cache[endpoint] = (True, payload)
            except (requests.RequestException, ValueError) as error:
                self.cache[endpoint] = (False, str(error)[:200])
        ok, payload = self.cache[endpoint]
        if not ok:
            return "fetch_failure", payload
        job = payload.get("jobPostingInfo") if provider == "workday" and isinstance(payload, dict) else payload
        if not isinstance(job, dict):
            return "fetch_failure", "Missing posting data"
        remote_title = job.get("title")
        if not isinstance(remote_title, str) or normalized_title(remote_title) != normalized_title(title):
            return "fetch_failure", "Posting title differs from stored title; identity uncertain"
        if provider == "greenhouse":
            valid_id = str(job.get("id")) == endpoint.rsplit("/", 1)[-1]
            content = job.get("content")
        else:
            external = job.get("externalPath")
            valid_id = bool(job.get("id") and isinstance(external, str) and endpoint.endswith(external))
            content = job.get("jobDescription")
        if not valid_id or not isinstance(content, str):
            return "fetch_failure", "Posting identity/description could not be verified"
        return "fetched", plain_description(content)


def run(root=Path("."), *, apply=False, batch_size=100, fetch=False, retry_unclassified=False, after_key="", fetcher=None):
    if not 1 <= batch_size <= 200:
        raise ValueError("batch_size must be between 1 and 200")
    state_path = root / STATE
    state = load_state(state_path)
    all_rows = []
    for listing in LISTING_FILES:
        document = (root / listing.path).read_text(encoding="utf-8")
        start, end = table_bounds(document, listing)
        all_rows.extend((str(listing.path), row) for row in rows(document[start:end], state))
    # Stable identities survive insertions, sorting, formatting, and Apply -> Closed.
    unique = {}
    counts = Counter(row.key for _, row in all_rows)
    for file, row in all_rows:
        unique.setdefault(row.key, (file, row))
    summary = Counter(total_jobs_considered=len(unique), total_listing_rows=len(all_rows),
                      already_classified=0, newly_classifiable=0, insufficient_evidence=0,
                      fetch_failures=0, skipped_checkpoint=0, deferred=0, processed=0)
    if after_key and not re.fullmatch(r"[0-9a-f]{64}", after_key):
        raise ValueError("after_key must be an identity from the previous review")
    grouped, review = Counter(), []
    fetcher = fetcher or Fetcher()
    for key, (file, row) in sorted(unique.items()):
        if counts[key] > 1:
            summary["ambiguous_identity"] += 1
            continue
        if key <= after_key:
            summary["before_cursor"] += 1
            continue
        old = state["jobs"].get(key)
        if old and old.get("work_term"):
            label(old)  # fail loudly on invalid existing metadata
            summary["already_classified"] += 1
            continue
        if old and not retry_unclassified:
            summary["skipped_checkpoint"] += 1
            continue
        if summary["processed"] >= batch_size:
            summary["deferred"] += 1
            continue
        summary["processed"] += 1
        result, reason = classify(row.title)
        source, fetch_status = "stored title", "not requested"
        if result is None and reason == "insufficient evidence" and fetch and row.url:
            fetch_status, description = fetcher(row.url, row.title)
            if fetch_status == "fetched":
                result, reason = classify(row.title, description)
                source = "posting description"
            else:
                reason = description
        entry = dict(original_title=row.title, status="classified" if result else "unclassified",
                     reason=reason, source=source, source_url=row.url, fetch_status=fetch_status)
        if result:
            entry.update(result)
            summary["newly_classifiable"] += 1
            grouped[label(entry)] += 1
        else:
            summary["insufficient_evidence"] += 1
        if fetch_status == "fetch_failure":
            summary["fetch_failures"] += 1
        review.append(dict(key=key, file=file, url=row.url, **entry))
        state["jobs"][key] = entry
        if apply:
            save_state(state, state_path)  # atomic per-job checkpoint; interrupted runs resume
    if apply:
        # Use the normal formatter's rendering mechanism without unrelated whitespace changes.
        from format_table import format_listings
        format_listings(root=root, terms_only=True)
    return dict(summary=dict(summary), classifications_by_term=dict(sorted(grouped.items())),
                next_after_key=review[-1]["key"] if review else after_key, review=review)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--fetch", action="store_true", help="Fetch supported ATS postings when titles are insufficient")
    parser.add_argument("--retry-unclassified", action="store_true", help="Explicitly revisit checkpointed unknowns")
    parser.add_argument("--after-key", default="", help="Review batches after this identity without changing checkpoints")
    args = parser.parse_args()
    result = run(apply=args.apply, batch_size=args.batch_size, fetch=args.fetch,
                 retry_unclassified=args.retry_unclassified, after_key=args.after_key)
    # No report files, caches or checkpoints in dry-run mode. Caller may capture stdout.
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
