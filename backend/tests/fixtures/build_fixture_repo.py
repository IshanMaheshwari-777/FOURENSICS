"""Builds a tiny, deterministic `csvtool` git repository for offline tests.

History (all dates fixed):
  2025-01-10  v2.2.0  parse_file works with empty header cells
  2025-02-01  v2.3.1  refactor introduces KeyError: 'col' on empty header cells
  2025-03-01          README tweak (last commit before the fixture issue of 2025-03-10)
  2025-03-20  v2.4.0  fix + regression test (also exposed as refs/pull/7/head)

Usage: python build_fixture_repo.py <target_dir>
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PARSER_V220 = '''"""CSV parsing helpers."""
from csvtool.checks import validate


def read_header(line):
    return [c.strip() for c in line.split(",")]


def parse_file(text):
    """Parse CSV text into a list of dicts."""
    lines = text.strip().splitlines()
    header = read_header(lines[0])
    rows = []
    for line in lines[1:]:
        cells = line.split(",")
        rows.append({(h or "col"): c for h, c in zip(header, cells)})
    for row in rows:
        validate(row)
    return rows
'''

PARSER_V231 = '''"""CSV parsing helpers."""
from csvtool.checks import validate


def read_header(line):
    return [c.strip() for c in line.split(",")]


def parse_file(text):
    """Parse CSV text into a list of dicts. Empty header cells are named `col`."""
    lines = text.strip().splitlines()
    header = read_header(lines[0])
    names = [h or "col" for h in header]
    index = {h: i for i, h in enumerate(header)}
    rows = []
    for line in lines[1:]:
        cells = line.split(",")
        row = {}
        for name in names:
            row[name] = cells[index[name]]
        rows.append(row)
    for row in rows:
        validate(row)
    return rows
'''

PARSER_FIXED = PARSER_V231.replace(
    "    index = {h: i for i, h in enumerate(header)}\n",
    "    index = {n: i for i, n in enumerate(names)}\n",
)

CHECKS = '''def validate(value):
    """Return True when value is a parsed row."""
    if value is None:
        return False
    return isinstance(value, dict)
'''

TESTS = '''from csvtool.parser import parse_file


def test_parse_basic():
    rows = parse_file("a,b\\n1,2")
    assert rows == [{"a": "1", "b": "2"}]
'''

TESTS_FIXED = TESTS + '''

def test_empty_header_cell():
    rows = parse_file("a,,c\\n1,2,3")
    assert rows == [{"a": "1", "col": "2", "c": "3"}]
'''

DOCS = """# Usage

Call `parse_file(text)` to parse CSV text into a list of dicts.

Empty header cells are allowed; they are named `col`.
"""

README = "# csvtool\n\nA tiny CSV helper used as a test fixture.\n"


def _run(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def build(target: Path) -> dict[str, str]:
    """Creates the repo and returns named SHAs."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    _run(target, "init", "-q", "-b", "main")
    shas: dict[str, str] = {}

    _write(target, {"csvtool/__init__.py": "", "csvtool/parser.py": PARSER_V220, "csvtool/checks.py": CHECKS,
                    "tests/test_parser.py": TESTS, "docs/usage.md": DOCS, "README.md": README,
                    "data/sample.csv": "a,b\n1,2\n",  # non-source file: skipped by sparse checkouts
                    ".github/workflows/ci.yml": "on: push\njobs: {}\n"})  # hidden folder: must still be seen
    _run(target, "add", "-A")
    _run(target, "commit", "-q", "-m", "csvtool 2.2.0", date="2025-01-10T10:00:00+00:00")
    _run(target, "tag", "v2.2.0")
    shas["v2.2.0"] = _run(target, "rev-parse", "HEAD")
    _run(target, "branch", "1.x")  # a maintenance branch that stays at 2.2.0

    _write(target, {"csvtool/parser.py": PARSER_V231})
    _run(target, "commit", "-q", "-am", "Release 2.3.1: name empty header cells", date="2025-02-01T10:00:00+00:00")
    _run(target, "tag", "v2.3.1")
    shas["v2.3.1"] = _run(target, "rev-parse", "HEAD")

    _write(target, {"README.md": README + "\nSee docs/usage.md.\n"})
    _run(target, "commit", "-q", "-am", "Docs: link usage guide", date="2025-03-01T10:00:00+00:00")
    shas["pre_issue"] = _run(target, "rev-parse", "HEAD")

    _write(target, {"csvtool/parser.py": PARSER_FIXED, "tests/test_parser.py": TESTS_FIXED})
    _run(target, "commit", "-q", "-am", "Fix KeyError for empty header cells", date="2025-03-20T10:00:00+00:00")
    shas["fix"] = _run(target, "rev-parse", "HEAD")
    _run(target, "update-ref", "refs/pull/7/head", shas["fix"])

    _run(target, "tag", "v2.4.0")
    shas["v2.4.0"] = shas["fix"]
    return shas


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
