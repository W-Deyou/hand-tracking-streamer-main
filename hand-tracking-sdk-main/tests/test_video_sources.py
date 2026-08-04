from __future__ import annotations

import asyncio
import fractions
import sys
from time import monotonic_ns
from types import SimpleNamespace
from typing import Any

import numpy as np

from hand_tracking_sdk.video.sources import OrbbecUvcSourceAdapter, UvcCameraSourceAdapter


class _FakeCapture:
    def __init__(self) -> None:
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - mirrors the OpenCV API
        return True

    def release(self) -> None:
        self.released = True


def test_preferred_orbbec_node_is_configured_without_probe(monkeypatch: Any) -> None:
    source = OrbbecUvcSourceAdapter(device_index=6)
    capture = _FakeCapture()
    configured: list[_FakeCapture] = []

    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            CAP_V4L2=200,
            VideoCapture=lambda index, backend: capture,
        ),
    )
    monkeypatch.setattr(
        source,
        "_probe_index",
        lambda index: (_ for _ in ()).throw(AssertionError("preferred node was probed")),
    )
    monkeypatch.setattr(source, "_configure_capture", configured.append)

    result = source._open_selected()

    assert result is capture
    assert configured == [capture]
    assert source.selected_device_index == 6


def test_stable_uvc_device_path_is_configured_without_discovery(monkeypatch: Any) -> None:
    device_path = "/dev/v4l/by-id/example-video-index0"
    source = UvcCameraSourceAdapter(device_path=device_path)
    opened: list[tuple[str, str, str, dict[str, str]]] = []
    stream = SimpleNamespace(codec_context=SimpleNamespace(width=1920, height=1080))
    container = SimpleNamespace(streams=SimpleNamespace(video=[stream]))

    def open_capture(device: str, *, format: str, mode: str, options: dict[str, str]) -> Any:
        opened.append((device, format, mode, options))
        return container

    monkeypatch.setitem(
        sys.modules,
        "av",
        SimpleNamespace(open=open_capture),
    )

    result = source._open_pyav_device()

    assert result is container
    assert opened == [
        (
            device_path,
            "v4l2",
            "r",
            {
                "video_size": "1280x720",
                "framerate": "30",
                "input_format": "mjpeg",
                "fflags": "nobuffer",
                "flags": "low_delay",
            },
        )
    ]
    assert source.selected_device_path == device_path


def test_capture_loop_overwrites_unconsumed_frames(monkeypatch: Any) -> None:
    source = OrbbecUvcSourceAdapter(device_index=6, width=2, height=2, fps=30)
    capture = _FakeCapture()
    source._capture = capture
    frames = iter(
        [(np.full((2, 2, 3), value, dtype=np.uint8), monotonic_ns()) for value in (1, 2, 3)]
    )

    def read_frame() -> tuple[Any, int]:
        try:
            return next(frames)
        except StopIteration as exc:
            raise RuntimeError("test capture complete") from exc

    monkeypatch.setattr(source, "_read_rgb_frame", read_frame)

    source._capture_loop()

    stats = source.get_runtime_stats()
    assert stats.frames_captured == 3
    assert stats.overwritten_frames == 2
    assert stats.capture_errors == 1
    assert capture.released


def test_next_frame_delivers_latest_capture_timestamp() -> None:
    async def run() -> None:
        source = OrbbecUvcSourceAdapter(device_index=6, width=2, height=2, fps=30)
        source._frame_ready = asyncio.Event()
        first_capture_ns = monotonic_ns()

        with source._frame_lock:
            source._latest_frame_rgb = np.zeros((2, 2, 3), dtype=np.uint8)
            source._latest_capture_ns = first_capture_ns
            source._latest_sequence = 1
        source._frame_ready.set()
        first = await source.next_frame()

        with source._frame_lock:
            source._latest_frame_rgb = np.ones((2, 2, 3), dtype=np.uint8)
            source._latest_capture_ns = first_capture_ns + 50_000_000
            source._latest_sequence = 2
        source._frame_ready.set()
        second = await source.next_frame()

        assert first.pts == 0
        assert second.pts == 4_500
        assert second.time_base == fractions.Fraction(1, 90_000)
        assert source.get_runtime_stats().frames_delivered == 2

    asyncio.run(run())
