"""Conservative work-term evidence and annotations for the existing Markdown rows."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from format_table import APPLY_LINK, split_markdown_row

STATE = Path("data/work-terms.json")
VERSION = 1
SEASON = r"(?:winter|summer|fall)"
MONTHS = "January February March April May June July August September October November December".split()
MONTH = "(?:" + "|".join(MONTHS) + ")"
TERM = re.compile(rf"\b({SEASON})\s+(20\d{{2}})\b", re.I)
UNSAFE = re.compile(r"\b(?:applications?|deadline|graduat\w*|posted|posting date|copyright|eligib\w*|previous|prior|example|not|may start|could|tentative|preferred|flexible|either|or)\b", re.I)
START = re.compile(
    rf"^(?:start date|expected start(?: date)?|anticipated start(?: date)?|"
    rf"(?:internship|co-?op|position|employment) (?:begins|starts)|"
    rf"(?:work |internship |co-?op )?term)\s*:?\s*"
    rf"({MONTH})(?:\s+(\d{{1,2}})(?:st|nd|rd|th)?,?)?"
    rf"(?:\s*[-–]\s*({MONTH}))?\s+(20\d{{2}})\.?$", re.I
)
TERM_LABEL = re.compile(r"^(?:(?:work |internship |co-?op )?term|start(?: date)?)\s*:\s*", re.I)
START_LABEL = re.compile(r"^(?:start date|expected start(?: date)?|anticipated start(?: date)?|(?:internship|co-?op|position|employment) (?:begins|starts))\b", re.I)


def classify(title, description=""):
    """Return (classification, reason); unknown is intentional, never a guessed year."""
    candidates = []
    for text, is_title in [(title, True)] + [(s.strip(), False) for s in description.splitlines() if s.strip()]:
        # Only the original title or a dedicated employment-period statement is evidence.
        contextual = is_title or TERM_LABEL.match(text) or START_LABEL.match(text) or re.fullmatch(
            rf"{SEASON}\s+20\d{{2}}\s+(?:internship|co-?op)\.?", text, re.I)
        if not contextual:
            continue
        if UNSAFE.search(text):
            if START_LABEL.match(text) or TERM_LABEL.match(text):
                return None, "ambiguous employment period"
            continue
        seasons = re.findall(rf"\b{SEASON}\b", text, re.I)
        years = set(re.findall(r"\b20\d{2}\b", text))
        if len(seasons) > 1 or len(years) > 1:
            return None, "ambiguous or conflicting employment period"
        for match in TERM.finditer(text):
            candidates.append(dict(work_term=match[1].lower(), start_year=int(match[2]),
                                   reason="explicit term", evidence=text))
        match = START.fullmatch(text)
        if (START_LABEL.match(text) or TERM_LABEL.match(text)) and not match and not TERM.fullmatch(TERM_LABEL.sub("", text)):
            return None, "unrecognized or ambiguous start statement"
        if match:
            month, day, end_month, year = match.groups()
            month_number = [m.lower() for m in MONTHS].index(month.lower()) + 1
            if day:
                from datetime import date
                try:
                    date(int(year), month_number, int(day))
                except ValueError:
                    return None, "invalid start date"
            if end_month and [m.lower() for m in MONTHS].index(end_month.lower()) + 1 < month_number:
                return None, "ambiguous cross-year period"
            candidates.append(dict(work_term=("winter", "summer", "fall")[(month_number - 1) // 4],
                                   start_year=int(year), reason="explicit start date", evidence=text))
    if len({(c["work_term"], c["start_year"]) for c in candidates}) > 1:
        return None, "conflicting evidence"
    return (candidates[0], "classified") if candidates else (None, "insufficient evidence")


def load_state(path=STATE):
    if not path.exists():
        return {"version": VERSION, "jobs": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != VERSION or not isinstance(data.get("jobs"), dict):
        raise ValueError("Unsupported work-term checkpoint format")
    return data


def save_state(data, path=STATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def label(entry):
    term, year = entry.get("work_term"), entry.get("start_year")
    if term not in {"winter", "summer", "fall"} or type(year) is not int or not 2000 <= year <= 2099:
        raise ValueError("Invalid normalized work term")
    return f"{term.title()} {year}"


def display_title(entry):
    title = entry["original_title"]
    suffix = label(entry)
    # Existing explicit title terms need no redundant suffix.
    if any(f"{m[1].title()} {m[2]}" == suffix for m in TERM.finditer(title)):
        return title
    return f"{title} ({suffix})"


def identity(company, title, location, posted):
    return hashlib.sha256(json.dumps([company, title, location, posted], ensure_ascii=False).encode()).hexdigest()


@dataclass
class Row:
    key: str
    title: str
    url: str | None
    line: int
    role_cell: str


def rows(table, state):
    company = ""
    for number, line in enumerate(table.splitlines(keepends=True)):
        cells = split_markdown_row(line)
        if not cells or len(cells) != 5:
            continue
        values = [c.strip() for c in cells]
        if values[0] == "Company" or re.fullmatch(r"[-:]+", values[0]):
            continue
        if values[0] != "↳":
            company = values[0]
        title = values[1]
        key = identity(company, title, values[2], values[4])
        # A suffix is stripped ONLY when a matching stored annotation owns it.
        match = re.fullmatch(r"(.*) \((?:Winter|Summer|Fall) 20\d{2}\)", title)
        if key not in state["jobs"] and match:
            candidate = identity(company, match[1], values[2], values[4])
            entry = state["jobs"].get(candidate, {})
            if entry.get("work_term") and display_title(entry) == title:
                key, title = candidate, entry["original_title"]
        link = APPLY_LINK.search(values[3])
        yield Row(key, title, link[1] if link else None, number, cells[1])


def render_table(table, state):
    lines = table.splitlines(keepends=True)
    for row in rows(table, state):
        entry = state["jobs"].get(row.key, {})
        if entry.get("work_term") and (not row.url or row.url == entry.get("source_url")):
            old = row.role_cell
            new = old.replace(old.strip(), display_title(entry), 1)
            # Locate the role cell using the same escaped-pipe convention as the formatter.
            separators = list(re.finditer(r"(?<!\\)\|", lines[row.line]))
            start, end = separators[1].end(), separators[2].start()
            lines[row.line] = lines[row.line][:start] + new + lines[row.line][end:]
    return "".join(lines)
