#!/usr/bin/env bash
# Starts the mapper. Start the bag player separately after initialization.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
set +u
source "$PROJECT_DIR/scripts/env.sh"
set -u
RUN_RVIZ="${RUN_RVIZ:-False}"
RUN_CONFIG="${RUN_CONFIG:-$PROJECT_DIR/experiments/config/retail_street.yaml}"
mkdir -p "$PROJECT_DIR/experiments/logs"
LOG_PATH="$PROJECT_DIR/experiments/logs/03_mapping_$(date +%Y%m%d_%H%M%S).log"
ros2 launch fast_livo mapping_aviz.launch.py \
  use_rviz:="$RUN_RVIZ" use_compressed_image:=False \
  avia_params_file:="$RUN_CONFIG" 2>&1 | tee "$LOG_PATH"
