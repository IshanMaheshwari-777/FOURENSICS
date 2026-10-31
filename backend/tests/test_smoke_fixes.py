"""Regression tests for problems found by the smoke test on real repositories."""
from __future__ import annotations

from app.db import new_run_id
from app.pipeline.aggregate import summarize
from app.pipeline.extract_regex import extract_anchors
from app.pipeline.index import _is_translation, build_index
from app.pipeline.ledger import Ledger
from app.pipeline.manifests import declarations, mentioned
from app.pipeline.orchestrator import analyze
from app.pipeline.probes import reference_probes
from app.pipeline.validator import validate
from app.schemas import Anchor, Claim, ClaimAssessment, PanelResult, Snapshot, Target

from conftest import OWNER, REPO


def _run(deps, n, kind="issue"):
    path = "pull" if kind == "pr" else "issues"
    r = PanelResult(run_id=new_run_id())
    deps.db.new_run(f"t:{r.run_id}", r)
    return analyze(deps, r, target=Target(owner=OWNER, repo=REPO, kind=kind, number=n,
                                          url=f"https://github.com/{OWNER}/{REPO}/{path}/{n}"))


def test_hidden_folders_like_github_workflows_are_seen(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text("on: push\n")
    idx = build_index(tmp_path)
    ledger = Ledger("o", "r")
    out = reference_probes(idx, [Anchor(kind="path", value=".github/workflows/release.yml")], Snapshot(sha="s"), ledger)
    assert [ledger.get(i).kind for ids in out.values() for i in ids] == ["path_found"]


def test_files_outside_a_sparse_checkout_still_exist(tmp_path):
    idx = build_index(tmp_path, all_files={"assets/logo.png", "src/a.py"}, complete=False)
    ledger = Ledger("o", "r")
    out = reference_probes(idx, [Anchor(kind="path", value="assets/logo.png"),
                                 Anchor(kind="symbol", value="nowhere_fn")], Snapshot(sha="s"), ledger)
    kinds = {ledger.get(i).kind for ids in out.values() for i in ids}
    assert kinds == {"path_found", "symbol_not_in_sources"}  # never "absent" from a partial checkout


def test_branch_named_in_report_is_used(make_deps, origin, fake_github):
    body = fake_github.issues[42]["body"].replace("Using csvtool version 2.3.1,", "On the `1.x` branch,")
    fake_github.issues[42]["body"] = body
    r = _run(make_deps(), 42)
    assert r.snapshot.sha == origin[1]["v2.2.0"] and r.snapshot.ref_label.startswith("1.x branch")


def test_dependency_manifest_evidence(tmp_path):
    (tmp_path / "package.json").write_text(
        '{\n  "name": "app",\n  "scripts": {\n    "test": "mocha"\n  },\n'
        '  "dependencies": {\n    "body-parser": "~1.20.3",\n    "debug": "2.6.9"\n  }\n}\n')
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = [\n  "requests>=2.31",\n]\n')
    decls = declarations(tmp_path, {"package.json", "pyproject.toml"})
    names = {d.name for d in decls}
    assert {"body-parser", "debug", "requests"} <= names and "test" not in names
    hit = mentioned(decls, "The 4.x branch specifies body-parser ~1.20.5, which allows vulnerable versions")
    assert [(d.path, d.line) for d in hit] == [("package.json", 7)]


def test_documentation_alone_cannot_decide_claims_about_code(tmp_path):
    (tmp_path / "History.md").write_text("1.0\n===\n\n * deps: body-parser@1.20.1\n")
    ledger = Ledger("o", "r")
    ledger.register_root("s", tmp_path)
    doc = ledger.add("doc_excerpt", "E3", "Documentation in History.md", "s", claim_ids=["C1", "C2"],
                     path="History.md", start=4, end=4)
    claims = [Claim(id="C1", text="package.json pins body-parser ~1.20.5", type="existence", source_span="x"),
              Claim(id="C2", text="Passing x raises an error", type="behavior", is_primary=True, source_span="y")]
    out = validate([
        ClaimAssessment(claim_id="C1", technical="contradicted", evidence_ids=[doc.id], relations={doc.id: "contradicts"},
                        explanation="Changelog lists 1.20.1", decided_by="model"),
        ClaimAssessment(claim_id="C2", technical="contradicted", evidence_ids=[doc.id], relations={doc.id: "contradicts"},
                        explanation="Documented as intended", decided_by="model"),
    ], claims, ledger, Snapshot(sha="s", reliability="inferred"), {})
    assert out[0].technical == "inconclusive" and "documentation" in out[0].downgraded_reason
    assert out[1].technical == "contradicted"  # "documented as intended" may contradict a bug report

    support = validate([ClaimAssessment(claim_id="C2", technical="supported", evidence_ids=[doc.id],
                                        relations={doc.id: "supports"}, decided_by="model")],
                       claims, ledger, Snapshot(sha="s", reliability="inferred"), {})
    assert support[0].technical == "inconclusive"  # docs never confirm a bug


def test_translated_docs_are_skipped():
    assert _is_translation("docs/de/docs/tutorial/index.md")
    assert _is_translation("docs/zh/pages/advanced/fetch.md")
    assert not _is_translation("docs/en/docs/tutorial/index.md")
    assert not _is_translation("src/go/parser.md")


def test_pr_diff_is_evidence_for_any_file(make_deps):
    r = _run(make_deps(), 7, kind="pr")
    diffs = [e for e in r.evidence if e.kind == "pr_diff"]
    parser = next(e for e in diffs if e.path == "csvtool/parser.py")
    assert "-    index = {h: i for i, h in enumerate(header)}" in parser.raw
    assert "+    index = {n: i for i, n in enumerate(names)}" in parser.raw


def test_versions_of_other_software_are_not_used_as_project_releases(make_deps, origin, fake_github):
    from app.pipeline.snapshot import _belongs_elsewhere, _project_names
    from app.schemas import AnalysisInput

    inp = AnalysisInput(target=Target(owner="BurntSushi", repo="ripgrep", kind="issue", number=1, url="u"),
                        title="t", body="b", body_hash="h", created_at="2026-01-01T00:00:00Z")
    names = _project_names(inp)
    vs = {a.context.strip()[-20:]: a for a in extract_anchors(
        "pins `CROSS_VERSION: v0.2.5`; on node v18.2.0; Python 3.12.1; with ripgrep 14.1.1; version 2.3.1")
        if a.kind == "version"}
    verdicts = {a.value: _belongs_elsewhere(a, names) for a in vs.values()}
    assert verdicts == {"0.2.5": True, "18.2.0": True, "3.12.1": True, "14.1.1": False, "2.3.1": False}

    # A fixture version tied to another tool is ignored: falls back to the date-based version.
    fake_github.issues[42]["body"] = fake_github.issues[42]["body"].replace(
        "Using csvtool version 2.3.1,", "With TOOL_VERSION=2.2.0 in CI,")
    r = _run(make_deps(), 42)
    assert r.snapshot.method == "date"


def test_dependencies_key_is_not_a_package(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = [\n  "requests>=2.31",\n]\n')
    names = {d.name for d in declarations(tmp_path, {"pyproject.toml"})}
    assert names == {"requests"}


def test_cargo_settings_are_not_packages(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "rg"\nbuild = "build.rs"\n\n'
                                        '[dependencies]\nregex = "1.10"\nserde = { version = "1" }\n')
    names = {d.name for d in declarations(tmp_path, {"Cargo.toml"})}
    assert names == {"regex", "serde"}


def test_missing_info_is_language_neutral():
    from app.pipeline.missing_info import check_missing
    js = ("Observed: a Vite 7.3.7 app with zod@4.6.7 prints two warnings on `vite build`:\n"
          "```\nnode_modules/zod/v4/core/util.js (400:0): A comment ... contains an annotation that Rollup "
          "cannot interpret; warning\n```\nExpected: no warnings. Node v22.1.0 on macOS.")
    assert check_missing(js, extract_anchors(js)) == []
    go = "Running the CLI panics.\n```\npanic: runtime error\n```\nI expected it to print help. go1.22 on linux, v1.8.0"
    assert check_missing(go, extract_anchors(go)) == []


def test_another_librarys_method_cannot_contradict_a_behaviour_claim(tmp_path):
    (tmp_path / "httpx").mkdir()
    (tmp_path / "httpx" / "client.py").write_text("class AsyncClient:\n    async def stream(self):\n        pass\n")
    idx = build_index(tmp_path)
    ledger = Ledger("o", "r")
    ledger.register_root("s", tmp_path)
    anchors = [Anchor(kind="symbol", value="task.cancel"), Anchor(kind="symbol", value="close_all_pools")]
    out = reference_probes(idx, anchors, Snapshot(sha="s"), ledger)
    kinds = {ledger.get(i).kind for ids in out.values() for i in ids}
    assert kinds == {"symbol_external", "symbol_absent"}

    absent = next(ledger.get(i) for ids in out.values() for i in ids if ledger.get(i).kind == "symbol_absent")
    absent.claim_ids = ["C1"]
    claims = [Claim(id="C1", text="double cancel leaks connections", type="behavior", is_primary=True, source_span="x")]
    out = validate([ClaimAssessment(claim_id="C1", technical="contradicted", evidence_ids=[absent.id],
                                    relations={absent.id: "contradicts"}, decided_by="model")],
                   claims, ledger, Snapshot(sha="s", reliability="release-matched"), {})
    assert out[0].technical == "inconclusive"


def test_test_files_are_recognised_in_every_language():
    from app.pipeline.pr import _is_test_path
    tests = ["Tests/AFError+AlamofireTests.swift", "src/test/java/FooTest.java", "pkg/server_test.go",
             "src/a.test.ts", "lib/b.spec.js", "spec/models/user_spec.rb", "tests/test_api.py", "__tests__/x.jsx",
             "Foo.Tests/BarTests.cs", "crates/x/tests/it.rs"]
    code = ["Source/Core/AFError.swift", "src/main/java/Foo.java", "pkg/server.go", "src/contest.ts", "README.md"]
    assert all(_is_test_path(p) for p in tests)
    assert not any(_is_test_path(p) for p in code)


def test_clear_outcome_does_not_need_an_expected_section():
    from app.pipeline.missing_info import check_missing
    race = ("With fzf v0.74.5 on macOS (go1.26.10): when run as a library, the race detector reports two data "
            "races and the program exits with code 66.\n```\nWARNING: DATA RACE\n```\nSteps: start it and type.")
    assert "expected_actual" not in check_missing(race, extract_anchors(race))


def test_negative_pr_claim_is_not_contradicted_by_an_untouched_file(tmp_path):
    ledger = Ledger("o", "r")
    miss = ledger.add("pr_diff_misses_claim", "E2", "The diff does not change `Usage.md`", "s", claim_ids=["C1"])
    claims = [Claim(id="C1", text="Updated docs, and no Usage.md change", type="docs", source_span="x")]
    out = validate([ClaimAssessment(claim_id="C1", technical="contradicted", evidence_ids=[miss.id],
                                    relations={miss.id: "contradicts"}, decided_by="model")],
                   claims, ledger, Snapshot(sha="s", reliability="exact"), {})
    assert out[0].technical == "inconclusive"


def test_feature_request_proposals_are_not_claims():
    from app.llm.gateway import FakeGateway
    from app.pipeline.claims import extract_claims
    from app.schemas import AnalysisInput
    body = "Currently `stream()` yields nested events. I propose a flattened API that would yield data directly."
    inp = AnalysisInput(target=Target(owner="o", repo="r", kind="issue", number=1, url="u"), title="Flatten stream",
                        body=body, body_hash="h", created_at="2026-01-01T00:00:00Z")
    gw = FakeGateway({"extract": {"report_kind": "feature", "claims": [
        {"text": "stream() yields nested events", "type": "behavior", "is_primary": True,
         "source_span": "Currently `stream()` yields nested events"},
        {"text": "Proposed API would yield data directly", "type": "existence",
         "source_span": "I propose a flattened API that would yield data directly"}]}})
    kind, claims, notes = extract_claims(inp, [], gw)
    assert kind == "feature" and [c.text for c in claims] == ["stream() yields nested events"]
    assert any("proposed feature" in n for n in notes)


def test_installed_or_built_paths_map_to_source_and_are_never_missing(tmp_path):
    src = tmp_path / "packages" / "zod" / "src" / "v4" / "core"
    src.mkdir(parents=True)
    (src / "util.ts").write_text("export const x = 1;\n")
    idx = build_index(tmp_path)
    ledger = Ledger("o", "r")
    anchors = [Anchor(kind="path", value="node_modules/zod/v4/core/util.js"),
               Anchor(kind="path", value="node_modules/zod/v4/core/nothing.js"),
               Anchor(kind="path", value="dist/esm/other.mjs")]
    out = reference_probes(idx, anchors, Snapshot(sha="s"), ledger)
    items = [ledger.get(i) for ids in out.values() for i in ids]
    assert [(i.kind, i.path) for i in items] == [("path_built_from", "packages/zod/src/v4/core/util.ts")]
    js_frames = extract_anchors("at f (node_modules/zod/v4/core/util.js:400:3)")
    out = reference_probes(idx, js_frames, Snapshot(sha="s"), ledger)
    assert not any(ledger.get(i).kind.startswith("frame_") for ids in out.values() for i in ids)


def test_commit_hashes_are_not_symbols():
    values = {(a.kind, a.value) for a in extract_anchors("Fixed on main in `ce7ebdb3`; see `parseThing()`.")}
    assert ("symbol", "ce7ebdb3") not in values and ("commit", "ce7ebdb3") in values
    assert ("symbol", "parseThing") in values


def test_builtins_are_not_project_symbols():
    values = {a.value for a in extract_anchors("Use `float` and `Field(gt=0)`; `Promise` and `my_helper()` too")
              if a.kind == "symbol"}
    assert "float" not in values and "Promise" not in values and "my_helper" in values


def test_non_specific_reports_are_labelled():
    claims = [Claim(id="C1", text="AI report score 72/100", type="behavior", is_primary=True, source_span="x")]
    line, tone = summarize("other", claims, [ClaimAssessment(claim_id="C1")])
    assert tone == "not_defect" and "specific technical claim" in line
