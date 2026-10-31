"""Structural index of a snapshot: functions/classes from Python's ast, call names,
tests, guard patterns, and BM25 over whole functions and doc paragraphs."""
from __future__ import annotations

import ast
import os
import re
import threading
import warnings
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rank_bm25 import BM25Okapi

from .search import MAX_FILE_BYTES, SKIP_DIRS

MAX_PY_FILES = 6000
MAX_DOC_FILES = 400
MAX_OTHER_CODE_FILES = 4000
CHUNK_LINES = 50
DOC_EXTS = (".md", ".rst", ".txt")
# Languages without a parser here are still indexed as text chunks for search.
OTHER_CODE_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs", ".java", ".kt",
                   ".kts", ".scala", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".swift",
                   ".m", ".mm", ".dart", ".ex", ".exs", ".erl", ".hs", ".lua", ".pl", ".r", ".jl", ".sh", ".zig")
_SPLIT = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _SPLIT.findall(text.replace("_", " "))]


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str  # function | method | class
    path: str
    start: int
    end: int
    calls: set[str] = field(default_factory=set)
    is_test: bool = False
    in_test_file: bool = False
    docstring: str = ""
    guards: list[tuple[int, str]] = field(default_factory=list)  # (line, description)
    raises: list[tuple[int, str]] = field(default_factory=list)  # (line, exception name)
    subscripts: list[int] = field(default_factory=list)  # lines with x[...] lookups (KeyError/IndexError sites)
    params: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)  # parameters without defaults (self/cls excluded)
    flexible: bool = False  # accepts *args or **kwargs
    # Calls made from this function: (callee name, line, positional count, keyword names, uses *args/**kwargs)
    call_sites: list[tuple[str, int, int, tuple[str, ...], bool]] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)  # web routes from decorators like @app.route("/x/<id>")


@dataclass
class DocChunk:
    path: str
    start: int
    end: int
    text: str


@dataclass
class RepoIndex:
    root: Path
    symbols: list[Symbol]
    by_name: dict[str, list[Symbol]]
    files: set[str]
    py_lines: dict[str, int]
    unparsed: list[str]
    docs: list[DocChunk]
    code_chunks: list[DocChunk] = field(default_factory=list)  # non-Python code, as fixed-size text windows
    complete: bool = True  # False for sparse checkouts: some files exist in the commit but not on disk
    _code_bm25: Optional[BM25Okapi] = None
    _doc_bm25: Optional[BM25Okapi] = None
    _chunk_bm25: Optional[BM25Okapi] = None

    @property
    def python_share(self) -> float:
        """Share of code files that are Python: how much of the repo the structural checks can see."""
        n_py, n_other = len(self.py_lines), len({c.path for c in self.code_chunks})
        return n_py / (n_py + n_other) if n_py + n_other else 0.0

    def search_chunks(self, query: str, k: int = 3) -> list[tuple[DocChunk, float]]:
        if not self.code_chunks:
            return []
        if self._chunk_bm25 is None:
            self._chunk_bm25 = BM25Okapi([tokenize(c.path + " " + c.text) or [""] for c in self.code_chunks])
        scores = self._chunk_bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self.code_chunks, scores), key=lambda x: -x[1])
        return [(c, float(sc)) for c, sc in ranked[:k] if sc > 0]

    def lookup(self, name: str) -> list[Symbol]:
        return self.by_name.get(name, [])

    def enclosing(self, path: str, line: int) -> Optional[Symbol]:
        best = None
        for s in self.symbols:
            if s.path == path and s.start <= line <= s.end and s.kind != "class":
                if best is None or (s.end - s.start) < (best.end - best.start):
                    best = s
        return best

    def callers_of(self, name: str) -> list[Symbol]:
        return [s for s in self.symbols if name in s.calls and s.kind != "class"]

    def tests_for(self, name: str) -> list[Symbol]:
        return [s for s in self.symbols if s.is_test and name in s.calls]

    def search_code(self, query: str, k: int = 10) -> list[tuple[Symbol, float]]:
        funcs = [s for s in self.symbols if s.kind != "class"]
        if not funcs:
            return []
        if self._code_bm25 is None:
            self._code_bm25 = BM25Okapi([self._symbol_tokens(s) for s in funcs] or [[""]])
        scores = self._code_bm25.get_scores(tokenize(query))
        ranked = sorted(zip(funcs, scores), key=lambda x: -x[1])
        return [(s, float(sc)) for s, sc in ranked[:k] if sc > 0]

    def search_docs(self, query: str, k: int = 3) -> list[tuple[DocChunk, float]]:
        if not self.docs:
            return []
        if self._doc_bm25 is None:
            self._doc_bm25 = BM25Okapi([tokenize(d.text) or [""] for d in self.docs])
        scores = self._doc_bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self.docs, scores), key=lambda x: -x[1])
        return [(d, float(sc)) for d, sc in ranked[:k] if sc > 0]

    def _symbol_tokens(self, s: Symbol) -> list[str]:
        text = f"{s.qualname} {s.path} {s.docstring} {' '.join(s.calls)}"
        try:
            lines = (self.root / s.path).read_text(errors="ignore").splitlines()[s.start - 1:s.end]
            text += " " + " ".join(lines)[:4000]
        except OSError:
            pass
        return tokenize(text) or [""]


