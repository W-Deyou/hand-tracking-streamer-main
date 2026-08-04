"""Run host-side signaling/media service with a low-latency UVC RGB source.

Use a stable ``/dev/v4l/by-id`` capture path when available so reconnecting a
camera does not change which device is streamed.
"""

from __future__ import annotations

import argparse
import asyncio

from _common import build_base_parser, run_video_service

from hand_tracking_sdk.video.service import VideoServiceConfig


def _parse_args() -> argparse.Namespace:
    parser = build_base_parser(
        "Host video service (low-latency UVC RGB source).",
        default_preset="1080p",
    )
    parser.add_argument(
        "--video-device",
        help="Stable V4L2 capture path, preferably /dev/v4l/by-id/...-video-index0.",
    )
    parser.add_argument(
        "--webcam-index",
        type=int,
        default=-1,
        help="Fallback /dev/videoN index when --video-device is not supplied.",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    config = VideoServiceConfig(
        signaling_host=args.tcp_host,
        signaling_port=args.tcp_port,
        source="uvc",
        preset=args.preset,
        encoder_backend=args.encoder,
        nvenc_preset=args.nvenc_preset,
        video_bitrate_bps=int(args.video_bitrate_mbps * 1_000_000),
        webcam_index=args.webcam_index,
        video_device=args.video_device,
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
