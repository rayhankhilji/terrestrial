#!/usr/bin/env bash
# Build the hosted (recorded) app and deploy it to Vercel as static files.
# The recording (web/public/rec, real captured server output) is deployed, never committed.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f public/rec/meta.json ]; then
  echo "No recording in web/public/rec: run 'uv run python -m live.capture' with the live server up." >&2
  exit 1
fi
VITE_RECORDED=1 npm run build
rm -rf .vercel/output
mkdir -p .vercel/output
cp -R dist .vercel/output/static
cat > .vercel/output/config.json <<'JSON'
{ "version": 3, "routes": [ { "src": "/rec/(.*)", "headers": { "cache-control": "public, max-age=300" } } ] }
JSON
vercel deploy --prebuilt --prod --yes "$@"
