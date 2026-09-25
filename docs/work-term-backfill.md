# Historical work-term backfill

## Repository findings

The source of truth is the five-column Markdown table in `README.md` and
`README-2026.md`, not a JSON database. Rows store company (sometimes `↳`), role,
location, Apply URL or `Closed🔒`, and posting date. There are no stored
descriptions, ATS payloads, start dates, or stable job IDs. The closure checker
removes URLs when marking jobs closed. The checkout has 1,825 parsed rows
(416 current and 1,409 archived), with only 309 retained URLs and 46 ambiguous
duplicate identities. Most archive rows are closed. Neither the table's year nor its posting dates
establish a job's work term.

The existing Python formatter now also renders work terms. The issue workflow
still inserts ordinary Markdown rows; ingestion is unchanged. Tests use the
existing `unittest` convention and fetching uses the existing `requests`
dependency, ATS URL builders, session factory, and HTML text parser.

## Storage and display

`data/work-terms.json` is created only by an apply run. It is a versioned map of
annotations/checkpoints, not a duplicate job database. Classified entries contain
`original_title`, `work_term` (`winter`, `summer`, `fall`), integer `start_year`,
`reason`, `evidence`, `source`, `source_url`, and fetch status. Unclassified entries
contain the original title and processing outcome, with no term or year.
SearchStop can join using the identity below or retained source URL.

Identity is SHA-256 of the UTF-8 JSON array `[resolved_company, original_title,
location, posted_date]`, serialized with Python's default separators and
`ensure_ascii=False`. The URL is excluded so marking a posting closed does not
invalidate its annotation. Ambiguous duplicate identities are skipped. Changes
to identity fields intentionally create a new job identity. Orphan annotations
are harmless and are not rendered. Do not edit a classified display title in
isolation: update its source annotation or remove that annotation first.

The formatter derives `Role (Summer 2027)` from the preserved original title.
An existing explicit term in the original title is not repeated. Unknown jobs
retain their exact display and no new column appears. Apply invokes the normal
formatter with `terms_only=True`, which changes role cells without normalizing
unrelated table whitespace. Normal formatting also preserves these annotations.

## Evidence policy

Accepted evidence is an explicit supported season and four-digit year in the
stored role title, a dedicated `Term: Summer 2027` field in a verified posting,
or a dedicated employment start statement such as `Start Date: May 2027`,
`Expected start: January 2027`, `Internship begins September 8, 2027`, or
`Term: May-August 2027`. Matching ignores case. Full month names and English
wording are supported. January–April map to Winter, May–August to Summer,
and September–December to Fall.

Missing years, multiple terms/years, conflicting start evidence, invalid dates,
unsupported wording, uncertain identity, and inaccessible postings remain
unclassified. Posting/discovery dates, deadlines, graduation requirements,
footer dates, URL slugs, recruiting conventions, duration, and README year are
never evidence. Text is deliberately constrained to dedicated statements rather
than arbitrary date mentions. Precision takes priority over recall. A title
that already establishes a term is sufficient without fetching its page;
therefore unseen conflicts on that page cannot be detected.

## Fetching and limits

Fetching is optional. Only supported Greenhouse and Workday public detail APIs
are used, with the existing URL builders. The returned job ID/path must match
the requested posting, and its title must match the stored title after case and
punctuation normalization. Shortened/renamed titles often fail this conservative
check. Only that job's description is examined, never a whole career page.
Other ATS providers, URLs removed from closed rows, redirects, expired pages,
403/429 responses, malformed responses, and timeouts are skipped. There is no
browser automation or anti-bot bypass. A reused requisition with the same ID
and title cannot always be distinguished from the historical posting; inspect
the evidence before merging.

The default job limit is `all`, covering both existing listing tables in one run.
A positive integer limits processing for debugging. Requests remain sequential, spaced
at least one second apart. Connect/read timeouts are 5/15 seconds; streamed
responses have a 2 MB cap and a 25-second elapsed-time check between chunks.
One slow read may extend that elapsed limit. No automatic retries: failures
become checkpoints. All-job runs revisit unclassified checkpoints while skipping
confirmed terms. Limited runs retain checkpoint skipping unless explicitly retried.
An in-memory LRU cache holds at most 32 endpoint responses. No full page bodies
are saved. The workflow allows 240 minutes for the existing dataset (309 retained
URLs at inspection); substantially larger future datasets may require revisiting
this limit. A timeout fails the run rather than opening a partial PR.

## Run from GitHub

1. Merge this infrastructure first. In repository **Settings → Actions → General**,
   ensure GitHub Actions may create pull requests (organization policy may
   control this).
2. Open **Actions → Historical work-term backfill → Run workflow**.
3. Select the default branch (`main`). Keep **dry_run=true** (Preview),
   **max_jobs=all**, and **fetch=true**. There is no cursor input to fill in.
4. Open the completed run's summary. Download **work-term-review** from its
   artifacts and open `work-term-review.json`. It includes proposed classifications
   with evidence, already classified jobs, unclassified outcomes, ambiguous
   identities, and fetch failures across the whole dataset. Duplicate identities
   are reported with their row count and remain unclassified for safety.
   Fetch failures also count as insufficient evidence; grouped terms describe
   new proposals. `deferred` is zero in an all-job run.
5. Preview changes no repository files and creates no commit, branch, or PR.
   The workflow captures stdout outside the checkout and uploads the artifact.
6. To apply the entire backfill, dispatch once with **dry_run=false**,
   **max_jobs=all**, and **fetch=true**. Apply re-evaluates current evidence;
   it does not blindly apply the preview.
7. The run checkpoints jobs, regenerates listings once, and creates **one PR** on
   `backfill/work-terms-<run_id>` containing the complete result. Review the README
   diff and metadata evidence before merging. There is no push to main or
   automatic merge. Unchanged runs create no PR; unknown-only changes may create
   a checkpoint-only PR.
8. For debugging, set **max_jobs** to a positive integer such as `10` or `100`.
   No repeated triggers or manual pagination are needed for normal `all` usage.

Only the PR job receives write permissions, and it never runs for a preview.
Runs are serialized. Do not start overlapping unmerged backfill PRs: concurrency
prevents simultaneous execution, but cannot carry unmerged checkpoints forward.
Review artifacts expire after 30 days. Local apply checkpoints survive an
interruption; a canceled/failed hosted runner can lose unuploaded progress and
must repeat that unmerged run. A stale PR should be rerun or resolved against
the current listings before merging.

## Local commands and explicit retries

Run from the repository root with Python 3.11+ and `requests` installed:

```sh
python -B -m unittest discover -s .github/scripts -p 'test_*.py'
python -B .github/scripts/backfill_work_terms.py --dry-run --max-jobs all --fetch
# Only after review:
python -B .github/scripts/backfill_work_terms.py --apply --max-jobs all --fetch
```

Default mode is dry run. It creates no report, checkpoint, bytecode, or page cache.
Redirect stdout yourself if you want a persistent JSON review file.
All-job runs automatically revisit unknowns and skip classified jobs. For limited
local debugging runs, `--retry-unclassified` and `--after-key <next_after_key>`
remain available. A cursor is rejected with `all` to prevent accidentally skipping
part of the dataset. `--batch-size` remains a compatibility alias for `--max-jobs`.
No live historical backfill is required to test the infrastructure: the tests
use synthetic rows and mocked HTTP responses.
