#!/usr/bin/env bash
# Run before every push. Mirrors CI exactly, so a green preflight means a green CI.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== workflow YAML parses and has expected jobs"
python3 - <<'PY'
import yaml
d = yaml.safe_load(open(".github/workflows/ci.yml"))
assert set(d["jobs"]) >= {"test", "postgres", "contracts"}, d["jobs"].keys()
PY
echo "== plain pytest (as CI runs it, not python -m pytest)"
pytest -q -p no:logging
if [ -n "${AGENTPAY_TEST_POSTGRES_DSN:-}" ]; then echo "== postgres suite"; pytest -q -p no:logging; fi
echo "== demo"
agentpay demo >/dev/null
if [ -n "${SOLC_PATH:-}" ]; then
  echo "== contract artifacts reproducible"
  node contracts/build.mjs >/dev/null && git diff --exit-code contracts/artifacts
fi
if [ -d web ]; then
  echo "== web: html/js syntax"
  node --check web/app.js
  node --input-type=module --check < web/tunnel.js
  python3 -c "import json;json.load(open('web/trace.json'))"
  test -s web/vendor/three.module.min.js
fi
echo "== vercel config + serverless function import"
python3 -c "import json; c=json.load(open('vercel.json')); assert c['outputDirectory']=='web'"
python3 -c "import api.run"
echo "PREFLIGHT OK"
