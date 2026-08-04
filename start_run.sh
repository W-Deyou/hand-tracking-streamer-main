#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="${ROOT_DIR}/hand-tracking-sdk-main"
ROS_SETUP="${ROOT_DIR}/ros-ws/install/setup.bash"
SDK_PYTHON="${SDK_DIR}/.venv/bin/python"
VIDEO_HOST="${SDK_DIR}/examples/video/orbbec_gemini_video_host.py"
MOCAP_PORT=8000
VIDEO_SIGNALING_PORT=8765
QUEST_SETTINGS_PACKAGE="com.oculus.panelapp.settings"

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
  local attempt

  if [[ -z "${pid}" ]]; then
    return
  fi

  # Check the process group, not only its leader: ros2 launch can exit before
  # bridge/RViz, leaving those children alive under the same PGID.
  if kill -0 -- "-${pid}" 2>/dev/null; then
    kill -TERM -- "-${pid}" 2>/dev/null || true
    for attempt in {1..30}; do
      if ! kill -0 -- "-${pid}" 2>/dev/null; then
        return
      fi
      sleep 0.1
    done
    kill -KILL -- "-${pid}" 2>/dev/null || true
  elif kill -0 "${pid}" 2>/dev/null; then
    kill -TERM "${pid}" 2>/dev/null || true
  fi
}

assert_port_available() {
  local label="$1"
  local port="$2"
  local listeners

  listeners="$(ss -H -ltnp "sport = :${port}" 2>/dev/null || true)"
  if [[ -n "${listeners}" ]]; then
    echo "Cannot start ${label}: TCP port ${port} is already in use:" >&2
    echo "${listeners}" >&2
    echo "Stop the existing process, then run ./start_run.sh again." >&2
    return 1
  fi
}

stop_quest_wifi_scanner() {
  if ! command -v adb >/dev/null 2>&1; then
    return
  fi
  if [[ "$(adb get-state 2>/dev/null || true)" != "device" ]]; then
    return
  fi

  # The Quest settings panel scans Wi-Fi every 10 seconds even in the
  # background, producing repeatable 50-150 ms latency spikes.
  if adb shell am force-stop "${QUEST_SETTINGS_PACKAGE}" >/dev/null 2>&1; then
    echo "Stopped Quest settings Wi-Fi scanner for low-latency streaming."
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
if ! command -v ss >/dev/null 2>&1; then
  echo "ss was not found." >&2
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

# Fail before spawning either service, so a stale process cannot leave a
# partially started chain behind.
assert_port_available "ROS 2 hand tracking" "${MOCAP_PORT}"
assert_port_available "WebRTC signaling" "${VIDEO_SIGNALING_PORT}"
stop_quest_wifi_scanner

echo "Starting ROS 2 hand tracking and RViz..."
(
  cd "${ROOT_DIR}"
  exec setsid ros2 launch hand_tracking_sdk_ros2 view_hands.launch.py
) &
ROS_PID=$!

echo "Starting Orbbec Gemini 336 video (1080p30, NVENC H.264, /dev/video6, CPUs 0-15)..."
(
  cd "${SDK_DIR}"
  exec setsid taskset -c 0-15 \
    "${SDK_PYTHON}" "${VIDEO_HOST}" \
    --webcam-index 6 \
    --preset 1080p \
    --encoder nvenc \
    --nvenc-preset p1 \
    --video-bitrate-mbps 10 \
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
