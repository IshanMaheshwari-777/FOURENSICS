"""Similarity cannot turn unrelated components into directional evidence."""
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.probes import claim_probes
from app.pipeline.retrieval import Candidate
from app.pipeline.validator import validate
from app.schemas import Anchor, Claim, ClaimAssessment, Snapshot


def test_derived_similarity_evidence_stays_weak(tmp_path, monkeypatch):
    (tmp_path / "chart.py").write_text('''
def chart_filter(value):
    """Filter values for a chart, not the table widget."""
    if value is None:
        return []
    return value[0]

def chart_selection(value):
    return chart_filter(value)
''')
    (tmp_path / "table.py").write_text('''
def table_select_all(rows):
    return rows
''')
    index = build_index(tmp_path)
    target = index.lookup("chart_filter")[0]
    monkeypatch.setattr("app.pipeline.probes.retrieve", lambda *_: [Candidate(target, 0.5, False)])
    claim = Claim(id="C1", text="An empty table selection causes IndexError", type="behavior",
                  is_primary=True, source_span="table selection causes IndexError")
    ledger = Ledger("o", "r")
    ledger.register_root("s", tmp_path)
    claim_probes(index, claim, Snapshot(sha="s"), ledger)
    derived = [e for e in ledger.items if e.kind in {"code_excerpt", "guards_present", "lookup_sites", "caller", "docstring"}]
    assert {e.kind for e in derived} >= {"guards_present", "lookup_sites", "caller", "docstring"}
    assert all(e.tier == "E4" for e in derived)
    a = ClaimAssessment(claim_id="C1", technical="supported", evidence_ids=[e.id for e in derived],
                        relations={e.id: "supports" for e in derived})
    assert validate([a], [claim], ledger, Snapshot(sha="s", reliability="exact"), {})[0].technical == "inconclusive"
    # A directly named API retains structural evidence; the safeguard is about relevance, not syntax.
    claim.anchors = [Anchor(kind="symbol", value="chart_filter")]
    anchored = Ledger("o", "r")
    anchored.register_root("s", tmp_path)
    claim_probes(index, claim, Snapshot(sha="s"), anchored)
    assert next(e for e in anchored.items if e.kind == "guards_present").tier == "E2"
    assert next(e for e in anchored.items if e.kind == "caller").tier == "E3"


def test_a_route_does_not_anchor_unrelated_similarity_results(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text('''
@app.route("/records/<rid>")
def records(rid):
    return rid

def unrelated_cache(value):
    return value
''')
    index = build_index(tmp_path)
    other = index.lookup("unrelated_cache")[0]
    monkeypatch.setattr("app.pipeline.probes.retrieve", lambda *_: [Candidate(other, 1.0, False)])
    claim = Claim(id="C1", text="GET /records/5 has incorrect output", type="behavior", source_span="incorrect output",
                  anchors=[Anchor(kind="route", value="/records/5")])
    ledger = Ledger("o", "r")
    claim_probes(index, claim, Snapshot(sha="s"), ledger)
    code = {e.result: e.tier for e in ledger.items if e.kind == "code_excerpt"}
    assert any("records`" in name and tier == "E3" for name, tier in code.items())
    assert any("unrelated_cache`" in name and tier == "E4" for name, tier in code.items())
