from __future__ import annotations

import re

from ..schemas import Target

_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/(?P<kind>issues|pull)/(?P<number>\d+)(?:[/?#].*)?$"
)


class BadURL(ValueError):
    pass


def parse_target(url: str) -> Target:
    m = _URL.match(url.strip())
    if not m:
        raise BadURL("Expected a GitHub issue or pull request URL, like https://github.com/owner/repo/issues/123")
    kind = "pr" if m["kind"] == "pull" else "issue"
    owner, repo, number = m["owner"], m["repo"], int(m["number"])
    path = "pull" if kind == "pr" else "issues"
    return Target(owner=owner, repo=repo, kind=kind, number=number,
                  url=f"https://github.com/{owner}/{repo}/{path}/{number}")


def permalink(owner: str, repo: str, sha: str, path: str, start: int | None = None, end: int | None = None) -> str:
    url = f"https://github.com/{owner}/{repo}/blob/{sha}/{path}"
    if start:
        url += f"#L{start}" + (f"-L{end}" if end and end != start else "")
    return url
