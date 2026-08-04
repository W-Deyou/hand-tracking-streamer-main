#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="${ROOT_DIR}/hand-tracking-sdk-main"
ROS_SETUP="${ROOT_DIR}/ros-ws/install/setup.bash"
SDK_PYTHON="${SDK_DIR}/.venv/bin/python"
RGB_CAMERA_DEVICE="${CAMERA_DEVICE:-/dev/v4l/by-id/usb-RYS_RGB_RGB_Camera_200901010001-video-index0}"
ORBBEC_INDEX="${ORBBEC_INDEX:--1}"
MOCAP_PORT=8000
VIDEO_SIGNALING_PORT=8765
QUEST_SETTINGS_PACKAGE="com.oculus.panelapp.settings"

CAMERA_PROFILE="${1:-auto}"
VIDEO_HOST=""
CAMERA_LABEL=""
CAMERA_DEVICE=""
VIDEO_SOURCE_ARGS=()
ROS_PID=""
VIDEO_PID=""

usage() {
  echo "Usage: $0 [auto|rgb|orbbec]"
  echo "  auto    Use any connected camera; prefer Orbbec when both are online (default)."
  echo "  rgb     Use the RYS RGB camera stable by-id capture path."
  echo "  orbbec  Auto-discover the Orbbec Gemini 336 RGB node."
}

if (($# > 1)); then
  usage >&2
  exit 2
fi
if [[ "${CAMERA_PROFILE}" == "-h" || "${CAMERA_PROFILE}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "${CAMERA_PROFILE}" != "auto" && "${CAMERA_PROFILE}" != "rgb" &&
  "${CAMERA_PROFILE}" != "orbbec" ]]; then
  echo "Unknown camera profile: ${CAMERA_PROFILE}" >&2
  usage >&2
  exit 2
fi

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

is_orbbec_rgb_device() {
  local device="$1"
  local formats
  local info

  [[ -e "${device}" ]] || return 1
  info="$(v4l2-ctl -d "${device}" --info 2>/dev/null || true)"
  [[ "${info}" == *"Card type"*"Orbbec Gemini"* ]] || return 1
  formats="$(v4l2-ctl -d "${device}" --list-formats-ext 2>/dev/null || true)"
  [[ "${formats}" == *"'MJPG'"* ]]
}

find_orbbec_rgb_index() {
  local device

  for device in /dev/video*; do
    if is_orbbec_rgb_device "${device}"; then
      echo "${device#/dev/video}"
      return 0
    fi
  done
  return 1
}

orbbec_connected() {
  find_orbbec_rgb_index >/dev/null
}

select_camera() {
  if [[ "${CAMERA_PROFILE}" == "auto" ]]; then
    if orbbec_connected; then
      CAMERA_PROFILE="orbbec"
    elif [[ -e "${RGB_CAMERA_DEVICE}" ]]; then
      CAMERA_PROFILE="rgb"
    else
      echo "No supported RGB camera is connected." >&2
      echo "Expected ${RGB_CAMERA_DEVICE} or an Orbbec Gemini 336." >&2
      return 1
    fi
  fi

  if [[ "${CAMERA_PROFILE}" == "rgb" ]]; then
    VIDEO_HOST="${SDK_DIR}/examples/video/uvc_video_host.py"
    CAMERA_DEVICE="${RGB_CAMERA_DEVICE}"
    CAMERA_LABEL="RYS RGB camera (${CAMERA_DEVICE})"
    VIDEO_SOURCE_ARGS=(--video-device "${CAMERA_DEVICE}")
    return
  fi

  if ! orbbec_connected; then
    echo "Orbbec Gemini 336 is not connected." >&2
    return 1
  fi
  if [[ "${ORBBEC_INDEX}" == "-1" ]]; then
    ORBBEC_INDEX="$(find_orbbec_rgb_index)"
  elif ! is_orbbec_rgb_device "/dev/video${ORBBEC_INDEX}"; then
    echo "/dev/video${ORBBEC_INDEX} is not an Orbbec MJPEG RGB node." >&2
    return 1
  fi
  VIDEO_HOST="${SDK_DIR}/examples/video/orbbec_gemini_video_host.py"
  CAMERA_LABEL="Orbbec Gemini 336 (/dev/video${ORBBEC_INDEX})"
  VIDEO_SOURCE_ARGS=(--webcam-index "${ORBBEC_INDEX}")
}

configure_camera() {
  if [[ "${CAMERA_PROFILE}" != "rgb" ]]; then
    return
  fi
  if [[ ! -e "${CAMERA_DEVICE}" ]]; then
    echo "Missing UVC camera capture device: ${CAMERA_DEVICE}" >&2
    return 1
  fi

  # This camera defaults to dynamic exposure FPS and falls to about 20 FPS in
  # normal indoor light even after accepting a 30 FPS format request.
  if ! v4l2-ctl -d "${CAMERA_DEVICE}" \
    --set-ctrl=exposure_dynamic_framerate=0 >/dev/null; then
    echo "Failed to disable dynamic frame rate on ${CAMERA_DEVICE}" >&2
    return 1
  fi
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM

  terminate_group "${VIDEO_PID}"
  terminate_group "${ROS_PID}"

  # `setsid` may fork before exec, so the PID recorded by this shell can exit
  # before its actual ROS/video session. Clean only this workspace's groups.
  "${ROOT_DIR}/stop_run.sh" --quiet 2>/dev/null || true

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
if ! command -v v4l2-ctl >/dev/null 2>&1; then
  echo "v4l2-ctl was not found." >&2
  exit 1
fi
select_camera
if [[ ! -x "${SDK_PYTHON}" ]]; then
  echo "Missing SDK virtualenv Python: ${SDK_PYTHON}" >&2
  exit 1
fi
if [[ ! -f "${VIDEO_HOST}" ]]; then
  echo "Missing video host: ${VIDEO_HOST}" >&2
  exit 1
fi

# Fail before spawning either service, so a stale process cannot leave a
# partially started chain behind.
assert_port_available "ROS 2 hand tracking" "${MOCAP_PORT}"
assert_port_available "WebRTC signaling" "${VIDEO_SIGNALING_PORT}"
configure_camera
stop_quest_wifi_scanner

echo "Starting ROS 2 hand tracking and RViz..."
(
  cd "${ROOT_DIR}"
  exec setsid ros2 launch hand_tracking_sdk_ros2 view_hands.launch.py
) &
ROS_PID=$!

echo "Starting ${CAMERA_LABEL} (1080p30 MJPEG, NVENC H.264, CPUs 0-15)..."
(
  cd "${SDK_DIR}"
  exec setsid taskset -c 0-15 \
    "${SDK_PYTHON}" "${VIDEO_HOST}" \
    "${VIDEO_SOURCE_ARGS[@]}" \
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
