# Video Examples

Host-side scripts that receive Quest hand/head tracking data and stream video
back to the headset over the **existing WebRTC path** (WebSocket signaling
`:8765` + H.264). Telemetry (UDP/TCP) is separate and optional for non-sim hosts.

## Setup

```bash
pip install hand-tracking-sdk[video]       # WebRTC + signaling
pip install hand-tracking-sdk[video,sim]   # + MuJoCo sim hosts
# or from this package root:
uv sync --extra video
```

## Scripts

| Script | Source | Description |
|--------|--------|-------------|
| `test_pattern_video_host.py` | Test pattern | Synthetic colour bars — no hardware needed |
| `webcam_video_host.py` | USB webcam | Streams a local camera feed |
| `uvc_video_host.py` | UVC RGB camera | Low-latency MJPEG capture using a stable device path |
| `orbbec_gemini_video_host.py` | Orbbec Gemini UVC | Auto-picks **RGB** `/dev/videoN`, streams via WebRTC |
| `inspire_hand_video_host.py` | MuJoCo | Bimanual Inspire Hand with vector retargeting |
| `shadow_hand_video_host.py` | MuJoCo | Bimanual Shadow Hand E3M5 with vector retargeting |
| `aloha_video_host.py` | MuJoCo | ALOHA 2 bimanual arms with IK (requires `mink`) |

## Quick start

```bash
# Full ROS + RViz + video chain from the repository root:
./start_run.sh            # Auto: use an online camera; prefer Orbbec if both are online
./start_run.sh auto       # Same as above
./start_run.sh rgb        # Require the RYS RGB camera
./start_run.sh orbbec     # Require Orbbec and auto-discover its RGB node
./stop_run.sh             # Stop video, ROS 2 and RViz; release ports 8000/8765
./stop_run.sh --dry-run   # Preview matching processes without stopping them

# Simplest — no camera required:
uv run examples/video/test_pattern_video_host.py --verbose

# USB webcam:
uv run examples/video/webcam_video_host.py --webcam-index 0 --preset 720p

# Low-latency UVC RGB camera (stable path survives /dev/videoN renumbering):
.venv/bin/python examples/video/uvc_video_host.py \
  --video-device /dev/v4l/by-id/usb-RYS_RGB_RGB_Camera_200901010001-video-index0 \
  --preset 1080p \
  --verbose \
  --disable-mocap-tcp

# Orbbec Gemini 336 RGB (RGB-D device; this host streams colour only).
# Default 1080p MJPG at 30 fps with adaptive 4-12 Mbps H.264.
.venv/bin/python examples/video/orbbec_gemini_video_host.py \
  --webcam-index 6 \
  --preset 1080p \
  --verbose \
  --disable-mocap-tcp
# Optional fallback: --preset 720p

# MuJoCo hand retargeting (Shadow Hand):
uv run examples/video/shadow_hand_video_host.py --mocap-tcp-port 5555
```

### Common flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--tcp-host` / `--tcp-port` | `0.0.0.0` / `8765` | WebSocket signaling bind |
| `--preset` | `720p` (Orbbec: `1080p`) | `480p` / `720p` / `1080p` |
| `--mocap-tcp-host` / `--mocap-tcp-port` | `0.0.0.0` / `8000` | Quest telemetry TCP sink (non-sim hosts) |
| `--disable-mocap-tcp` | off | Do **not** listen on `:8000` (required when ROS2 bridge owns `:8000`) |
| `--webcam-index` | `0` (webcam) / `-1` (Orbbec auto) | V4L2 device index |
| `--video-device` | unset | Stable V4L2 capture path accepted by `uvc_video_host.py` |
| `--verbose` | off | Detailed `[video-service]` logs |

Quest: enable **Video**, set a concrete host LAN IP (not `255.255.255.255`), signaling is `ws://<HOST_IP>:8765`.

## RYS RGB UVC camera

