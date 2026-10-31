"""Deterministic probes on small throwaway source trees."""
from __future__ import annotations

from app.pipeline.adjudicate import hard_rules
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.probes import _call_arity_probe, reference_probes
from app.schemas import Anchor, Claim, Snapshot

SRC = '''
def export(doc, uuid, last_modified, fmt="json"):
    return doc

class Exporter:
    def run(self, doc, mode):
        return doc

def view(cre):
    return export(cre)

def ok_view(cre):
    return export(cre, None, None)

def kw_view(cre):
    return export(cre, uuid=None, last_modified=None)

def method_view(e):
    return e.run(1, 2)
'''


def _setup(tmp_path, src=SRC):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "export.py").write_text(src)
    idx = build_index(tmp_path)
    ledger = Ledger("o", "r")
    ledger.register_root("sha", tmp_path)
    return idx, ledger


def test_arity_probe_finds_the_bad_call_only(tmp_path):
    idx, ledger = _setup(tmp_path)
    items = _call_arity_probe(idx, "TypeError: export() missing 2 required positional arguments: 'uuid' and "
                                   "'last_modified'", ledger, "sha")
    kinds = [i.kind for i in items]
    assert kinds == ["signature", "call_site_mismatch"]
    bad = items[1]
    assert bad.result == "`view` calls `export` without `uuid`, `last_modified` (passes 1 of 3 required)"
    assert bad.excerpt.strip() == "return export(cre)"
    assert bad.permalink.endswith("app/export.py#L10")


def test_arity_probe_reports_all_calls_fine(tmp_path):
    idx, ledger = _setup(tmp_path, SRC.replace("return export(cre)\n", "return export(cre, 1, 2)\n"))
    items = _call_arity_probe(idx, "TypeError: export() missing 1 required positional argument: 'uuid'", ledger, "sha")
    assert [i.kind for i in items] == ["signature", "call_sites_match"]


def test_method_self_is_not_required(tmp_path):
    idx, _ = _setup(tmp_path)
    run = idx.lookup("run")[0]
    assert run.required == ["doc", "mode"]


def test_arity_mismatch_decides_behaviour_claim(tmp_path):
    idx, ledger = _setup(tmp_path)
    err = Anchor(kind="error", value="TypeError: export() missing 2 required positional arguments: 'uuid' and 'last_modified'")
    claim = Claim(id="C1", text="the view crashes with TypeError", type="behavior", is_primary=True,
                  source_span="x", anchors=[err])
    snap = Snapshot(sha="sha", method="date", reliability="inferred")
    found = reference_probes(idx, [err], snap, ledger)
    for ids in found.values():
        for i in ids:
            ledger.link(i, "C1")
    decided = hard_rules([claim], ledger, snap, {})
    assert decided["C1"].technical == "supported" and decided["C1"].decided_by == "rule"


def test_route_matching_and_handler_probe(tmp_path):
    from app.pipeline.extract_regex import extract_anchors
    from app.pipeline.index import route_matches

    assert route_matches("/rest/v1/id/<creid>", "/rest/v1/id/111-111")
    assert route_matches("/rest/v1/name/<path:crename>", "/rest/v1/name/Root%20CRE/x")
    assert route_matches("/items/{item_id}", "/items/5")
    assert not route_matches("/rest/v1/id/<creid>", "/rest/v1/id/1/extra")
    assert not route_matches("/<path:path>", "/rest/v1/standard")  # catch-all pages say nothing

    src = SRC + '''
class app:
    pass

@app.route("/rest/v1/id/<creid>", methods=["GET"])
@app.route("/rest/v1/name/<path:crename>", methods=["GET"])
def find_cre(creid=None, crename=None):
    return view(creid)
'''
    idx, ledger = _setup(tmp_path, src)
    anchors = [a for a in extract_anchors("GET /rest/v1/name/Root%20CRE?format=oscal returns a 500") if a.kind == "route"]
    assert [a.value for a in anchors] == ["/rest/v1/name/Root%20CRE"]
    out = reference_probes(idx, anchors, Snapshot(sha="sha"), ledger)
    item = ledger.get(next(iter(out.values()))[0])
    assert item.kind == "route_handler" and "find_cre" in item.result


def test_arity_rule_cites_the_endpoint_when_handler_has_the_bad_call(tmp_path):
    src = SRC + '''
class app:
    pass

@app.route("/export/<cid>")
def export_view(cid):
    return export(cid)
'''
    idx, ledger = _setup(tmp_path, src)
    err = Anchor(kind="error", value="TypeError: export() missing 2 required positional arguments: 'uuid' and 'last_modified'")
    route = Anchor(kind="route", value="/export/42")
    claim = Claim(id="C1", text="GET /export/42 fails with TypeError", type="behavior", is_primary=True,
                  source_span="x", anchors=[err, route])
    snap = Snapshot(sha="sha", method="exact", reliability="exact")
    for ids in reference_probes(idx, [err, route], snap, ledger).values():
        for i in ids:
            ledger.link(i, "C1")
    a = hard_rules([claim], ledger, snap, {})["C1"]
    assert a.technical == "supported"
    assert any(ledger.get(i).kind == "route_handler" for i in a.evidence_ids)
    assert "endpoint's handler" in a.explanation and a.limitations[0].startswith("Not run")


def test_parameter_names_are_recognised(tmp_path):
    idx, ledger = _setup(tmp_path)
    anchors = [Anchor(kind="symbol", value="export"), Anchor(kind="symbol", value="last_modified")]
    out = reference_probes(idx, anchors, Snapshot(sha="sha"), ledger)
    kinds = {ledger.get(i).kind for ids in out.values() for i in ids}
    assert kinds == {"symbol_found", "symbol_is_parameter"}
