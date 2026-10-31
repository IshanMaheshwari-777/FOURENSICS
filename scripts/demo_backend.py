"""Local cache-only demonstration API. Uses port 8790, never the user's port 8787.

Real public GitHub reads and allowlisted Docker reproduction remain enabled.
Live inference cannot be called, and the normal server's .env is never edited.
"""
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

import uvicorn

from app.config import get_settings
from app.llm.gateway import CachedGateway
from app.main import Services, create_app
from app.pipeline.snapshot import RepoCache
from storage import isolated_db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    original = get_settings()
    settings = original.model_copy(update={
        "data_dir": original.data_dir / "demo_server",
        "enable_sandbox": True,
        "sandbox_allowlist": ROOT / "backend" / "sandbox_allowlist.example.json",
        "run_timeout_s": 600,
    })
    gateway = CachedGateway(original.llm_base_url, original.llm_model, db=isolated_db(original, "demo"),
                            prompt_budget_tokens=4400)
    services = Services(settings, gateway=gateway)
    services.repos = RepoCache(original.cache_dir, original.git_timeout_s)
    uvicorn.run(create_app(services), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
