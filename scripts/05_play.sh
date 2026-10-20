#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
set +u
source "$PROJECT_DIR/scripts/env.sh"
set -u
BAG_DIR="$PROJECT_DIR/datasets/Retail_Street_humble"
PLAY_RATE="${PLAY_RATE:-0.5}"
mkdir -p "$PROJECT_DIR/experiments/logs"
LOG_PATH="$PROJECT_DIR/experiments/logs/05_play_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_PATH") 2>&1
ros2 bag info "$BAG_DIR"
ros2 bag play "$BAG_DIR" --rate "$PLAY_RATE"
echo 'Playback finished. Wait for processing to drain before stopping the mapper with Ctrl+C to save PCD.'
