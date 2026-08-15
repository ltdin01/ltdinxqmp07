#!/usr/bin/env bash
# Verification loop for the laptopdeals pipeline.
# Usage: verify_all.sh [--golden-diff] [--quality <datafile>] [--quality-baseline <report>]
#
# Steps:
#   1. py_compile every pipeline python script
#   2. unittest suite (pipeline/tests)
#   3. YAML validation of pipeline workflows
#   4. --golden-diff: re-run format_catalog on the frozen golden inputs and
#      assert byte-identical output vs the captured snapshot
#   5. --quality: run the data-quality gate on a catalog file
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIPELINE="$ROOT/pipeline"
GOLDEN="${GOLDEN_DIR:-/tmp/opencode/golden}"
FAILURES=0

if command -v "${PYTHON:-}" >/dev/null 2>&1; then
  PY="${PYTHON}"
elif [ -x "$ROOT/venv/bin/python" ]; then
  PY="$ROOT/venv/bin/python"
else
  PY="python3"
fi

GOLDEN_DIFF=0
QUALITY_INPUTS=()
QUALITY_BASELINES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --golden-diff) GOLDEN_DIFF=1 ;;
    --quality) shift; QUALITY_INPUTS+=("${1:-}") ;;
    --quality-baseline) shift; QUALITY_BASELINES+=("${1:-}") ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$1"; FAILURES=$((FAILURES + 1)); }
ok() { printf '\033[32mok: %s\033[0m\n' "$1"; }

step "1. py_compile"
shopt -s nullglob
PY_COMPILE_TARGETS=(
  "$PIPELINE"/scripts/*.py
  "$PIPELINE"/scripts/laptopdeals/*.py
  "$PIPELINE"/scripts/laptopdeals/sources/*.py
  "$PIPELINE"/scripts/laptopdeals/providers/*.py
  "$PIPELINE"/tests/*.py
)
"$PY" -m py_compile "${PY_COMPILE_TARGETS[@]}" \
  && ok "py_compile" || fail "py_compile"

step "2. unittest suite"
(cd "$PIPELINE" && "$PY" -m unittest discover -s tests >/dev/null 2>&1) \
  && ok "unittest" || fail "unittest"

step "3. workflow YAML validation"
for wf in "$PIPELINE"/.github/workflows/*.yml; do
  "$PY" -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1])); print("ok:", sys.argv[1])' "$wf" \
    || fail "yaml: $wf"
done

if [ "$GOLDEN_DIFF" -eq 1 ]; then
  step "4. golden diff (frozen inputs -> snapshot)"
  if [ -f "$GOLDEN/input/lenovo-catalog.json" ] && [ -f "$GOLDEN/expected/lenovo-data.json" ]; then
    TMP_OUT="$(mktemp "${GOLDEN}-verify-XXXXXX.json")"
    if "$PY" "$PIPELINE/scripts/catalog.py" format \
      --input "$GOLDEN/input/lenovo-catalog.json" \
      --output "$TMP_OUT" \
      --existing-data "$GOLDEN/existing/data.json" \
      --history-dir "$GOLDEN/history" \
      --cto-dir "$GOLDEN/cto" \
      --psref-map "$GOLDEN/psref/final_sku_specs.json" >/dev/null 2>&1 \
      && cmp -s "$TMP_OUT" "$GOLDEN/expected/lenovo-data.json"; then
      ok "golden diff: byte-identical"
    else
      fail "golden diff: output differs from snapshot"
    fi
    rm -f "$TMP_OUT"
  else
    fail "golden bundle missing (set GOLDEN_DIR or run capture first)"
  fi
fi

if [ "${#QUALITY_INPUTS[@]}" -gt 0 ]; then
  step "5. quality gate"
  idx=0
  for QUALITY_INPUT in "${QUALITY_INPUTS[@]}"; do
    Q_ARGS=("--input" "$QUALITY_INPUT")
    if [ -n "${QUALITY_BASELINES[$idx]:-}" ]; then
      Q_ARGS+=(--baseline "${QUALITY_BASELINES[$idx]}")
    fi
    "$PY" "$PIPELINE/scripts/quality.py" "${Q_ARGS[@]}" && ok "quality $QUALITY_INPUT" || fail "quality $QUALITY_INPUT"
    idx=$((idx + 1))
  done
fi

step "result"
if [ "$FAILURES" -eq 0 ]; then
  printf 'ALL CHECKS PASSED\n'
else
  printf '%d CHECK(S) FAILED\n' "$FAILURES"
  exit 1
fi
