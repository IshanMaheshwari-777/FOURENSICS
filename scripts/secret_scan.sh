#!/usr/bin/env bash
# Fails if a file git would commit contains an API key or private key, or if a local secrets
# file is not git-ignored. Run from anywhere inside the repository.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PATTERN='gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{40,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'
status=0

# Tracked files plus new files that are not ignored: exactly what a commit could include.
hits=$( { git ls-files -z; git ls-files --others --exclude-standard -z; } \
  | xargs -0 grep -lE "$PATTERN" 2>/dev/null || true)
if [ -n "$hits" ]; then
  echo "✗ Possible secrets in files git would commit:"
  echo "$hits" | sed 's/^/    /'
  status=1
else
  echo "✓ no API keys or private keys in committable files"
fi

for f in backend/.env backend/.data backend/sandbox_allowlist.json; do
  if [ -e "$f" ] && ! git check-ignore -q "$f"; then
    echo "✗ $f exists but is not git-ignored"
    status=1
  fi
done
[ $status -eq 0 ] && echo "✓ local secrets and data are git-ignored"
exit $status
