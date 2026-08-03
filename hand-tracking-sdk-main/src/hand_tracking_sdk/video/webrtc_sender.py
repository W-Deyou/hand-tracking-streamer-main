"""Host-side WebRTC sender for one outbound H.264 video track."""

from __future__ import annotations

import asyncio
import fractions
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from hand_tracking_sdk.video.sources import VideoSourceAdapter


@dataclass(frozen=True, slots=True)
class VideoSenderStats:
    """Observable sender-side stats snapshot."""

    fps: float
    bitrate_kbps: float
    frame_drops: int
    rtt_ms: float | None


class _AdapterVideoTrack:  # Runtime subclass after aiortc import.
    """Internal adapter used to bridge source frames into aiortc track API."""

    kind = "video"

    def __init__(self, source: VideoSourceAdapter, fps: int) -> None:
        self._source = source
        self._fps = max(1, fps)
        self._pts = 0
        self._time_base = fractions.Fraction(1, self._fps)

    async def recv(self) -> Any:
        frame = await self._source.next_frame()
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += 1
        return frame


class VideoWebRTCSender:
    """One-to-one sender peer for host->Quest video."""

    def __init__(
        self,
        *,
        source: VideoSourceAdapter,
        on_local_ice_candidate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        log_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize sender with a frame source and optional ICE callback."""
        self._source = source
        self._on_local_ice_candidate = on_local_ice_candidate
        self._log_hook = log_hook
        self._pc: Any = None
        self._created_at = monotonic()
        self._frames_sent = 0
        self._bytes_sent = 0
        self._frame_drops = 0
        self._last_stats_lock = asyncio.Lock()
        self._h264_forced = False
        self._stats_prev_bytes = 0
        self._stats_prev_frames = 0
        self._stats_prev_t = self._created_at

    async def start(self) -> None:
        """Start the source and create the outbound peer/video track."""
        self._pin_webrtc_encode_bitrate()
        await self._source.start()
        self._pc = self._new_peer_connection()
        self._add_video_track()
        self._wire_ice_callbacks()

    async def stop(self) -> None:
        """Stop peer connection and release source resources."""
        if self._pc is not None:
            await self._pc.close()
            self._pc = None
        await self._source.stop()

    async def apply_offer(self, *, sdp_offer: str) -> str:
        """Apply Quest SDP offer and return host SDP answer."""
        if self._pc is None:
            raise RuntimeError("Video sender not started.")

        rtc_session_description = self._import_aiortc_symbol("RTCSessionDescription")
        offer = rtc_session_description(sdp=sdp_offer, type="offer")
        await self._pc.setRemoteDescription(offer)
        self._force_h264_codec_if_possible()
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        for t in self._pc.getTransceivers():
            self._log(
                f"transceiver kind={t.kind} direction={t.direction} "
                f"currentDirection={t.currentDirection}"
            )
        return str(self._pc.localDescription.sdp)

    async def add_remote_ice_candidate(
        self,
        *,
        candidate: str,
        sdp_mid: str | None,
        sdp_mline_index: int | None,
    ) -> None:
        """Apply one remote ICE candidate from Quest."""
        if self._pc is None:
            return
        if not candidate:
            return

        candidate_from_sdp = self._import_aiortc_sdp_symbol("candidate_from_sdp")
        parsed_candidate = candidate_from_sdp(candidate)
        parsed_candidate.sdpMid = sdp_mid
        parsed_candidate.sdpMLineIndex = sdp_mline_index
        await self._pc.addIceCandidate(parsed_candidate)

    async def get_stats(self) -> VideoSenderStats:
        """Return sender stats snapshot."""
        if self._pc is None:
            return VideoSenderStats(fps=0.0, bitrate_kbps=0.0, frame_drops=0, rtt_ms=None)

        async with self._last_stats_lock:
            try:
                report = await self._pc.getStats()
            except Exception:
                report = {}

            bytes_sent = self._bytes_sent
            frames_sent = self._frames_sent
            rtt_ms: float | None = None
            for stat in report.values() if hasattr(report, "values") else []:
                stat_type = getattr(stat, "type", "")
                if stat_type == "outbound-rtp":
                    bytes_sent = int(getattr(stat, "bytesSent", bytes_sent))
                    frames_sent = int(getattr(stat, "framesSent", frames_sent))
                if stat_type == "candidate-pair":
                    current_rtt = getattr(stat, "currentRoundTripTime", None)
                    if current_rtt is not None:
                        rtt_ms = float(current_rtt) * 1000.0

            now = monotonic()
            dt_s = max(0.001, now - self._stats_prev_t)
            d_bytes = max(0, bytes_sent - self._stats_prev_bytes)
            d_frames = max(0, frames_sent - self._stats_prev_frames)
            # Instantaneous window (not lifetime avg) so REMB/quality fixes are visible.
            bitrate_kbps = (d_bytes * 8.0 / dt_s) / 1000.0
            fps = d_frames / dt_s
            self._stats_prev_bytes = bytes_sent
            self._stats_prev_frames = frames_sent
            self._stats_prev_t = now
            self._bytes_sent = bytes_sent
            self._frames_sent = frames_sent
            return VideoSenderStats(
                fps=fps,
                bitrate_kbps=bitrate_kbps,
                frame_drops=self._frame_drops,
                rtt_ms=rtt_ms,
            )

    def _new_peer_connection(self) -> Any:
        rtc_peer_connection = self._import_aiortc_symbol("RTCPeerConnection")
        rtc_configuration = self._import_aiortc_symbol("RTCConfiguration")
        rtc_ice_server = self._import_aiortc_symbol("RTCIceServer")
        config = rtc_configuration(
            iceServers=[
                rtc_ice_server(urls=["stun:stun.l.google.com:19302"]),
            ]
        )
        pc = rtc_peer_connection(config)
        self._wire_connection_state(pc)
        return pc

    def _wire_connection_state(self, pc: Any) -> None:
        @pc.on("connectionstatechange")  # type: ignore[untyped-decorator]
        async def _on_state() -> None:
            self._log(f"connection state: {pc.connectionState}")

        @pc.on("iceconnectionstatechange")  # type: ignore[untyped-decorator]
        async def _on_ice_state() -> None:
            self._log(f"ICE connection state: {pc.iceConnectionState}")

        @pc.on("icegatheringstatechange")  # type: ignore[untyped-decorator]
        async def _on_ice_gathering() -> None:
            self._log(f"ICE gathering state: {pc.iceGatheringState}")

    def _log(self, message: str) -> None:
        if self._log_hook is not None:
            self._log_hook(message)

    def _add_video_track(self) -> None:
        if self._pc is None:
            raise RuntimeError("Peer connection not created.")

        video_stream_track = self._import_aiortc_symbol("VideoStreamTrack")

        class AdapterTrack(video_stream_track):  # type: ignore[misc, valid-type]
            def __init__(self, adapter: _AdapterVideoTrack, sender: VideoWebRTCSender) -> None:
                super().__init__()
                self._adapter = adapter
                self._sender = sender

            async def recv(self) -> Any:
                try:
                    frame = await self._adapter.recv()
                except Exception as exc:
                    import traceback

                    self._sender._log(
                        f"recv() error: {exc}\n{''.join(traceback.format_exc())}"
                    )
                    raise
                self._sender._frames_sent += 1
                if frame is None:
                    self._sender._frame_drops += 1
                return frame

        video_format = self._source.get_format()
        track = AdapterTrack(_AdapterVideoTrack(self._source, fps=video_format.fps), self)
        self._pc.addTrack(track)

    def _wire_ice_callbacks(self) -> None:
        if self._pc is None or self._on_local_ice_candidate is None:
            return

        @self._pc.on("icecandidate")  # type: ignore[untyped-decorator]
        async def _on_icecandidate(candidate: Any) -> None:
            if candidate is None:
                return
            candidate_str = str(getattr(candidate, "candidate", ""))
            self._log(f"local ICE candidate: {candidate_str[:80]}")
            payload = {
                "candidate": candidate_str,
                "sdpMid": getattr(candidate, "sdpMid", None),
                "sdpMLineIndex": getattr(candidate, "sdpMLineIndex", None),
            }
            assert self._on_local_ice_candidate is not None
            await self._on_local_ice_candidate(payload)

    def _force_h264_codec_if_possible(self) -> None:
        """Prefer H.264 on send transceiver when runtime supports codec controls."""
        if self._pc is None:
            return
        try:
            rtc_rtp_sender = self._import_aiortc_symbol("RTCRtpSender")
            capabilities = rtc_rtp_sender.getCapabilities("video")
            codecs = [
                codec
                for codec in getattr(capabilities, "codecs", [])
                if str(getattr(codec, "mimeType", "")).lower() == "video/h264"
            ]
            if not codecs:
                return
            for transceiver in self._pc.getTransceivers():
                if getattr(transceiver, "kind", "") == "video":
                    transceiver.setCodecPreferences(codecs)
                    self._h264_forced = True
        except Exception:
            return

    def _pin_webrtc_encode_bitrate(self) -> None:
        """Ignore REMB underestimates and pin a low-latency encode ceiling.

        Evidence:
        - Live Quest REMB crushed send rate to ~150 kbps; after pin, ~2.5 Mbps at
          ~15 fps (host logs) — still soft; Wi-Fi 7 BE70 is not the bottleneck.
        - aiortc applies REMB in RTCRtpSender → Encoder.target_bitrate
          (aiortc/rtcrtpsender.py + codecs/h264.py).
        - Huawei XIHE-BE70 / BE7: Wi-Fi 7 BE6500 (~6.4 Gbps class, Huawei).
        - User target: 1080p + ~20 Mbps; NVENC/QSV unavailable on this host
          (avcodec_open2 denied / not implemented) → libx264 zerolatency CBR.
        - Mirror aiortc Vp8Encoder CBR (maxrate + small bufsize) for hold without
          large VBV lag.
        """
        pinned_bps = 20_000_000
        try:
            import aiortc.codecs.h264 as h264_mod
            import aiortc.codecs.vpx as vpx_mod

            for mod in (h264_mod, vpx_mod):
                mod.DEFAULT_BITRATE = pinned_bps
                mod.MIN_BITRATE = pinned_bps
                mod.MAX_BITRATE = pinned_bps

            self._install_pinned_bitrate_property(h264_mod.H264Encoder, pinned_bps)
            self._install_pinned_bitrate_property(vpx_mod.Vp8Encoder, pinned_bps)
            self._install_h264_low_latency_cbr(h264_mod, pinned_bps)

            self._log(
                "webrtc encode bitrate pinned "
                f"bps={pinned_bps} (ignore REMB; zerolatency CBR)"
            )
        except Exception as exc:
            self._log(f"bitrate pin failed: {exc}")
            return

    def _install_pinned_bitrate_property(self, encoder_cls: Any, pinned_bps: int) -> None:
        """Replace target_bitrate so REMB writes cannot lower encode quality."""
        if getattr(encoder_cls, "_hts_bitrate_pinned", False):
            return
        attr_name = f"_{encoder_cls.__name__}__target_bitrate"

        def _get(enc_self: Any) -> int:
            return int(getattr(enc_self, attr_name, pinned_bps))

        def _set(enc_self: Any, bitrate: int) -> None:
            # Drop REMB; always publish the pinned ceiling.
            setattr(enc_self, attr_name, pinned_bps)

        encoder_cls.target_bitrate = property(_get, _set)
        encoder_cls._hts_bitrate_pinned = True

    def _install_h264_low_latency_cbr(self, h264_mod: Any, pinned_bps: int) -> None:
        """Replace aiortc H264Encoder._encode_frame with zerolatency CBR at pinned rate.

        Fork of aiortc/codecs/h264.py H264Encoder._encode_frame; only rate/options change.
        """
        encoder_cls = h264_mod.H264Encoder
        if getattr(encoder_cls, "_hts_cbr_patched", False):
            return

        import fractions

        import av

        max_fps = int(getattr(h264_mod, "MAX_FRAME_RATE", 30))

        def _encode_frame(enc_self: Any, frame: Any, force_keyframe: bool) -> Any:
            setattr(enc_self, "_H264Encoder__target_bitrate", pinned_bps)
            if enc_self.codec and (
                frame.width != enc_self.codec.width
                or frame.height != enc_self.codec.height
                or abs(pinned_bps - int(enc_self.codec.bit_rate))
                / max(int(enc_self.codec.bit_rate), 1)
                > 0.1
            ):
                enc_self.buffer_data = b""
                enc_self.buffer_pts = None
                enc_self.codec = None

            if force_keyframe:
                frame.pict_type = av.video.frame.PictureType.I
            else:
                frame.pict_type = av.video.frame.PictureType.NONE

            if enc_self.codec is None:
                enc_self.codec = av.CodecContext.create("libx264", "w")
                enc_self.codec.width = frame.width
                enc_self.codec.height = frame.height
                enc_self.codec.bit_rate = pinned_bps
                enc_self.codec.pix_fmt = "yuv420p"
                enc_self.codec.framerate = fractions.Fraction(max_fps, 1)
                enc_self.codec.time_base = fractions.Fraction(1, max_fps)
                # maxrate+bufsize mirrors aiortc Vp8Encoder CBR; bufsize~100ms.
                # ultrafast: cut encode latency vs veryfast (1080p was ~8 fps).
                enc_self.codec.options = {
                    "level": "41",
                    "tune": "zerolatency",
                    "preset": "ultrafast",
                    "maxrate": str(pinned_bps),
                    "bufsize": str(max(pinned_bps // 10, 1)),
                }
                enc_self.codec.profile = "Baseline"

            data_to_send = b""
            for package in enc_self.codec.encode(frame):
                data_to_send += bytes(package)
            if data_to_send:
                yield from enc_self._split_bitstream(data_to_send)

        encoder_cls._encode_frame = _encode_frame
        encoder_cls._hts_cbr_patched = True

    def _import_aiortc_symbol(self, symbol: str) -> Any:
        try:
            module = __import__("aiortc", fromlist=[symbol])
            return getattr(module, symbol)
        except Exception as exc:
            raise RuntimeError(
                "aiortc is required for video streaming. "
                "Install with: pip install hand-tracking-sdk[video]"
            ) from exc

    def _import_aiortc_sdp_symbol(self, symbol: str) -> Any:
        try:
            module = __import__("aiortc.sdp", fromlist=[symbol])
            return getattr(module, symbol)
        except Exception as exc:
            raise RuntimeError("aiortc.sdp helpers unavailable.") from exc
