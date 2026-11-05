#!/usr/bin/env bash
set -euo pipefail

OPTS=/data/options.json
VENV=/data/venv
opt() { python3 -c "import json,sys;print(json.load(open('$OPTS')).get(sys.argv[1],''))" "$1"; }

REQ_VERSION="$(opt emhass_version)"
PROD_SLUG="$(opt production_addon_slug)"
export LAB_LOG_LEVEL="$(opt log_level)"

echo "[lab] pornesc EMHASS Perfect World 0.3.1"

# 1. Versiunea țintă = versiunea EMHASS de producție (Supervisor API) sau cea fixată manual.
if [ "$REQ_VERSION" = "auto" ] || [ -z "$REQ_VERSION" ]; then
  TARGET="$(curl -fsS -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
      "http://supervisor/addons/${PROD_SLUG}/info" \
      | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['version'])")"
  echo "[lab] versiune producție (${PROD_SLUG}): ${TARGET}"
else
  TARGET="$REQ_VERSION"
  echo "[lab] versiune fixată manual: ${TARGET}"
fi

TARGET="${TARGET#v}"   # add-on-ul de producție raportează "v0.18.8"; PyPI/importlib folosesc "0.18.8"

# 2. Venv persistent cu exact acea versiune.
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
INSTALLED="$("$VENV/bin/python" -c 'from importlib.metadata import version;print(version("emhass"))' 2>/dev/null || echo none)"
if [ "$INSTALLED" != "$TARGET" ]; then
  echo "[lab] instalez emhass==${TARGET} (era: ${INSTALLED}); durează la prima pornire"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install "emhass==${TARGET}"
fi
echo "$TARGET" > /data/emhass_version.txt

# 3. Runner-ul laboratorului (pasul 3-4 din handoff: arhivă sloturi + soluții perfecte).
exec "$VENV/bin/python" -m lab.runner
