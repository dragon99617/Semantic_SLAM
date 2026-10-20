#!/usr/bin/env bash
# Builds into project-local directories; requires 01_install_system.sh first.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_DIR="$PROJECT_DIR/fast_livo2_ws"
DEPS_PREFIX="$PROJECT_DIR/third_party/install"
mkdir -p "$PROJECT_DIR/experiments/logs" "$DEPS_PREFIX"
LOG_PATH="$PROJECT_DIR/experiments/logs/02_build_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_PATH") 2>&1
trap 'rc=$?; printf "Build failed at line %s (exit %s). Log: %s\n" "$LINENO" "$rc" "$LOG_PATH"; exit "$rc"' ERR
if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo 'ROS 2 Humble is missing. Run scripts/01_install_system.sh first.'
  exit 1
fi
set +u
source /opt/ros/humble/setup.bash
set -u
export MAKEFLAGS='-j2 -l2'
export CMAKE_BUILD_PARALLEL_LEVEL=2
export CMAKE_PREFIX_PATH="$DEPS_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export LD_LIBRARY_PATH="$DEPS_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

cmake -S "$PROJECT_DIR/third_party/Sophus" \
  -B "$PROJECT_DIR/third_party/build/Sophus" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$DEPS_PREFIX" \
  -DBUILD_SOPHUS_TESTS=OFF -DBUILD_SOPHUS_EXAMPLES=OFF \
  -DCMAKE_EXPORT_NO_PACKAGE_REGISTRY=ON
cmake --build "$PROJECT_DIR/third_party/build/Sophus" --parallel 2
cmake --install "$PROJECT_DIR/third_party/build/Sophus"

cmake -S "$PROJECT_DIR/third_party/Livox-SDK2" \
  -B "$PROJECT_DIR/third_party/build/Livox-SDK2" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$DEPS_PREFIX"
cmake --build "$PROJECT_DIR/third_party/build/Livox-SDK2" --parallel 2
cmake --install "$PROJECT_DIR/third_party/build/Livox-SDK2"

# The upstream build.sh deletes the whole workspace's build/install folders.
# Select its ROS 2 manifest directly, then build normally without deleting data.
if ! cmp -s "$WORKSPACE_DIR/src/livox_ros_driver2/package_ROS2.xml" \
    "$WORKSPACE_DIR/src/livox_ros_driver2/package.xml"; then
  cp "$WORKSPACE_DIR/src/livox_ros_driver2/package_ROS2.xml" \
    "$WORKSPACE_DIR/src/livox_ros_driver2/package.xml"
fi
cd "$WORKSPACE_DIR"
colcon build --symlink-install --executor sequential \
  --packages-up-to fast_livo \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
    -DROS_EDITION=ROS2 -DDISTRO_ROS=humble \
    -DCMAKE_PREFIX_PATH="$DEPS_PREFIX;/opt/ros/humble"

set +u
source "$WORKSPACE_DIR/install/setup.bash"
set -u
ros2 pkg executables fast_livo
echo 'Build completed. Use scripts/03_run.sh for the next stage.'
