"""Behaviour outside Python and outside the happy path: other languages, long reports,
large repos, monorepo tags, transferred issues."""
from __future__ import annotations

import subprocess

from app.llm.gateway import FakeGateway, LLMTooLarge
from app.pipeline.claims import extract_claims, fit_text
from app.pipeline.extract_regex import extract_anchors
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.orchestrator import fetch_input
from app.pipeline.probes import _def_regex, _match_repo_path, claim_probes, reference_probes
from app.pipeline.snapshot import RepoCache, _prefixed_tags
from app.schemas import AnalysisInput, Claim, Snapshot, Target

from conftest import OWNER, REPO

JS = """export function parseFile(text) {
  const rows = text.split("\\n");
  return rows.map((r) => r.length);
}

const helper = (x) => x * 2;
module.exports = { helper };
"""


def _js_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parse.js").write_text(JS)
    (tmp_path / "src" / "main.go").write_text("package main\n\nfunc (s *Server) Handle(w int) error {\n\treturn nil\n}\n")
    (tmp_path / "README.md").write_text("# demo\n\nparseFile turns text into rows.\n")
    idx = build_index(tmp_path)
    ledger = Ledger("o", "r")
    ledger.register_root("sha", tmp_path)
    return idx, ledger


def test_definition_regex_across_languages():
    cases = {
        "parseFile": ["export function parseFile(text) {", "const parseFile = (t) => t", "  parseFile: function (t) {"],
        "Handle": ["func (s *Server) Handle(w int) error {", "func Handle(w int) {"],
        "parse": ["pub fn parse(input: &str) -> Result<()>", "fn parse<T>(x: T)", "def parse(self):"],
        "Widget": ["class Widget extends Base {", "struct Widget {", "interface Widget {", "type Widget struct {"],
        "render": ["  public static void render(Graphics g) {", "  private async render(): Promise<void> {"],
    }
    for name, lines in cases.items():
        for line in lines:
            assert _def_regex(name).search(line), (name, line)
    assert not _def_regex("parseFile").search("const rows = parseFile(text);")


def test_js_repo_gets_text_evidence(tmp_path):
    idx, ledger = _js_repo(tmp_path)
    assert idx.python_share == 0.0 and idx.code_chunks
    text = ("`parseFile()` crashes:\nTypeError: Cannot read properties of undefined (reading 'length')\n"
            "    at parseFile (src/parse.js:3:24)\n    at Object.<anonymous> (/app/node_modules/x/index.js:9:1)")
    anchors = extract_anchors(text)
    frames = [a for a in anchors if a.kind == "traceback_frame"]
    assert any(a.value == "src/parse.js" and a.line == 3 for a in frames)

    out = reference_probes(idx, anchors, Snapshot(sha="sha"), ledger)
    kinds = {ledger.get(i).kind for ids in out.values() for i in ids}
    assert {"symbol_defined_text", "frame_match"} <= kinds

    claim = Claim(id="C1", text="parseFile crashes on undefined rows", type="behavior", is_primary=True,
                  source_span="x", anchors=[a for a in anchors if a.kind in ("symbol", "error")])
    claim_probes(idx, claim, Snapshot(sha="sha"), ledger)
    excerpts = [i for i in ledger.for_claim("C1") if i.kind == "code_excerpt"]
    assert any("Definition of `parseFile`" in i.result and i.excerpt.startswith("export function parseFile")
               for i in excerpts)


def test_out_of_range_frame_in_other_language(tmp_path):
    idx, ledger = _js_repo(tmp_path)
    anchors = extract_anchors("at parseFile (src/parse.js:300:1)")
    out = reference_probes(idx, anchors, Snapshot(sha="sha"), ledger)
    assert any(ledger.get(i).kind == "frame_line_out_of_range" for ids in out.values() for i in ids)


def test_ambiguous_bare_file_names_are_not_matched(tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "main.go").write_text("package main\n")
    idx = build_index(tmp_path)
    assert _match_repo_path(idx, "main.go") is None
    assert _match_repo_path(idx, "/home/ci/b/main.go") == "b/main.go"
    assert _match_repo_path(idx, "./a/main.go") == "a/main.go"


def test_panic_and_rust_frames_become_anchors():
    anchors = extract_anchors("thread 'main' panicked at 'index out of bounds', src/lib.rs:42:9\n"
                              "panic: runtime error: invalid memory address\n\t/build/pkg/server.go:118 +0x1d")
    values = {(a.kind, a.value, a.line) for a in anchors}
    assert ("traceback_frame", "src/lib.rs", 42) in values
    assert ("traceback_frame", "build/pkg/server.go", 118) in values  # leading "/" is dropped; matched by suffix
    assert any(k == "error" and v.startswith("panic: index out of bounds") for k, v, _ in values)


def test_long_report_is_fitted_and_extraction_retries_when_too_large():
    long_body = "Steps:\n" + ("log line with details\n" * 4000) + "Expected it to work."
    assert len(fit_text(long_body, 5000)) <= 5000
    assert fit_text(long_body, 5000).endswith("Expected it to work.")

    inp = AnalysisInput(target=Target(owner="o", repo="r", kind="issue", number=1, url="https://github.com/o/r/issues/1"),
                        title="Crash", body=long_body, body_hash="h", created_at="2025-01-01T00:00:00Z")
    ok = {"report_kind": "defect", "claims": [{"text": "It crashes", "type": "behavior", "is_primary": True,
                                                 "source_span": "Expected it to work"}]}
    gw = FakeGateway({"extract": [LLMTooLarge("too big", 8000), ok]}, prompt_budget_tokens=3000)
    kind, claims, notes = extract_claims(inp, [], gw)
    assert len(gw.calls) == 2 and claims[0].text == "It crashes"
    assert len(gw.calls[0]["user"]) < 3000 * 4
    assert any("shortened" in n for n in notes)


def test_monorepo_tags():
    tags = {"core@1.4.0", "cli@1.4.0", "web-v2.0.1", "v0.9.0"}
    notes: list[str] = []
    assert _prefixed_tags(tags, "2.0.1", "", notes) == ["web-v2.0.1"]
    assert _prefixed_tags(tags, "1.4.0", "the cli crashes on start", notes) == ["cli@1.4.0"]
    assert _prefixed_tags(tags, "1.4.0", "it crashes", notes) == [] and "Several packages" in notes[-1]


def test_sparse_checkout_skips_non_source_files(tmp_path, origin):
    path, shas = origin
    cache = RepoCache(tmp_path / "cache", 60, {f"{OWNER}/{REPO}": str(path)})
    cache.ensure_clone(OWNER, REPO)
    wt = cache.worktree(OWNER, REPO, shas["v2.3.1"], sparse=True)
    assert (wt / "csvtool" / "parser.py").exists() and (wt / "docs" / "usage.md").exists()
    assert not (wt / "data" / "sample.csv").exists()
    full = cache.worktree(OWNER, REPO, shas["v2.3.1"])
    assert (full / "data" / "sample.csv").exists()


def test_transferred_issue_follows_its_new_repository(make_deps, fake_github):
    fake_github.issues[42]["repository_url"] = "https://api.github.com/repos/neworg/csvtool2"
    deps = make_deps()
    target, inp = fetch_input(deps, Target(owner=OWNER, repo=REPO, kind="issue", number=42,
                                          url=f"https://github.com/{OWNER}/{REPO}/issues/42"), "live")
    assert (target.owner, target.repo) == ("neworg", "csvtool2") and inp.target.repo == "csvtool2"