_ROUTE_DECORATORS = {"route", "get", "post", "put", "patch", "delete", "api_route", "websocket"}
_ROUTE_PARAM = re.compile(r"<(?:[^:>]+:)?([^>]+)>|\{([^}:]+)(?::[^}]+)?\}")


def route_matches(pattern: str, path: str) -> bool:
    """Flask '<id>' / '<path:p>' and FastAPI '{id}' style patterns against a concrete URL path.
    Catch-all patterns with no literal segment (like '/<path:path>') never match: they say nothing."""
    if not any(seg and not _ROUTE_PARAM.fullmatch(seg) for seg in pattern.split("/")):
        return False
    regex, pos = "", 0
    for m in _ROUTE_PARAM.finditer(pattern):
        regex += re.escape(pattern[pos:m.start()])
        regex += ".+" if m.group(0).startswith("<path:") else "[^/]+"
        pos = m.end()
    regex += re.escape(pattern[pos:])
    return re.fullmatch(regex.rstrip("/") + "/?", path) is not None


_GUARD_EXC = {"KeyError", "IndexError", "TypeError", "ValueError", "AttributeError", "Exception"}


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, is_test_file: bool):
        self.path = path
        self.is_test_file = is_test_file
        self.stack: list[str] = []
        self.out: list[Symbol] = []
        self.current: list[Symbol] = []

    def _enter(self, node, kind: str) -> None:
        qual = ".".join(self.stack + [node.name])
        params: list[str] = []
        required: list[str] = []
        flexible = False
        if kind != "class":
            a = node.args
            positional = a.posonlyargs + a.args
            params = [x.arg for x in positional + a.kwonlyargs]
            n_default = len(a.defaults)
            required = [x.arg for x in (positional[:len(positional) - n_default] if n_default else positional)]
            required += [x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
            if kind == "method" and required and required[0] in ("self", "cls"):
                required = required[1:]
            flexible = a.vararg is not None or a.kwarg is not None
        sym = Symbol(
            name=node.name, qualname=qual, kind=kind, path=self.path, start=node.lineno,
            end=getattr(node, "end_lineno", node.lineno) or node.lineno,
            is_test=self.is_test_file and node.name.startswith("test") or node.name.startswith("test_"),
            in_test_file=self.is_test_file,
            docstring=(ast.get_docstring(node) or "")[:500], params=params, required=required, flexible=flexible,
        )
        for dec in getattr(node, "decorator_list", []):
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in _ROUTE_DECORATORS \
                    and dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                sym.routes.append(dec.args[0].value)
        self.out.append(sym)
        self.stack.append(node.name)
        self.current.append(sym)
        self.generic_visit(node)
        self.current.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._enter(node, "method" if self.stack and self.current and self.current[-1].kind == "class" else "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._enter(node, "class")

    def _func(self) -> Optional[Symbol]:
        for s in reversed(self.current):
            if s.kind != "class":
                return s
        return None

    def visit_Call(self, node):
        f = self._func()
        if f is not None:
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name:
                star = any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords)
                f.call_sites.append((name, node.lineno, len(node.args),
                                     tuple(k.arg for k in node.keywords if k.arg), star))
            if isinstance(fn, ast.Name):
                f.calls.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                f.calls.add(fn.attr)
                if fn.attr == "get":
                    f.guards.append((node.lineno, ".get() lookup with default"))
            if isinstance(fn, ast.Name) and fn.id == "isinstance":
                f.guards.append((node.lineno, "isinstance() check"))
        self.generic_visit(node)

    def visit_Compare(self, node):
        f = self._func()
        if f is not None:
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(comp, ast.Constant) and comp.value is None:
                    f.guards.append((node.lineno, "None check"))
                elif isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and \
                        isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name) and node.left.func.id == "len":
                    f.guards.append((node.lineno, "length check"))
                elif isinstance(op, (ast.In, ast.NotIn)):
                    f.guards.append((node.lineno, "membership check"))
                elif isinstance(comp, ast.Constant) and comp.value in ("", 0):
                    f.guards.append((node.lineno, "empty/zero comparison"))
        self.generic_visit(node)

    def visit_If(self, node):
        f = self._func()
        if f is not None and isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
            f.guards.append((node.lineno, "falsy check (if not ...)"))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        f = self._func()
        if f is not None and isinstance(node.op, ast.Or):
            f.guards.append((node.lineno, "fallback with 'or'"))
        self.generic_visit(node)

    def visit_Try(self, node):
        f = self._func()
        if f is not None:
            for h in node.handlers:
                names = []
                if h.type is None:
                    names = ["(bare except)"]
                elif isinstance(h.type, ast.Name):
                    names = [h.type.id]
                elif isinstance(h.type, ast.Tuple):
                    names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
                if any(n in _GUARD_EXC or n == "(bare except)" for n in names):
                    f.guards.append((h.lineno, f"except {', '.join(names)}"))
        self.generic_visit(node)

    def visit_Raise(self, node):
        f = self._func()
        if f is not None and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = exc.id if isinstance(exc, ast.Name) else (exc.attr if isinstance(exc, ast.Attribute) else "")
            if name:
                f.raises.append((node.lineno, name))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        f = self._func()
        if f is not None and isinstance(node.ctx, ast.Load):
            f.subscripts.append(node.lineno)
        self.generic_visit(node)


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in ("tests", "test", "testing") for p in parts[:-1]) or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")