The `0bda:5161` camera exposes a capture node (`video-index0`) and a metadata
node (`video-index1`). Use the capture node. It supports 1920x1080 MJPEG at
30 fps, but its default `exposure_dynamic_framerate=1` reduces measured output
to about 20 fps in indoor light. `start_run.sh` disables that control before
starting the stream and uses the stable by-id path shown above. Override it with
`CAMERA_DEVICE=/dev/v4l/by-id/... ./start_run.sh rgb` when needed.

## Orbbec Gemini 336

Gemini 336 is an **RGB-D** camera (colour + stereo depth + IR). This demo only
streams **RGB** over WebRTC. Depth / point cloud are out of scope here.

On Linux the device exposes several `/dev/videoN` nodes with the same name.
Early indexes are often **IR** (looks black-and-white); colour is typically a
later node (commonly index `6` on the machines used in this repo). The Orbbec
host scores candidates by channel colourfulness and skips IR/gray nodes.
Use `ORBBEC_INDEX=6 ./start_run.sh orbbec` to bypass discovery when the RGB node
is already known.

```bash
# Auto-detect the RGB node:
uv run examples/video/orbbec_gemini_video_host.py --verbose --disable-mocap-tcp

# Confirmed command on the current Gemini 336 workstation (/dev/video6 is RGB):
.venv/bin/python examples/video/orbbec_gemini_video_host.py \
  --webcam-index 6 \
  --preset 1080p \
  --verbose \
  --disable-mocap-tcp
```

On the current i9-14900HX workstation, the same command can be restricted to
the performance CPUs while ROS 2 / RViz is active:

```bash
taskset -c 0-15 \
  .venv/bin/python examples/video/orbbec_gemini_video_host.py \
  --webcam-index 6 \
  --preset 1080p \
  --verbose \
  --disable-mocap-tcp
```

CPU numbering is host-specific. Check `lscpu -e` before copying the `taskset`
range to another machine. Do not narrow this host to `4-11`: tests showed
ongoing source overwrites at 1080p30, while `0-15` sustained 30 fps.

Successful host logs look like:

```text
[video-service] signaling server listening host=0.0.0.0 port=8765
orbbec UVC RGB selected index=6 detail=shape=... color=...
[video-service] client connected remote=(...)
[video-service] recv type=hello ...
[video-service] offer received ...
[video-service] video_state playing ...
```

### Run beside ROS 2 `view_hands`

Telemetry and video must not share the same TCP listener:

| Process | Role | Port |
|---------|------|-----:|
| `ros2 launch hand_tracking_sdk_ros2 view_hands.launch.py` | Hands/controllers → RViz | TCP `8000` |
| `orbbec_gemini_video_host.py --disable-mocap-tcp` | Orbbec RGB → Quest | WS `8765` |

Wireless Quest: **TCP (Wireless)**, host IP from `hostname -I`, port `8000`, Video on.

## Internal modules

| File | Purpose |
|------|---------|
| `_common.py` | Shared argument parsing, mocap pump, MuJoCo host runner |
| `_tracking.py` | Relative head camera and wrist tracking from mocap frames |
| `_retarget.py` | Lightweight vector-based finger retargeting for MuJoCo |

SDK source adapters live under
`src/hand_tracking_sdk/video/` (`VideoService`, `UvcCameraSourceAdapter`, WebRTC sender).

## Assets

MuJoCo XML models live under `assets/`:

- `assets/shadow_hand/` — Shadow Hand E3M5 (left, right teleop, bimanual scene)
- `assets/aloha/` — ALOHA 2 bimanual arm scene
- `assets/inspire/` — Inspire Hand (left, right, bimanual scenes)

Shadow Hand and ALOHA models are borrowed from
[MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie)
and follow their respective [license](https://github.com/google-deepmind/mujoco_menagerie/blob/main/LICENSE).
Inspire Hand assets are provided by Inspire Robots and slightly modified for simulation purposes.
