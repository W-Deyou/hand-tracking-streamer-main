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
| `orbbec_gemini_video_host.py` | Orbbec Gemini UVC | Auto-picks **RGB** `/dev/videoN`, streams via WebRTC |
| `inspire_hand_video_host.py` | MuJoCo | Bimanual Inspire Hand with vector retargeting |
| `shadow_hand_video_host.py` | MuJoCo | Bimanual Shadow Hand E3M5 with vector retargeting |
| `aloha_video_host.py` | MuJoCo | ALOHA 2 bimanual arms with IK (requires `mink`) |

## Quick start

```bash
# Simplest — no camera required:
uv run examples/video/test_pattern_video_host.py --verbose

# USB webcam:
uv run examples/video/webcam_video_host.py --webcam-index 0 --preset 720p

# Orbbec Gemini 336 RGB (RGB-D device; this host streams colour only).
# Default 720p MJPG: sharper than YUYV 640x480, far smoother than 1080p software encode.
uv run examples/video/orbbec_gemini_video_host.py --verbose --disable-mocap-tcp
# Optional: --preset 1080p (higher detail, lower FPS on CPU encode)

# MuJoCo hand retargeting (Shadow Hand):
uv run examples/video/shadow_hand_video_host.py --mocap-tcp-port 5555
```

### Common flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--tcp-host` / `--tcp-port` | `0.0.0.0` / `8765` | WebSocket signaling bind |
| `--preset` | `720p` | `480p` / `720p` / `1080p` |
| `--mocap-tcp-host` / `--mocap-tcp-port` | `0.0.0.0` / `8000` | Quest telemetry TCP sink (non-sim hosts) |
| `--disable-mocap-tcp` | off | Do **not** listen on `:8000` (required when ROS2 bridge owns `:8000`) |
| `--webcam-index` | `0` (webcam) / `-1` (Orbbec auto) | V4L2 device index |
| `--verbose` | off | Detailed `[video-service]` logs |

Quest: enable **Video**, set a concrete host LAN IP (not `255.255.255.255`), signaling is `ws://<HOST_IP>:8765`.

## Orbbec Gemini 336

Gemini 336 is an **RGB-D** camera (colour + stereo depth + IR). This demo only
streams **RGB** over WebRTC. Depth / point cloud are out of scope here.

On Linux the device exposes several `/dev/videoN` nodes with the same name.
Early indexes are often **IR** (looks black-and-white); colour is typically a
later node (commonly index `6` on the machines used in this repo). The Orbbec
host scores candidates by channel colourfulness and skips IR/gray nodes.

```bash
# Auto RGB (preferred):
uv run examples/video/orbbec_gemini_video_host.py --verbose --disable-mocap-tcp

# Force colour node if auto-pick fails or the image is gray:
uv run examples/video/orbbec_gemini_video_host.py --verbose --disable-mocap-tcp --webcam-index 6
```

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
`src/hand_tracking_sdk/video/` (`VideoService`, `OrbbecUvcSourceAdapter`, WebRTC sender).

## Assets

MuJoCo XML models live under `assets/`:

- `assets/shadow_hand/` — Shadow Hand E3M5 (left, right teleop, bimanual scene)
- `assets/aloha/` — ALOHA 2 bimanual arm scene
- `assets/inspire/` — Inspire Hand (left, right, bimanual scenes)

Shadow Hand and ALOHA models are borrowed from
[MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie)
and follow their respective [license](https://github.com/google-deepmind/mujoco_menagerie/blob/main/LICENSE).
Inspire Hand assets are provided by Inspire Robots and slightly modified for simulation purposes.
