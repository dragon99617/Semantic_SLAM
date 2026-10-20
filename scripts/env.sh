#!/usr/bin/env bash
# source this file; paths are derived from its location.
FAST_LIVO_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f /opt/ros/humble/setup.bash || ! -f "$FAST_LIVO_PROJECT_DIR/fast_livo2_ws/install/setup.bash" ]]; then
  echo 'ROS or workspace is missing. Complete scripts/01_install_system.sh and scripts/02_build.sh.' >&2
  return 1 2>/dev/null || exit 1
fi
source /opt/ros/humble/setup.bash
source "$FAST_LIVO_PROJECT_DIR/fast_livo2_ws/install/setup.bash"
export LD_LIBRARY_PATH="$FAST_LIVO_PROJECT_DIR/third_party/install/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CMAKE_PREFIX_PATH="$FAST_LIVO_PROJECT_DIR/third_party/install${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
