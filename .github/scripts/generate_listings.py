"""Generate a structured ``data/listings.json`` from the Markdown tables.

The Markdown listings remain the single source of truth. This script only
projects them (plus any stored annotations and conservative title/URL duration
evidence) into a dependency-free JSON document for the static filter site and
for other tooling.

It performs no network access and needs no third-party packages.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

from check_closed_jobs import LISTING_FILES
from duration import extract
from work_terms import STATE, TERM, load_state, rows

OUTPUT = Path("data/listings.json")
VERSION = 1
MONTHS = {name[:3].lower(): index for index, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}


def table_bounds(document, listing):
    start = document.find(listing.begin_marker)
    end = document.find(listing.end_marker)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Missing markers for {listing.path}")
    return start + len(listing.begin_marker), end


def cycle_for(path):
    # README-2026.md is the archive; the default README.md holds the current cycle.
    match = re.search(r"(20\d{2})", path.name)
    return match[1] if match else "2027"


def parse_posted(text):
    text = text.strip()
    match = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", text)
    if not match:
        return ""
    month = MONTHS.get(match[1].lower(), 0)
    if not month:
        return ""
    return f"{match[3]}-{month:02d}-{int(match[2]):02d}"


def parse_location(text):
    segments = [segment.strip() for segment in re.split(r"[/|]", text) if segment.strip()]
    remote = any("remote" in segment.lower() for segment in segments)
    provinces = re.findall(r",\s*([A-Za-z]{2})\b", text)
    cities = []
    for segment in segments:
        city = segment.split(",")[0].strip()
        if city and city.lower() not in {"remote", "remote canada"}:
            cities.append(city)
    return remote, cities, provinces


def work_term_of(row, entry):
    if entry.get("work_term") and entry.get("start_year"):
        return f"{entry['work_term'].title()} {entry['start_year']}"
    match = TERM.search(row.title)
    return f"{match[1].title()} {match[2]}" if match else ""


def build(root=Path(".")):
    state = load_state(root / STATE)
    listings = []
    for listing in LISTING_FILES:
        document = (root / listing.path).read_text(encoding="utf-8")
        start, end = table_bounds(document, listing)
        for row in rows(document[start:end], state):
            entry = state["jobs"].get(row.key, {})
            duration = extract(row.title, row.url or "")
            remote, cities, provinces = parse_location(row.location)
            listings.append({
                "company": row.company,
                "role": row.title,
                "location": row.location,
                "cities": cities,
                "provinces": provinces,
                "remote": remote,
                "url": row.url,
                "status": "open" if row.url else "closed",
                "posted": parse_posted(row.posted),
                "posted_raw": row.posted.strip(),
                "work_term": work_term_of(row, entry),
                "duration_months": duration["duration_months"] if duration else None,
                "duration_source": duration["duration_source"] if duration else None,
                "cycle": cycle_for(listing.path),
            })
    # Newest postings first, matching the "sorted by posting date" convention.
    listings.sort(key=lambda item: (item["posted"], item["company"]), reverse=True)
    return {"version": VERSION, "count": len(listings), "listings": listings}


def main():
    data = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} ({data['count']} listings)")


if __name__ == "__main__":
    main()