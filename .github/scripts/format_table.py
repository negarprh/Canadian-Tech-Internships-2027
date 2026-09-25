from pathlib import Path
import re


LISTING_FILES = (
    (Path("README.md"), "INTERNSHIPS_TABLE"),
    (Path("README-2026.md"), "INTERNSHIPS_2026_TABLE"),
)

# Shared Apply-control parser; the formatter itself needs no HTTP dependency.
APPLY_LINK = re.compile(
    r"\[!\[Apply\]\([^)]+?\)\]\((https?://[^)\s]+)\)", re.IGNORECASE
)

def split_markdown_row(line: str) -> list[str] | None:
    """Split a Markdown table row without treating escaped pipes as separators."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None

    cells: list[str] = []
    cell: list[str] = []
    escaped = False
    for character in stripped[1:]:
        if character == "|" and not escaped:
            cells.append("".join(cell))
            cell = []
        else:
            cell.append(character)
        escaped = character == "\\" and not escaped

    if cell:
        cells.append("".join(cell))
    while cells and not cells[-1].strip():
        cells.pop()
    return cells


def normalize_table(block: str) -> str:
    lines = block.splitlines()
    out = []

    for line in lines:
        cells = split_markdown_row(line)
        if cells is None:
            out.append(line.rstrip())
            continue

        out.append("| " + " | ".join(cell.strip() for cell in cells) + " |")

    return "\n".join(out)

def format_listings(root=Path("."), terms_only=False) -> None:
    from collections import Counter
    from work_terms import load_state, render_table, rows, STATE
    state = load_state(root / STATE)
    counts = Counter()
    for path, table_name in LISTING_FILES:
        text = (root / path).read_text(encoding="utf-8")
        match = re.search(rf"<!-- BEGIN:{table_name} -->([\s\S]*?)<!-- END:{table_name} -->", text)
        if match:
            counts.update(row.key for row in rows(match[1], state))
    state = {**state, "jobs": {key: entry for key, entry in state["jobs"].items() if counts[key] == 1}}
    for path, table_name in LISTING_FILES:
        path = root / path
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"(<!-- BEGIN:{re.escape(table_name)} -->)([\s\S]*?)(<!-- END:{re.escape(table_name)} -->)"
        )

        def replacer(match: re.Match[str]) -> str:
            start, table, end = match.groups()
            table = render_table(table, state)
            if terms_only:
                return f"{start}{table}{end}"
            return f"{start}\n{normalize_table(table.strip())}\n{end}"

        formatted, replacements = pattern.subn(replacer, text)
        if replacements != 1:
            raise RuntimeError(f"Expected one {table_name} table in {path}, found {replacements}")
        if not terms_only:
            formatted = formatted.rstrip() + "\n"
        if formatted != text:
            path.write_text(formatted, encoding="utf-8")


def main() -> None:
    format_listings()

    print("Internship tables normalized.")


if __name__ == "__main__":
    main()
