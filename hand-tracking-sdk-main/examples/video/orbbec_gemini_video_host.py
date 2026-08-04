"""Run host-side signaling/media service with an Orbbec Gemini UVC RGB source.

Streams color frames over the existing WebRTC path (WS signaling :8765 + H.264)
so the Quest app can display them with no client changes.

Usage::

    uv run examples/video/orbbec_gemini_video_host.py --disable-mocap-tcp
    uv run examples/video/orbbec_gemini_video_host.py --webcam-index 6 --preset 720p
"""

from __future__ import annotations

import argparse
import asyncio

from _common import build_base_parser, run_video_service

from hand_tracking_sdk.video.service import VideoServiceConfig


def _parse_args() -> argparse.Namespace:
    parser = build_base_parser(
        "Host video service (Orbbec Gemini UVC RGB source).",
        # 1080p/30 fps with bounded adaptive bitrate prioritizes detail without
        # the long recovery stalls caused by a fixed 20 Mbps stream.
        default_preset="1080p",
    )
    parser.add_argument(
        "--webcam-index",
        type=int,
        default=-1,
        help="Preferred V4L2 device index; -1 auto-discovers RGB (often index 6).",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    config = VideoServiceConfig(
        signaling_host=args.tcp_host,
        signaling_port=args.tcp_port,
        source="orbbec",
        preset=args.preset,
        encoder_backend=args.encoder,
        nvenc_preset=args.nvenc_preset,
        video_bitrate_bps=int(args.video_bitrate_mbps * 1_000_000),
        webcam_index=args.webcam_index,
        verbose=args.verbose,
    )
    return await run_video_service(
        config,
        enable_mocap_tcp=not args.disable_mocap_tcp,
        mocap_tcp_host=args.mocap_tcp_host,
        mocap_tcp_port=args.mocap_tcp_port,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
