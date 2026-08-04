import asyncio
import fractions
from types import SimpleNamespace
from typing import Any

import pytest

from hand_tracking_sdk.video.webrtc_sender import (
    VideoWebRTCSender,
    _AdapterVideoTrack,
    _create_h264_codec,
)


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


def test_h264_preference_is_applied_before_remote_offer() -> None:
    async def run() -> None:
        events: list[str] = []

        class _PeerConnection:
            localDescription = SimpleNamespace(sdp="answer-sdp")

            async def setRemoteDescription(self, _offer: Any) -> None:
                events.append("remote")

            async def createAnswer(self) -> Any:
                events.append("answer")
                return SimpleNamespace(sdp="answer-sdp", type="answer")

            async def setLocalDescription(self, _answer: Any) -> None:
                events.append("local")

            def getTransceivers(self) -> list[Any]:
                return []

        sender = VideoWebRTCSender(source=_FrameSource([]))  # type: ignore[arg-type]
        sender._pc = _PeerConnection()
        sender._force_h264_codec_if_possible = lambda: events.append("h264")  # type: ignore[method-assign]
        sender._import_aiortc_symbol = (  # type: ignore[method-assign]
            lambda _symbol: lambda **values: SimpleNamespace(**values)
        )

        result = await sender.apply_offer(sdp_offer="offer-sdp")

        assert result == "answer-sdp"
        assert events == ["h264", "remote", "answer", "local"]

    asyncio.run(run())


def test_nvenc_policy_disables_encoder_queues() -> None:
    sender = VideoWebRTCSender(
        source=_FrameSource([]),  # type: ignore[arg-type]
        encoder_backend="nvenc",
        nvenc_preset="p1",
    )
    policy = sender._encoder_policy(
        backend="nvenc", allow_software_fallback=False, max_fps=30
    )

    class _Codec:
        pass

    codec = _Codec()
    fake_av = SimpleNamespace(
        CodecContext=SimpleNamespace(create=lambda _name, _mode: codec)
    )
    frame = SimpleNamespace(width=1920, height=1080)

    result = _create_h264_codec(fake_av, policy, "nvenc", frame, 10_000_000)

    assert result.options["preset"] == "p1"
    assert result.options["tune"] == "ull"
    assert result.options["rc"] == "cbr"
    assert result.options["bf"] == "0"
    assert result.options["rc-lookahead"] == "0"
    assert result.options["zerolatency"] == "1"
    assert result.options["delay"] == "0"
    assert result.gop_size == 30
    assert result.max_b_frames == 0
    assert result.options["bufsize"] == "333333"


def test_auto_encoder_falls_back_when_nvenc_probe_fails() -> None:
    messages: list[str] = []
    sender = VideoWebRTCSender(
        source=_FrameSource([]),  # type: ignore[arg-type]
        encoder_backend="auto",
        log_hook=messages.append,
    )

    class _VideoFrame:
        def __init__(self, width: int, height: int, _format: str) -> None:
            self.width = width
            self.height = height
            self.pts = None
            self.time_base = None

    def _fail_create(_name: str, _mode: str) -> Any:
        raise RuntimeError("no encoder device")

    fake_av = SimpleNamespace(
        VideoFrame=_VideoFrame,
        CodecContext=SimpleNamespace(create=_fail_create),
    )

    result = sender._select_encoder_backend(
        fake_av, SimpleNamespace(MAX_FRAME_RATE=30)
    )

    assert result == "x264"
    assert any("falling back to libx264" in message for message in messages)


def test_explicit_nvenc_fails_when_probe_fails() -> None:
    sender = VideoWebRTCSender(
        source=_FrameSource([]),  # type: ignore[arg-type]
        encoder_backend="nvenc",
    )

    class _VideoFrame:
        def __init__(self, width: int, height: int, _format: str) -> None:
            self.width = width
            self.height = height
            self.pts = None
            self.time_base = None

    fake_av = SimpleNamespace(
        VideoFrame=_VideoFrame,
        CodecContext=SimpleNamespace(
            create=lambda _name, _mode: (_ for _ in ()).throw(
                RuntimeError("no encoder device")
            )
        ),
    )

    with pytest.raises(RuntimeError, match="NVENC H.264 is unavailable"):
        sender._select_encoder_backend(fake_av, SimpleNamespace(MAX_FRAME_RATE=30))


def test_encoder_configuration_is_validated() -> None:
    with pytest.raises(ValueError, match="Unsupported encoder backend"):
        VideoWebRTCSender(
            source=_FrameSource([]),  # type: ignore[arg-type]
            encoder_backend="invalid",
        )
