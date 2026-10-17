#!/usr/bin/env bash
# Build the static site for deployment.
#
# Deliberately an allowlist into a clean directory rather than a .vercelignore denylist:
# the deploy CLI uploads from the working directory, not from git, and .env lives in the
# repo root. Copying only what the site needs makes uploading a secret impossible rather
# than merely unlikely.
set -euo pipefail
rm -rf site && mkdir -p site/viewer site/runs

cp viewer/*.html site/viewer/
cp runs/index.json site/runs/
cp -R runs/demo-final site/runs/demo-final
find site -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find site -name '*.pyc' -delete 2>/dev/null || true

# the pages fetch ../runs/..., so the structure above must be preserved and the root needs
# somewhere to send a visitor
cat > site/index.html <<'HTML'
<!doctype html>
<meta charset="utf-8">
<title>done — the recursive verifier court</title>
<meta http-equiv="refresh" content="0; url=/viewer/">
<link rel="canonical" href="/viewer/">
<body style="font:15px -apple-system,Helvetica,Arial,sans-serif;background:#F4F4F0;padding:40px">
<p>Redirecting to <a href="/viewer/">the run gallery</a>…</p>
HTML

echo "site/ built:"
find site -type f | sed 's/^/  /' | head -20
echo "  ($(find site -type f | wc -l | tr -d ' ') files, $(du -sh site | cut -f1))"

# refuse to ship if anything secret-shaped made it in
if find site -name ".env*" -o -name "*.key" -o -name "*.pem" | grep -q .; then
  echo "REFUSING: secret-shaped file in site/" >&2; exit 1
fi
if grep -rlI "CRUSOE_API_KEY=[A-Za-z0-9]" site 2>/dev/null | grep -q .; then
  echo "REFUSING: a populated API key is present in site/" >&2; exit 1
fi
echo "  secret scan: clean"
