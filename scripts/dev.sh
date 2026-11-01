#!/usr/bin/env bash
# Local development helper. Run from the repo root.
#
#   scripts/dev.sh setup     create the backend venv and install the extension
#   scripts/dev.sh test      run backend tests and the extension typecheck
#   scripts/dev.sh check     run EVERY guard: secrets, backend tests (incl. golden, invariants, budgets),
#                            extension typecheck, panel tests, build, bundle/manifest budgets
#   scripts/dev.sh backend   start the API on http://localhost:8787
#   scripts/dev.sh ext       build the extension into extension/dist (load it unpacked in Chrome)
#   scripts/dev.sh site     build the download page and extension zip into build/site
#   scripts/dev.sh smee URL  forward GitHub App webhooks from a smee.io channel to the local API
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  setup)
    python3 -m venv backend/.venv
    backend/.venv/bin/pip install -q --upgrade pip
    backend/.venv/bin/pip install -q -e "backend[dev]"
    (cd extension && npm install --no-audit --no-fund)
    [ -f backend/.env ] || cp backend/.env.example backend/.env
    echo "Done. Edit backend/.env, then: scripts/dev.sh test"
    ;;
  test)
    (cd backend && .venv/bin/python -m pytest -q -p no:warnings)
    (cd extension && npm run typecheck)
    ;;
  check)
    # Run before and after every change. Stops at the first failing guard.
    step() { printf '\n== %s\n' "$1"; }
    step "secret scan";            scripts/secret_scan.sh
    step "backend tests";          (cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:warnings -p no:cacheprovider)
    step "extension typecheck";    (cd extension && npm run --silent typecheck)
    step "panel tests";            (cd extension && npm test --silent)
    step "extension build";        (cd extension && npm run --silent build >/dev/null && echo "built extension/dist")
    step "bundle + manifest";      (cd extension && npm run --silent check:bundle)
    step "download site";          python3 scripts/build_site.py --no-build
    printf '\nAll checks passed.\n'
    ;;
  backend)
    cd backend && exec .venv/bin/uvicorn app.main:app --port 8787 --reload
    ;;
  ext)
    cd extension && npm run build && echo "Load extension/dist via chrome://extensions > Developer mode > Load unpacked"
    ;;
  site)
    python3 scripts/build_site.py && echo "Open build/site/index.html"
    ;;
  smee)
    [ -n "${2:-}" ] || { echo "usage: scripts/dev.sh smee https://smee.io/<channel>"; exit 1; }
    exec npx --yes smee-client --url "$2" --target http://localhost:8787/api/webhooks/github
    ;;
  *)
    sed -n '2,11p' "$0"
    ;;
esac
