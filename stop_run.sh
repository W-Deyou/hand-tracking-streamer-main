#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="${ROOT_DIR}/hand-tracking-sdk-main"
SERVICE_PORTS=(8000 8765)

DRY_RUN=false
QUIET=false
for argument in "$@"; do
  case "${argument}" in
    --dry-run) DRY_RUN=true ;;
    --quiet) QUIET=true ;;
    *)
      echo "Usage: $0 [--dry-run] [--quiet]" >&2
      exit 2
      ;;
  esac
done

verify_ports_released() {
  local listeners
  local port
  local ports_in_use
  local release_failed=false

  for _ in {1..20}; do
    ports_in_use=false
    for port in "${SERVICE_PORTS[@]}"; do
      if [[ -n "$(ss -H -ltn "sport = :${port}" 2>/dev/null || true)" ]]; then
        ports_in_use=true
        break
      fi
    done
    [[ "${ports_in_use}" == true ]] || break
    sleep 0.1
  done

  for port in "${SERVICE_PORTS[@]}"; do
    listeners="$(ss -H -ltnp "sport = :${port}" 2>/dev/null || true)"
    if [[ -n "${listeners}" ]]; then
      echo "TCP port ${port} is still in use:" >&2
      echo "${listeners}" >&2
      release_failed=true
    fi
  done

  [[ "${release_failed}" != true ]]
}

declare -A WORKSPACE_GROUPS=()
SELF_PGID="$(ps -o pgid= -p "$$" | tr -d '[:space:]')"

for process_dir in /proc/[0-9]*; do
  pid="${process_dir##*/}"
  [[ -r "${process_dir}/cmdline" ]] || continue

  command_line="$(tr '\0' ' ' < "${process_dir}/cmdline" 2>/dev/null || true)"
  [[ -n "${command_line}" ]] || continue
  label=""

  if [[ "${command_line}" == *"${SDK_DIR}/examples/video/"*"_video_host.py"* ]]; then
    label="video host"
  elif [[ "${command_line}" == *"${ROOT_DIR}/ros-ws/install/hand_tracking_sdk_ros2/"* ]]; then
    label="ROS hand tracking"
  elif [[ "${command_line}" == *"${ROOT_DIR}/ros-ws/install/"*"hand_tracking.rviz"* ]]; then
    label="RViz"
  else
    process_cwd="$(readlink -f "${process_dir}/cwd" 2>/dev/null || true)"
    if [[ "${process_cwd}" == "${ROOT_DIR}" ]] &&
      [[ "${command_line}" == *"ros2 launch hand_tracking_sdk_ros2 view_hands.launch.py"* ]]; then
      label="ROS launch"
    fi
  fi

  [[ -n "${label}" ]] || continue
  pgid="$(ps -o pgid= -p "${pid}" 2>/dev/null | tr -d '[:space:]')"
  [[ "${pgid}" =~ ^[0-9]+$ ]] || continue
  [[ "${pgid}" != "${SELF_PGID}" ]] || continue
  WORKSPACE_GROUPS["${pgid}"]="${label}"
done

if ((${#WORKSPACE_GROUPS[@]} == 0)); then
  if ! verify_ports_released; then
    exit 1
  fi
  if [[ "${QUIET}" != true ]]; then
    echo "No hand-tracking stream services are running; TCP ports 8000 and 8765 are free."
  fi
  exit 0
fi

mapfile -t GROUP_IDS < <(printf '%s\n' "${!WORKSPACE_GROUPS[@]}" | sort -n)

for pgid in "${GROUP_IDS[@]}"; do
  if [[ "${QUIET}" != true ]]; then
    echo "Stopping ${WORKSPACE_GROUPS[${pgid}]} process group ${pgid}..."
  fi
  if [[ "${DRY_RUN}" != true ]]; then
    kill -TERM -- "-${pgid}" 2>/dev/null || true
  fi
done

if [[ "${DRY_RUN}" == true ]]; then
  exit 0
fi

for _ in {1..30}; do
  any_running=false
  for pgid in "${GROUP_IDS[@]}"; do
    if kill -0 -- "-${pgid}" 2>/dev/null; then
      any_running=true
      break
    fi
  done
  [[ "${any_running}" == true ]] || break
  sleep 0.1
done

for pgid in "${GROUP_IDS[@]}"; do
  if kill -0 -- "-${pgid}" 2>/dev/null; then
    if [[ "${QUIET}" != true ]]; then
      echo "Force-stopping process group ${pgid}..." >&2
    fi
    kill -KILL -- "-${pgid}" 2>/dev/null || true
  fi
done

if ! verify_ports_released; then
  exit 1
fi

if [[ "${QUIET}" != true ]]; then
  echo "Hand-tracking stream services stopped; TCP ports 8000 and 8765 are free."
fi
