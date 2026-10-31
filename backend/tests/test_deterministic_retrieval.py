"""Stable bounded retrieval avoids changing the evidence prompt on every restart."""
import json
import subprocess
import sys
from pathlib import Path

from app.pipeline.index import build_index
from app.pipeline.probes import _match_repo_path
from app.pipeline.search import search_text


def test_equal_specific_path_matches_remain_ambiguous(tmp_path):
    for directory in ("pkg1/src", "pkg2/src"):
        p = tmp_path / directory
        p.mkdir(parents=True)
        (p / "config.py").write_text("value = 1\n")
    assert _match_repo_path(build_index(tmp_path), "src/config.py") is None


def test_bounded_search_uses_stable_path_order(tmp_path):
    for name in ("z.py", "c.py", "a.py"):
        (tmp_path / name).write_text("value = 1\n")
    hits = search_text(tmp_path, "value", max_hits=2)
    assert [h.path for h in hits] == ["a.py", "c.py"]


def test_reference_evidence_is_stable_across_python_hash_seeds(tmp_path):
    for name in ("b", "a", "c"):
        p = tmp_path / name
        p.mkdir()
        (p / "util.ts").write_text("export const date = 1;\n")
    code = '''import sys
from pathlib import Path
from app.pipeline.index import build_index
from app.pipeline.probes import reference_probes
from app.pipeline.ledger import Ledger
from app.schemas import Anchor, Snapshot
idx = build_index(Path(sys.argv[1]))
ledger = Ledger("o", "r")
ledger.register_root("sha", Path(sys.argv[1]))
reference_probes(idx, [Anchor(kind="path", value="util.ts")], Snapshot(sha="sha"), ledger)
print(ledger.items[0].model_dump_json())
'''
    results = []
    for seed in ("1", "2", "3"):
        proc = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1], env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, check=True)
        results.append(json.loads(proc.stdout))
    assert results[0] == results[1] == results[2]
    assert results[0]["tier"] == "E4" and results[0]["path"] == "a/util.ts"
