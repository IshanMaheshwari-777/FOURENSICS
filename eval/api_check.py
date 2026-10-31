"""Audit real API responses, with inference disabled for new assessments.

Existing --run-id reports can be read from any backend. --url requires a cache-only
backend unless --live is explicitly supplied. Results are JSON.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--out", type=Path, default=Path("eval/out/api_check.json"))
    parser.add_argument("--timeout", type=float, default=660)
    parser.add_argument("--live", action="store_true", help="Explicitly allow new model calls for the requested URLs")
    args = parser.parse_args()
    if not (args.url or args.run_id):
        parser.error("provide at least one --url or --run-id")
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        if args.url and not args.live and health.json().get("inference") != "cache-only":
            parser.error("--url requires cache-only mode or explicit --live; use --run-id for read-only inspection")
        ids = list(args.run_id)
        for url in args.url:
            response = client.get("/api/assessment", params={"url": url})
            response.raise_for_status()
            ids.append(response.json()["run_id"])
        reports = []
        deadline = time.monotonic() + args.timeout
        for run_id in ids:
            while True:
                response = client.get(f"/api/runs/{run_id}")
                response.raise_for_status()
                report = response.json()
                if report["status"] in ("complete", "partial", "error"):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"API run {run_id} did not finish within the audit budget")
                time.sleep(0.5)
            reports.append(report)
            print(json.dumps({"run_id": run_id, "url": (report.get("target") or {}).get("url"),
                              "status": report["status"], "summary": report["summary_line"],
                              "claims": len(report["claims"]), "evidence": len(report["evidence"]),
                              "blockers": [b["code"] for b in report.get("blockers", [])]}), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"health": health.json(), "reports": reports}, indent=2) + "\n")


if __name__ == "__main__":
    main()
