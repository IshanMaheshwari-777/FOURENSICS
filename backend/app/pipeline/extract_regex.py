"""Deterministic anchor extraction: the concrete references in a report.

These are authoritative and independent of the model, so a missed or misspelled
name in the model's output cannot hide a reference.
"""
from __future__ import annotations

import builtins
import re
import sys

from ..schemas import Anchor

_STDLIB = set(sys.stdlib_module_names)

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_INLINE = re.compile(r"`([^`\n]{1,120})`")
_QUOTED = re.compile(r'''(?<![\w\\])(["'])([^\n]{1,240}?)\1(?!\w)''')
_LOG_LINE = re.compile(r"^.*?\b(?:WARNING|WARN|ERROR|FATAL|CRITICAL|panic)\b\s*[:\]-]?\s+(.{3,240})$", re.M)
_ANNOTATION = re.compile(r"/\*+\s*([@#][A-Za-z_][\w@#-]{1,80})\s*\*/")
MAX_LITERALS = 32
_TB_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([\w<>.]+))?')
_ERROR = re.compile(r"\b((?:[A-Z]\w*\.)*[A-Z]\w*(?:Error|Exception|Warning|Exit|Interrupt))(?::[ \t]*([^\n]{0,200}))?")
_VERSION_CTX = re.compile(
    r"(?:\bversion\s*:?\s*|\bv(?=\d)|==\s*|\brelease\s+|\b(?:on|using|with|in)\s+(?=v?\d+\.\d+))"
    r"(v?\d+\.\d+(?:\.\d+){0,2}(?:(?:a|b|rc|\.dev|\.post)\d+)?)",
    re.I,
)
_BARE_SEMVER = re.compile(r"(?<![\w.])(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)(?![\w.])")
_CODE_EXT = r"(?:py|pyi|js|mjs|cjs|jsx|ts|tsx|vue|svelte|go|rs|java|kt|scala|rb|php|c|cc|cpp|h|hpp|cs|swift|dart|ex|exs|lua|sh)"
_PATH = re.compile(r"(?<![\w/.-])((?:[\w.@-]+/)*[\w.-]+\.(?:" + _CODE_EXT[3:-1] +
                   r"|cfg|toml|ini|txt|md|rst|yml|yaml|json|lock|gradle|xml))\b")
# Stack frames in other languages: "at fn (src/a.js:3:9)", "src/main.rs:10:5", "/x/y.go:123", "(Bar.java:42)"
_GEN_FRAME = re.compile(r"(?P<path>(?:[\w.@-]+/)*[\w.-]+\.(?!py\b)" + _CODE_EXT + r"):(?P<line>\d+)(?::\d+)?")
_PANIC = re.compile(r"(?:\bpanic: |panicked at '?)([^\n']{3,200})")
_CALL = re.compile(r"(?<![\w.])([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*)\(\)")
_COMMIT = re.compile(r"\bcommit\s+([0-9a-f]{7,40})\b", re.I)
_HEX = re.compile(r"(?<![\w.-])([0-9a-f]{7,40})(?![\w.-])")
# URL paths such as /rest/v1/root_cres?format=oscal (with or without a host in front)
_ROUTE = re.compile(r"(?:https?://[^\s/`'\"]+)?(/(?:[\w.%~:-]+/)*[\w.%~:-]+(?:\?[^\s`'\")]*)?)")
_IDENT = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*(?:\(\))?$")

_NOT_VERSIONS = {"0.0.0"}
# Language built-ins and globals are never the project's own code.
_COMMON_WORDS = set(dir(builtins)) | {
    "self", "cls", "pip", "python", "npm", "node", "null", "undefined", "true", "false", "nil",
    "Promise", "Array", "Object", "String", "Number", "Boolean", "Buffer", "Error", "JSON", "Math", "Date",
    "Map", "Set", "Symbol", "console", "window", "document", "require", "module", "exports", "process",
    "fmt", "err", "ctx", "string", "int64", "float64", "bool", "byte", "rune", "Vec", "Option", "Result",
    "Some", "Ok", "Err", "Box", "println", "panic", "main",
}


def _add(out: list[Anchor], seen: set, anchor: Anchor) -> None:
    key = (anchor.kind, anchor.value, anchor.line)
    if key not in seen:
        seen.add(key)
        out.append(anchor)


