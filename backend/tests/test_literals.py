"""Quoted report text is evidence to inspect, never an automatic behaviour verdict."""
from __future__ import annotations

from app.pipeline.claims import attach_anchors
from app.pipeline.extract_regex import extract_anchors, MAX_LITERALS
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.probes import reference_probes
from app.pipeline.search import search_literals
from app.pipeline.validator import validate
from app.schemas import Claim, ClaimAssessment, Snapshot


def test_literals_keep_punctuation_quotes_and_log_lines():
    text = ('The annotation `@__PURE__` disappears; "unexpected end of input" is shown.\n'
            "```python\nraise ValueError('bad [input].*')\n```\n"
            '2025-07-03 10:00 WARNING: Cache directory is read-only\n')
    values = {a.value for a in extract_anchors(text) if a.kind == "literal"}
    assert {"@__PURE__", "unexpected end of input", "bad [input].*", "Cache directory is read-only"} <= values
    assert "python" not in values
    annotations = {a.value for a in extract_anchors('```js\nconst x = /* #__NO_SIDE_EFFECTS__ */ call();\n```')
                   if a.kind == "literal"}
    assert "#__NO_SIDE_EFFECTS__" in annotations
    assert len([a for a in extract_anchors(" ".join(f'`token{i}`' for i in range(100)))
                if a.kind == "literal"]) == MAX_LITERALS


def test_literal_search_cross_language_and_safe_files(tmp_path, monkeypatch):
    import app.pipeline.search as search
    monkeypatch.setattr(search, "_rg_available", lambda: False)
    (tmp_path / "src").mkdir()
    for name, content in {
        "a.ts": "// @__PURE__\nexport const a = 1;\n",
        "b.py": 'import warnings\nwarnings.warn("Cache directory is read-only")\n',
        "c.rs": 'fn check() { panic!("bad [input].*"); }\n',
    }.items():
        (tmp_path / "src" / name).write_text(content)
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "ci.yml").write_text("# @__PURE__\n")
    (tmp_path / "binary.dat").write_bytes(b"\0@__PURE__")
    (tmp_path / ".git").write_text("gitdir: /private/local/@__PURE__")
    (tmp_path / "oversize.txt").write_text("@__PURE__" + "x" * 512000)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignore.js").write_text("@__PURE__")
    outside = tmp_path.parent / "outside-literal.txt"
    outside.write_text("outside marker")
    (tmp_path / "link.txt").symlink_to(outside)
    needles = ["@__PURE__", "Cache directory is read-only", "bad [input].*", "outside marker", "bad input"]
    found = search_literals(tmp_path, needles)
    assert {h.path for h in found["@__PURE__"]} == {"src/a.ts", ".github/ci.yml"}
    assert found["Cache directory is read-only"][0].line == 2
    assert found["bad [input].*"][0].path == "src/c.rs"
    assert not found["outside marker"] and not found["bad input"]


def test_literal_matches_are_linked_and_docs_cannot_confirm_code(tmp_path):
    (tmp_path / "src.py").write_text('def render():\n    return "@__PURE__"\n')
    (tmp_path / "guide.md").write_text('The "@__PURE__" annotation can be used.\n')
    anchors = extract_anchors('Using `@__PURE__` crashes the renderer; "not in repository".')
    index = build_index(tmp_path)
    ledger = Ledger("demo", "tree")
    ledger.register_root("abc", tmp_path)
    mapping = reference_probes(index, anchors, Snapshot(sha="abc"), ledger)
    claim = Claim(id="C1", text="The renderer crashes", source_span="crashes the renderer",
                  type="behavior", is_primary=True)
    attach_anchors([claim], anchors, {})
    assert any(a.kind == "literal" and a.value == "@__PURE__" for a in claim.anchors)
    for a in claim.anchors:
        for eid in mapping.get((a.kind, a.value, a.line), []):
            ledger.link(eid, claim.id)
    items = ledger.for_claim("C1")
    assert {e.kind for e in items} >= {"literal_match", "literal_in_docs"}
    src = next(e for e in items if e.kind == "literal_match")
    assert "@__PURE__" in src.excerpt and src.permalink.endswith("src.py#L1-L2")
    assert not any(e.absence for e in ledger.items if e.kind.startswith("literal"))
    doc = next(e for e in items if e.kind == "literal_in_docs")
    assessment = ClaimAssessment(claim_id="C1", technical="supported", evidence_ids=[doc.id],
                                 relations={doc.id: "supports"})
    validated = validate([assessment], [claim], ledger, Snapshot(sha="abc", reliability="exact"), {})[0]
    assert validated.technical == "inconclusive" and "documentation" in validated.downgraded_reason


def test_batch_search_bounds_results_and_keeps_distinct_needles(tmp_path):
    (tmp_path / "a.txt").write_text("first token\n" * 50 + "last token\n")
    out = search_literals(tmp_path, ["first token", "last token", "first token"], max_hits=2)
    assert len(out["first token"]) == 2 and out["last token"][0].line == 51


def test_source_matches_survive_many_earlier_doc_matches(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("reported marker\n" * 100)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("# reported marker\n")
    hits = search_literals(tmp_path, ["reported marker"], max_hits=2)["reported marker"]
    assert len(hits) == 2 and hits[0].path == "src/a.py"
