import asyncio
import fractions
from types import SimpleNamespace
from typing import Any

from hand_tracking_sdk.video.webrtc_sender import VideoWebRTCSender, _AdapterVideoTrack


class _FrameSource:
    def __init__(self, frames: list[Any]) -> None:
        self._frames = iter(frames)

    async def next_frame(self) -> Any:
        return next(self._frames)


def test_1080p_encoding_policy_is_adaptive_with_one_second_gop() -> None:
    assert VideoWebRTCSender._DEFAULT_BITRATE_BPS == 10_000_000
    assert VideoWebRTCSender._MIN_BITRATE_BPS == 4_000_000
    assert VideoWebRTCSender._MAX_BITRATE_BPS == 12_000_000
    assert VideoWebRTCSender._GOP_SIZE == 30


def test_adapter_track_preserves_capture_timestamp() -> None:
    async def run() -> None:
        time_base = fractions.Fraction(1, 90_000)
        frame = SimpleNamespace(pts=12_345, time_base=time_base)
        track = _AdapterVideoTrack(_FrameSource([frame]), fps=30)  # type: ignore[arg-type]

        result = await track.recv()

        assert result.pts == 12_345
        assert result.time_base == time_base

    asyncio.run(run())


def test_adapter_track_adds_timestamps_for_sources_without_them() -> None:
    async def run() -> None:
        frames = [
            SimpleNamespace(pts=None, time_base=None),
            SimpleNamespace(pts=None, time_base=None),
        ]
        track = _AdapterVideoTrack(_FrameSource(frames), fps=30)  # type: ignore[arg-type]

        first = await track.recv()
        second = await track.recv()

        assert first.pts == 0
        assert second.pts == 1
        assert first.time_base == fractions.Fraction(1, 30)
        assert second.time_base == fractions.Fraction(1, 30)

    asyncio.run(run())
