"""Conservative internship-length evidence for the existing Markdown rows.

Duration is treated like the work-term annotation: it is only recorded when a
single, explicit value is supported by the stored title, the posting URL, or a
dedicated statement in a verified posting description. Ambiguous, conflicting,
or merely implied values are intentionally left unknown.

This module has no third-party dependencies so the formatter can import it
without pulling in ``requests``.
"""
import re

# The negative lookahead (not a word boundary) tolerates URL slugs such as
# "8-months_R1" while still rejecting words like "monthly".
MONTH_WORD = r"(?:months|month|mos|mo)(?![a-z])"

# A single explicit count, e.g. "8 months", "8-months", "4-Month", "16months".
SINGLE = re.compile(rf"(\d{{1,2}})\s*[-\s]?\s*{MONTH_WORD}", re.I)

# A clustered range/list such as "4--8--12-months", "4/8/12 months",
# "4 to 8 months", or "4-8 month term" is never collapsed to one value.
MULTI = re.compile(
    rf"\b\d{{1,2}}(?:\s*(?:--|-|/|,|–|—|to|or)\s*\d{{1,2}})+\s*-?\s*{MONTH_WORD}",
    re.I,
)

# Sentences about deadlines or eligibility routinely mention months; they are
# not evidence of the employment length.
UNSAFE = re.compile(
    r"\b(?:deadline|applications?|graduat\w*|posted|posting date|copyright|"
    r"eligib\w*|expire\w*|closes?)\b",
    re.I,
)

# Only dedicated length statements in a description are accepted.
DESC_LABEL = re.compile(
    rf"^(?:duration|length|contract length|term length|work term(?: length)?|"
    rf"internship (?:length|duration)|co-?op (?:length|duration))\s*[:\-]?\s*"
    rf"(\d{{1,2}})\s*[-\s]?\s*{MONTH_WORD}",
    re.I,
)
DESC_INLINE = re.compile(
    rf"\b(?:this|the)\s+(?:internship|co-?op|position|role|placement)\s+"
    rf"(?:is|is a|is an|runs for|lasts)\s+(\d{{1,2}})\s*[-\s]?\s*{MONTH_WORD}",
    re.I,
)
DESC_AFTER = re.compile(
    rf"\b(\d{{1,2}})\s*[-\s]?\s*{MONTH_WORD}\s+"
    rf"(?:internship|co-?op|term|contract|placement|work term)\b",
    re.I,
)

MIN_MONTHS, MAX_MONTHS = 1, 24


def _valid(values):
    return {value for value in values if MIN_MONTHS <= value <= MAX_MONTHS}


def explicit_months(text):
    """Return the set of explicit month counts in ``text`` (ignoring ranges)."""
    if not text:
        return set()
    return _valid(int(match) for match in SINGLE.findall(text))


def _counts(text):
    """Single explicit value as a set, ``None`` when ambiguous/unsupported."""
    if not text:
        return set()
    if MULTI.search(text):
        return None
    values = explicit_months(text)
    return None if len(values) > 1 else values


def _description_counts(text):
    if not text:
        return set()
    values = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or UNSAFE.search(line):
            continue
        matches = [pattern.search(line) for pattern in (DESC_LABEL, DESC_INLINE, DESC_AFTER)]
        for match in matches:
            if match:
                values.add(int(match.group(1)))
    values = _valid(values)
    return None if len(values) > 1 else values


def extract(title="", url="", description=""):
    """Return ``{duration_months, duration_source}`` or ``None`` when unknown.

    ``title`` outranks the URL slug, which outranks the description. Any
    disagreement between explicit sources is treated as ambiguous.
    """
    title_counts = _counts(title)
    url_counts = _counts(url)
    if title_counts is None or url_counts is None:
        return None
    explicit = title_counts or url_counts
    if title_counts and url_counts and title_counts != url_counts:
        return None

    description_counts = _description_counts(description)
    if description_counts is None:
        return None
    if explicit and description_counts and explicit != description_counts:
        return None

    final = explicit or description_counts
    if len(final) != 1:
        return None
    months = next(iter(final))
    source = "title" if title_counts else "url" if url_counts else "description"
    return {"duration_months": months, "duration_source": source}