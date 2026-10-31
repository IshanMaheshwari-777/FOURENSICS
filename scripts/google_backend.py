"""Isolated live Google/Gemma API check service; never binds to the user's 8787."""
import sys
import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import uvicorn
from app.config import get_settings
from app.main import Services, create_app
from app.pipeline.snapshot import RepoCache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--cached-only", action="store_true", help="Replay live-check responses without new inference")
    args = parser.parse_args()
    original = get_settings()
    settings = original.model_copy(update={"data_dir": original.data_dir / ("google_replay" if args.cached_only else "google_live"),
        "llm_provider": "google", "run_timeout_s": 600,
        "llm_cache_only": args.cached_only or original.llm_cache_only})
    services = Services(settings)
    if args.cached_only:
        source_path = original.data_dir / "google_live" / "triage.sqlite3"
        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
            with services.db._conn() as connection:
                connection.executemany("INSERT OR IGNORE INTO llm_cache VALUES (?,?,?)",
                    source.execute("SELECT prompt_hash,response_text,created_at FROM llm_cache"))
    services.repos = RepoCache(original.cache_dir, original.git_timeout_s)
    uvicorn.run(create_app(services), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
