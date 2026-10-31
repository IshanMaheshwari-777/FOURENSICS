"""Rule-based checklist of what a defect report is missing, plus a polite reply draft."""
from __future__ import annotations

import re

from ..schemas import Anchor

_STEPS = re.compile(r"(steps to reproduce|to reproduce|reproduc|minimal example|^\s*\d+[.)]\s)", re.I | re.M)
_EXPECTED = re.compile(r"\b(expect(ed|ing|s)?|should|supposed to|desired|trying to|want(ed)? to|intended)\b", re.I)
_ACTUAL = re.compile(r"\b(actual|instead|observed|seeing|happens|happened|but (it|i get|got)|got|returns?|"
                     r"raises?|throws?|prints?|fails?|crash(es|ed)?|hangs?|warns?|reports?|exits?|errors?|leaks?|"
                     r"panics?|segfaults?|freezes|blocks?|stuck|breaks?|times? out|doesn'?t|does not|never|"
                     r"can'?t|cannot|won'?t)\b", re.I)
# Any language runtime, platform tool or browser with a version, or an operating system.
_ENV = re.compile(
    r"\b(python|py|node(?:\.?js)?|deno|bun|npm|yarn|pnpm|go|golang|rustc|rust|cargo|java|jdk|openjdk|kotlin|ruby|"
    r"php|dotnet|\.net|swift|xcode|gcc|clang|chrome|chromium|firefox|safari|edge|electron|vite|webpack|android|"
    r"ios)[\s@:/-]*v?\d+(\.\d+)*"
    r"|\b(windows|macos|mac os|os x|linux|ubuntu|debian|fedora|alpine|centos|wsl|platform)\b", re.I)
# Console output in a code block counts as the error output.
_OUTPUT = re.compile(r"\b(error|warn(ing)?|exception|panic|fail(ed|ure)?|traceback|fatal)\b", re.I)

LABELS = {
    "version": "the version or commit being used",
    "steps": "steps or a minimal code snippet to reproduce the problem",
    "expected_actual": "what was expected and what happened instead",
    "error": "the full error message or output",
    "environment": "the environment (runtime or language version, and operating system)",
}


def check_missing(text: str, anchors: list[Anchor]) -> list[str]:
    kinds = {a.kind for a in anchors}
    missing: list[str] = []
    if "version" not in kinds and "commit" not in kinds:
        missing.append("version")
    if "code_block" not in kinds and not _STEPS.search(text):
        missing.append("steps")
    # A clear description of what happens is enough: for crashes, races and errors "expected" is implied.
    if not (_EXPECTED.search(text) or _ACTUAL.search(text)):
        missing.append("expected_actual")
    blocks = [a for a in anchors if a.kind == "code_block"]
    # Output pasted in a code block, or a block introduced by prose like "prints two warnings:".
    has_output = any(_OUTPUT.search(b.value) for b in blocks) or (bool(blocks) and bool(_OUTPUT.search(text)))
    if "error" not in kinds and "traceback_frame" not in kinds and not has_output:
        missing.append("error")
    if not _ENV.search(text):
        missing.append("environment")
    return missing


def reply_draft(missing: list[str]) -> str | None:
    if not missing:
        return None
    items = "\n".join(f"- {LABELS[m]}" for m in missing)
    return (
        "Thanks for the report! To help us look into this, could you share:\n\n"
        f"{items}\n\n"
        "With these details we can check the behaviour against the exact version you are using."
    )


def human_labels(missing: list[str]) -> list[str]:
    return [LABELS[m] for m in missing]