def extract_anchors(text: str) -> list[Anchor]:
    out: list[Anchor] = []
    seen: set = set()

    for m in _FENCE.finditer(text):
        block = m.group(1).strip()
        if block:
            _add(out, seen, Anchor(kind="code_block", value=block[:4000]))

    # Search report-supplied text literally, including punctuation and strings inside code/log blocks.
    # Fences are masked for inline parsing so ```python is not mistaken for an inline expression.
    inline_text = _FENCE.sub(lambda m: " " * len(m.group(0)), text)
    literal_values = [m.group(1) for m in _INLINE.finditer(inline_text)]
    literal_values += [m.group(1) for m in _ANNOTATION.finditer(text)]
    literal_values += [m.group(1) for m in _LOG_LINE.finditer(text)]
    literal_values += [m.group(2) for m in _QUOTED.finditer(text)]
    for value in dict.fromkeys(v.strip() for v in literal_values if v.strip()):
        if len([a for a in out if a.kind == "literal"]) >= MAX_LITERALS:
            break
        _add(out, seen, Anchor(kind="literal", value=value))

    for m in _TB_FRAME.finditer(text):
        _add(out, seen, Anchor(kind="traceback_frame", value=m.group(1), line=int(m.group(2)), func=m.group(3)))
    for m in _GEN_FRAME.finditer(text):
        _add(out, seen, Anchor(kind="traceback_frame", value=m.group("path"), line=int(m.group("line"))))
    for m in _PANIC.finditer(text):
        _add(out, seen, Anchor(kind="error", value=f"panic: {m.group(1).strip()}"))

    errors: dict[str, list[str]] = {}
    for m in _ERROR.finditer(text):
        name = m.group(1)
        msg = (m.group(2) or "").strip().rstrip(".")
        errors.setdefault(name, [])
        if msg and msg not in errors[name]:
            errors[name].append(msg)
    for name, msgs in errors.items():
        # A bare "TypeError" adds nothing when the full message is also present.
        for value in ([f"{name}: {m}" for m in msgs] or [name]):
            _add(out, seen, Anchor(kind="error", value=value))

    # Versions named with a keyword ("version 2.3", "v2.3", "==2.3") and bare x.y.z numbers ("ripgrep 14.1.1").
    matches = list(_VERSION_CTX.finditer(text))
    keyword_spans = {m.span(1) for m in matches}
    matches += [m for m in _BARE_SEMVER.finditer(text) if m.span(1) not in keyword_spans]
    for m in sorted(matches, key=lambda m: m.start(1)):
        v = m.group(1).lstrip("vV")
        if v not in _NOT_VERSIONS:
            before = text[max(0, m.start() - 40):m.start(1)].replace("\n", " ")
            _add(out, seen, Anchor(kind="version", value=v, context=before))

    for m in _COMMIT.finditer(text):
        _add(out, seen, Anchor(kind="commit", value=m.group(1)))
    # Bare hex like "on main at `cbf22e0`". Only candidates: the snapshot resolver keeps one only if
    # it is a real commit in the repository from before the report.
    for m in _HEX.finditer(text):
        h = m.group(1)
        if any(c.isdigit() for c in h) and any(c.isalpha() for c in h):
            _add(out, seen, Anchor(kind="commit", value=h))

    for m in _ROUTE.finditer(text):
        path = m.group(1).split("?")[0].rstrip("/")
        if path.count("/") >= 2 and not path.endswith((".py", ".js", ".md", ".html")):
            _add(out, seen, Anchor(kind="route", value=path))

    for m in _PATH.finditer(text):
        p = m.group(1)
        if not p.startswith(("http", "www.")):
            _add(out, seen, Anchor(kind="path", value=p))

    symbols: list[str] = []
    for m in _INLINE.finditer(text):
        token = m.group(1).strip()
        if _IDENT.match(token) and not _PATH.fullmatch(token):
            symbols.append(token.removesuffix("()"))
    symbols += [m.group(1) for m in _CALL.finditer(text)]
    for s in symbols:
        if s in _COMMON_WORDS or s[0].isdigit():
            continue
        if "." in s and s.split(".", 1)[0] in _STDLIB:
            continue  # datetime.now, json.loads...: standard library, not the project's code
        if re.fullmatch(r"[0-9a-f]{7,40}", s) and any(c.isdigit() for c in s):
            continue  # a commit hash in backticks, not a code name
        _add(out, seen, Anchor(kind="symbol", value=s))

    return out


def symbol_leaf(value: str) -> str:
    """'csvtool.parser.parse_file()' -> 'parse_file'"""
    return value.removesuffix("()").split(".")[-1]
