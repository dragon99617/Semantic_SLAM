#!/usr/bin/env bash
# Sequential bounded capture -> validated projection -> OVO -> saved-map check.
set -eo pipefail
if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 DESCRIPTOR.json NEW_OUTPUT_DIRECTORY [SECONDS=16; 0=full]" >&2
  exit 2
fi
LIVO_OVO_PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LIVO_OVO_PROJECT"
LIVO_OVO_DESCRIPTOR="$(realpath "$1")"
LIVO_OVO_OUTPUT="$(realpath -m "$2")"
LIVO_OVO_SECONDS="${3:-16}"
if [[ -e "$LIVO_OVO_OUTPUT" ]]; then
  echo "Choose a new output directory: $LIVO_OVO_OUTPUT" >&2
  exit 1
fi
mkdir -p "$LIVO_OVO_OUTPUT"
source scripts/env.sh
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-86}"
export ROS_LOG_DIR="$LIVO_OVO_OUTPUT/ros_logs"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
python3 scripts/verify_phase1_handoff.py --output "$LIVO_OVO_OUTPUT/phase1_recheck.json"
python3 -u scripts/capture_livo_ovo.py "$LIVO_OVO_DESCRIPTOR" --output "$LIVO_OVO_OUTPUT/capture" --seconds "$LIVO_OVO_SECONDS" --rate .5
python3 -u scripts/prepare_livo_ovo.py "$LIVO_OVO_OUTPUT/capture" --output "$LIVO_OVO_OUTPUT/input"
third_party/semantic_venv/bin/python -u scripts/run_livo_ovo.py "$LIVO_OVO_OUTPUT/input" --output "$LIVO_OVO_OUTPUT/ovo" --depth-policy valid_mask --mapping-policy valid_mask --geometry-precision float32
third_party/semantic_venv/bin/python scripts/inspect_livo_ovo.py "$LIVO_OVO_OUTPUT/ovo"