_LANG_CODES = {"de", "fr", "es", "pt", "pt-br", "pt_br", "zh", "zh-cn", "zh_cn", "zh-hans", "zh-hant", "zh-tw", "ja",
               "ko", "ru", "it", "tr", "uk", "pl", "nl", "vi", "id", "fa", "ar", "he", "hi", "bn", "cs", "sv", "fi",
               "da", "nb", "th", "el", "hu", "ro", "az", "em", "ur", "ta", "te"}


def _is_translation(rel: str) -> bool:
    """Translated copies of docs (docs/de/..., i18n/zh-cn/...) repeat the English text and crowd out evidence."""
    parts = rel.lower().split("/")
    return any(p in ("doc", "docs", "documentation", "i18n", "locale", "locales", "translations") for p in parts) \
        and any(p in _LANG_CODES for p in parts[:-1])


def build_index(root: Path, all_files: Optional[set[str]] = None, complete: bool = True) -> RepoIndex:
    """all_files: every path in the commit (from git), when the checkout may be partial.
    complete=False marks a sparse checkout, where 'not found' results are weaker."""
    root = Path(root)
    symbols: list[Symbol] = []
    files: set[str] = set()
    py_lines: dict[str, int] = {}
    unparsed: list[str] = []
    docs: list[DocChunk] = []
    code_chunks: list[DocChunk] = []
    py_count = doc_count = other_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            files.add(rel)
            try:
                if p.is_symlink():
                    continue
                size = p.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            if name.endswith(".py") and py_count < MAX_PY_FILES:
                py_count += 1
                source = p.read_text(errors="ignore")
                py_lines[rel] = source.count("\n") + 1
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")  # e.g. invalid escape sequences in the analysed repo
                        tree = ast.parse(source, filename=rel)
                except (SyntaxError, ValueError):
                    unparsed.append(rel)
                    continue
                v = _Visitor(rel, _is_test_path(rel))
                v.visit(tree)
                symbols.extend(v.out)
            elif name.endswith(OTHER_CODE_EXTS) and other_count < MAX_OTHER_CODE_FILES and not name.endswith(".min.js"):
                other_count += 1
                lines = p.read_text(errors="ignore").splitlines()
                for start in range(0, len(lines), CHUNK_LINES):
                    window = lines[start:start + CHUNK_LINES]
                    if any(l.strip() for l in window):
                        code_chunks.append(DocChunk(rel, start + 1, start + len(window), "\n".join(window)[:4000]))
            elif name.endswith(DOC_EXTS) and doc_count < MAX_DOC_FILES and not _is_translation(rel) and (
                "doc" in rel.lower() or name.lower().startswith(("readme", "changelog", "changes", "history"))
            ):
                doc_count += 1
                docs.extend(_doc_chunks(rel, p.read_text(errors="ignore")))

    by_name: dict[str, list[Symbol]] = defaultdict(list)
    for s in symbols:
        by_name[s.name].append(s)
        if s.qualname != s.name:
            by_name[s.qualname].append(s)
    return RepoIndex(root=root, symbols=symbols, by_name=dict(by_name), files=all_files or files,
                     py_lines=py_lines, unparsed=unparsed, docs=docs, code_chunks=code_chunks, complete=complete)


def _doc_chunks(rel: str, text: str) -> list[DocChunk]:
    chunks: list[DocChunk] = []
    lines = text.splitlines()
    start = 0
    for i in range(len(lines) + 1):
        if i == len(lines) or not lines[i].strip():
            if i > start and any(l.strip() for l in lines[start:i]):
                chunks.append(DocChunk(rel, start + 1, i, "\n".join(lines[start:i])[:2000]))
            start = i + 1
    return chunks


_cache: "OrderedDict[str, RepoIndex]" = OrderedDict()
_cache_lock = threading.Lock()


def get_index(root: Path, key: str, all_files: Optional[set[str]] = None, complete: bool = True) -> RepoIndex:
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    idx = build_index(root, all_files=all_files, complete=complete)
    with _cache_lock:
        _cache[key] = idx
        while len(_cache) > 8:
            _cache.popitem(last=False)
    return idx
