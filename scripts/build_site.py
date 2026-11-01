"""Builds the one-page download site: site/index.html plus the extension zip, into build/site.

    python3 scripts/build_site.py            # builds the extension first
    python3 scripts/build_site.py --no-build # reuse an existing extension/dist

The page is a template: {{version}}, {{pipeline}}, {{commit}}, {{built}} and {{zip_kb}} are filled
in here, so the published page always describes the zip next to it. Then the site is checked.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "extension" / "dist"
OUT = ROOT / "build" / "site"
ZIP_NAME = "fourensics-extension.zip"
PAGE_BUDGET_KB = 40
REQUIRED_IN_ZIP = {"manifest.json", "content.js", "background.js", "options.html", "options.js"}


def fail(msg: str) -> None:
    print(f"✗ {msg}")
    sys.exit(1)


def commit() -> str:
    sha = os.environ.get("GITHUB_SHA") or subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not sha:
        return "unknown"
    dirty = not os.environ.get("GITHUB_SHA") and subprocess.run(
        ["git", "status", "--porcelain", "--", "extension", "backend/app", "site"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return sha[:7] + ("+local changes" if dirty else "")


def build() -> Path:
    if "--no-build" not in sys.argv:
        subprocess.run(["npm", "run", "--silent", "build"], cwd=ROOT / "extension", check=True,
                       stdout=subprocess.DEVNULL)
    if not DIST.is_dir():
        fail("extension/dist is missing; build the extension first")
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    zpath = OUT / ZIP_NAME
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(DIST.rglob("*")):
            if f.is_file() and f.name != ".DS_Store":
                z.write(f, f.relative_to(DIST).as_posix())

    manifest = json.loads((DIST / "manifest.json").read_text())
    pipeline = re.search(r'PIPELINE_VERSION = "([^"]+)"', (ROOT / "backend/app/config.py").read_text())
    values = {
        "version": manifest["version"],
        "pipeline": pipeline[1] if pipeline else "unknown",
        "commit": commit(),
        "built": datetime.now(timezone.utc).strftime("%-d %b %Y"),
        "zip_kb": str(round(zpath.stat().st_size / 1024)),
    }
    page = (ROOT / "site" / "index.html").read_text()
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", value)
    (OUT / "index.html").write_text(page)
    return OUT


def check(out: Path) -> None:
    """The site stays one self-contained page that matches its zip."""
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    if files != ["fourensics-extension.zip", "index.html"]:
        fail(f"the site must be exactly one page and the zip, found {files}")
    page = (out / "index.html").read_text()
    if "{{" in page or "}}" in page:
        fail("unfilled template value left in the page")
    if re.search(r"<script\b[^>]*\bsrc=|<link\b[^>]*rel=[\"']?stylesheet", page, re.I):
        fail("the page must not load external scripts or stylesheets")
    for href in re.findall(r'href="([^"]+)"', page):
        if not (href.startswith("https://github.com/") or href.startswith("#") or (out / href).is_file()):
            fail(f"link does not resolve: {href}")
    if len(page.encode()) > PAGE_BUDGET_KB * 1024:
        fail(f"page is over {PAGE_BUDGET_KB} KB")
    with zipfile.ZipFile(out / ZIP_NAME) as z:
        names = set(z.namelist())
        missing = REQUIRED_IN_ZIP - names
        if missing:
            fail(f"zip is missing {sorted(missing)}")
        manifest = json.loads(z.read("manifest.json"))
    if f"Extension {manifest['version']} " not in page:
        fail("the page's version does not match the zip's manifest")
    print(f"✓ site: one page ({len(page.encode()) // 1024} KB) and {ZIP_NAME} (extension {manifest['version']}) in {out.relative_to(ROOT)}")


if __name__ == "__main__":
    check(build())
