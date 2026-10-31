from app.pipeline.adjudicate import hard_rules
from app.pipeline.ledger import Ledger
from app.pipeline.validator import validate
from app.schemas import Claim, ClaimAssessment, Snapshot


def test_diff_presence_does_not_prove_counts_or_passing_tests():
    ledger = Ledger("o", "r")
    item = ledger.add("pr_tests_changed", "E2", "Test code changed", "sha", claim_ids=["C1"])
    snapshot = Snapshot(sha="sha", reliability="exact")
    claim = Claim(id="C1", text="Adds a regression test", source_span="x", type="pr_test")
    assert hard_rules([claim], ledger, snapshot, {})["C1"].technical == "supported"
    claim.text = "Adds 17 tests covering every case"
    assert hard_rules([claim], ledger, snapshot, {}) == {}
    legacy = ClaimAssessment(claim_id="C1", technical="supported", evidence_ids=[item.id],
                            relations={item.id: "supports"}, decided_by="rule")
    assert validate([legacy], [claim], ledger, snapshot, {})[0].technical == "inconclusive"
    legacy.decided_by = "model"
    assert validate([legacy], [claim], ledger, snapshot, {})[0].technical == "inconclusive"
    claim.text = "The existing assertions still pass, confirming no regression"
    assert hard_rules([claim], ledger, snapshot, {}) == {}
    assessment = ClaimAssessment(claim_id="C1", technical="supported", evidence_ids=[item.id],
                                 relations={item.id: "supports"}, decided_by="model")
    after = validate([assessment], [claim], ledger, snapshot, {})[0]
    assert after.technical == "inconclusive" and "test outcomes" in after.downgraded_reason


def test_execution_evidence_can_support_a_test_outcome():
    ledger = Ledger("o", "r")
    item = ledger.add("sandbox_run", "E1", "Reported tests passed", "sha", claim_ids=["C1"])
    claim = Claim(id="C1", text="Tests pass", source_span="x", type="pr_test")
    assessment = ClaimAssessment(claim_id="C1", technical="supported", evidence_ids=[item.id],
        relations={item.id: "supports"}, decided_by="model")
    assert validate([assessment], [claim], ledger, Snapshot(sha="sha", reliability="exact"), {})[0].technical == "supported"
