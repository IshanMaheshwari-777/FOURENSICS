"""The evaluation harness runs end to end on fixture questions, and labels stay hidden."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.llm.gateway import FakeGateway
from app.db import Database
from app.schemas import PanelResult

from conftest import BUG_BODY, FABRICATED_BODY, QUESTION_BODY, adjudicate_response, extract_response

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import run_eval  # noqa: E402


def test_eval_harness_on_fixture(tmp_path, settings, fake_github):
    data = tmp_path / "data"
    data.mkdir()
    qs = [
        {"id": "acme/csvtool#42", "url": "https://github.com/acme/csvtool/issues/42", "owner": "acme", "repo": "csvtool",
         "number": 42, "title": "parse_file crashes with KeyError on an empty column", "body": BUG_BODY,
         "created_at": "2025-03-10T12:00:00Z", "default_branch": "main", "split": "dev"},
        {"id": "acme/csvtool#43", "url": "https://github.com/acme/csvtool/issues/43", "owner": "acme", "repo": "csvtool",
         "number": 43, "title": "parse_stream() leaks file handles", "body": FABRICATED_BODY,
         "created_at": "2025-03-12T12:00:00Z", "default_branch": "main", "split": "dev"},
        {"id": "acme/csvtool#44", "url": "https://github.com/acme/csvtool/issues/44", "owner": "acme", "repo": "csvtool",
         "number": 44, "title": "How do I read TSV files?", "body": QUESTION_BODY,
         "created_at": "2025-03-12T12:00:00Z", "default_branch": "main", "split": "dev"},
    ]
    answers = [{"id": "acme/csvtool#42", "label": "confirmed"}, {"id": "acme/csvtool#43", "label": "invalid"},
               {"id": "acme/csvtool#44", "label": "invalid"}]
    (data / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in qs))
    (data / "answers.jsonl").write_text("".join(json.dumps(a) + "\n" for a in answers))

    gw = FakeGateway({"extract": extract_response, "adjudicate": adjudicate_response,
                      "baseline": {"label": "confirmed", "reason": "guess"}})
    live_db = Database(settings.db_path)
    live_db.new_run("live", PanelResult(run_id="live", status="running"))
    results = run_eval.main(["--split", "dev", "--data", str(data), "--out", str(tmp_path / "out"), "--baseline"],
                            settings=settings, gateway=gw, github=fake_github)

    assert results["strict"]["n"] == 3
    assert results["strict"]["accuracy_answered"] == 1.0 and results["strict"]["coverage"] == 1.0
    assert results["baseline"]["accuracy_all"] < 1.0
    assert (tmp_path / "out" / "report_dev.md").exists()
    assert (settings.data_dir / "evaluation.sqlite3").exists()
    assert live_db.get_run("live").status == "running"
    # Eval mode never fetches anything from GitHub: the frozen questions are the only input.
    assert fake_github.calls == []
    # Labels never appear in any prompt.
    for call in gw.calls:
        assert '"label"' not in call["user"]
