#!/usr/bin/env bash
# Run in your own WSL terminal; sudo asks for the Linux password there.
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$PROJECT_DIR/experiments/logs" "$PROJECT_DIR/third_party/downloads"
LOG_PATH="$PROJECT_DIR/experiments/logs/01_install_system_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_PATH") 2>&1
trap 'rc=$?; printf "Installation failed at line %s (exit %s). Log: %s\n" "$LINENO" "$rc" "$LOG_PATH"; exit "$rc"' ERR

if [[ "$EUID" -eq 0 ]]; then
  echo 'Run this script as hxy, without putting sudo before bash.'
  exit 1
fi
source /etc/os-release
if [[ "${ID}" != ubuntu || "${VERSION_ID}" != 22.04 ]]; then
  echo 'This script is intended for Ubuntu 22.04 / ROS 2 Humble.'
  exit 1
fi
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
echo "Installing ROS 2 Humble and build dependencies. Log: $LOG_PATH"
sudo -v
sudo apt-get update
# ROS documentation requires current systemd/udev packages on fresh Jammy.
sudo apt-get install -y --only-upgrade systemd systemd-sysv libudev1 udev
sudo apt-get install -y ca-certificates curl gnupg git build-essential cmake \
  pkg-config python3-pip python3-venv python3-dev

ROS_SOURCE_DEB="$PROJECT_DIR/third_party/downloads/ros2-apt-source_1.3.0.jammy_all.deb"
if ! dpkg-query -W -f='${Status}' ros2-apt-source 2>/dev/null | grep -q 'install ok installed'; then
  curl --fail --location --retry 3 \
    https://github.com/ros-infrastructure/ros-apt-source/releases/download/1.3.0/ros2-apt-source_1.3.0.jammy_all.deb \
    --output "$ROS_SOURCE_DEB"
  dpkg-deb --info "$ROS_SOURCE_DEB"
  sha256sum "$ROS_SOURCE_DEB"
  sudo dpkg -i "$ROS_SOURCE_DEB"
fi
sudo apt-get update
sudo apt-get install -y \
  ros-humble-desktop \
  python3-colcon-common-extensions python3-rosdep python3-vcstool \
  ros-humble-ament-cmake-auto ros-humble-rosidl-default-generators \
  ros-humble-cv-bridge ros-humble-image-transport \
  ros-humble-image-transport-plugins ros-humble-compressed-image-transport \
  ros-humble-pcl-ros ros-humble-pcl-conversions ros-humble-tf2-geometry-msgs \
  ros-humble-rosbag2-storage-default-plugins \
  libeigen3-dev libpcl-dev libopencv-dev libfmt-dev libboost-thread-dev \
  libgoogle-glog-dev libgtest-dev libusb-1.0-0-dev libapr1-dev libyaml-cpp-dev

if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
  sudo rosdep init
fi
# rosdep update is intentionally run as the ordinary user.
rosdep update --rosdistro humble
set +u
source /opt/ros/humble/setup.bash
set -u
ros2 pkg prefix rclcpp
command -v colcon
dpkg-query -W ros-humble-desktop cmake build-essential libeigen3-dev libpcl-dev libopencv-dev
touch "$PROJECT_DIR/experiments/system_dependencies_installed"
echo 'System dependencies installed. Return to Codex to continue the build.'
