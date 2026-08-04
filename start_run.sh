#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="${ROOT_DIR}/hand-tracking-sdk-main"
ROS_SETUP="${ROOT_DIR}/ros-ws/install/setup.bash"
SDK_PYTHON="${SDK_DIR}/.venv/bin/python"
VIDEO_HOST="${SDK_DIR}/examples/video/orbbec_gemini_video_host.py"

ROS_PID=""
VIDEO_PID=""

source_setup() {
  local setup_file="$1"
  set +u
  # ROS setup scripts may reference optional variables that are not defined yet.
  source "${setup_file}"
  set -u
}

terminate_group() {
  local pid="$1"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
  fi
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM

  terminate_group "${VIDEO_PID}"
  terminate_group "${ROS_PID}"

  [[ -z "${VIDEO_PID}" ]] || wait "${VIDEO_PID}" 2>/dev/null || true
  [[ -z "${ROS_PID}" ]] || wait "${ROS_PID}" 2>/dev/null || true
  exit "${status}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -f /opt/ros/humble/setup.bash ]]; then
  source_setup /opt/ros/humble/setup.bash
fi

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "Missing ROS workspace setup: ${ROS_SETUP}" >&2
  echo "Build the ROS 2 workspace before running this script." >&2
  exit 1
fi
source_setup "${ROS_SETUP}"

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 was not found after loading the workspace." >&2
  exit 1
fi
if ! command -v taskset >/dev/null 2>&1; then
  echo "taskset was not found." >&2
  exit 1
fi
if ! command -v setsid >/dev/null 2>&1; then
  echo "setsid was not found." >&2
  exit 1
fi
if [[ ! -x "${SDK_PYTHON}" ]]; then
  echo "Missing SDK virtualenv Python: ${SDK_PYTHON}" >&2
  exit 1
fi
if [[ ! -f "${VIDEO_HOST}" ]]; then
  echo "Missing Orbbec video host: ${VIDEO_HOST}" >&2
  exit 1
fi

echo "Starting ROS 2 hand tracking and RViz..."
(
  cd "${ROOT_DIR}"
  exec setsid ros2 launch hand_tracking_sdk_ros2 view_hands.launch.py
) &
ROS_PID=$!

echo "Starting Orbbec Gemini 336 video (1080p30, /dev/video6, CPUs 0-15)..."
(
  cd "${SDK_DIR}"
  exec setsid taskset -c 0-15 \
    "${SDK_PYTHON}" examples/video/orbbec_gemini_video_host.py \
    --webcam-index 6 \
    --preset 1080p \
    --verbose \
    --disable-mocap-tcp
) &
VIDEO_PID=$!

echo "Both services started. Press Ctrl+C to stop both."

set +e
wait -n "${ROS_PID}" "${VIDEO_PID}"
STATUS=$?
set -e

if kill -0 "${ROS_PID}" 2>/dev/null && kill -0 "${VIDEO_PID}" 2>/dev/null; then
  echo "A service stopped unexpectedly; shutting down both." >&2
fi
exit "${STATUS}"
