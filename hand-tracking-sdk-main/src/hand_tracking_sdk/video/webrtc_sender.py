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
class _H264EncoderPolicy:
    backend: str
    allow_software_fallback: bool
    nvenc_preset: str
    gop_size: int
    max_fps: int
    log_hook: Callable[[str], None]
    backend_hook: Callable[[str], None]
    encode_time_hook: Callable[[float], None]


def _create_h264_codec(
    av_module: Any,
    policy: _H264EncoderPolicy,
    backend: str,
    frame: Any,
    bitrate_bps: int,
) -> Any:
    """Create one hardware or software H.264 encoder with no frame queueing."""
    codec_name = "h264_nvenc" if backend == "nvenc" else "libx264"
    codec = av_module.CodecContext.create(codec_name, "w")
    codec.width = frame.width
    codec.height = frame.height
    codec.bit_rate = bitrate_bps
    codec.pix_fmt = "yuv420p"
    codec.framerate = fractions.Fraction(policy.max_fps, 1)
    codec.time_base = fractions.Fraction(1, policy.max_fps)
    codec.gop_size = policy.gop_size
    codec.max_b_frames = 0

    maxrate = str(bitrate_bps)
    # One frame of VBV budget limits burst size without adding a multi-frame queue.
    bufsize = str(max(bitrate_bps // policy.max_fps, 1))
    if backend == "nvenc":
        codec.options = {
            "preset": policy.nvenc_preset,
            "tune": "ull",
            "rc": "cbr",
            "profile": "baseline",
            "level": "41",
            "bf": "0",
            "rc-lookahead": "0",
            "zerolatency": "1",
            "delay": "0",
            "forced-idr": "1",
            "no-scenecut": "1",
            "maxrate": maxrate,
            "bufsize": bufsize,
        }
    else:
        codec.options = {
            "level": "41",
            "tune": "zerolatency",
            "preset": "ultrafast",
            "maxrate": maxrate,
            "bufsize": bufsize,
            "x264-params": (
                f"nal-hrd=cbr:force-cfr=1:keyint={policy.gop_size}:"
                f"min-keyint={policy.gop_size}:scenecut=0:bframes=0:rc-lookahead=0"
            ),
        }
        codec.profile = "Baseline"
    return codec


@dataclass(frozen=True, slots=True)
class VideoSenderStats:
    """Observable sender-side stats snapshot."""

    fps: float
    bitrate_kbps: float
    frame_drops: int
    rtt_ms: float | None
    source_overwrites: int = 0
    stale_frame_drops: int = 0
    capture_age_ms: float | None = None
    encoder_backend: str = "unknown"
    encode_ms: float = 0.0


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
        if frame.pts is None or frame.time_base is None:
            frame.pts = self._pts
            frame.time_base = self._time_base
            self._pts += 1
        return frame


class VideoWebRTCSender:
    """One-to-one sender peer for host->Quest video."""

    _DEFAULT_BITRATE_BPS = 10_000_000
    _MIN_BITRATE_BPS = 4_000_000
    _MAX_BITRATE_BPS = 12_000_000
    _GOP_SIZE = 30
    ENCODER_BACKENDS = ("auto", "nvenc", "x264")
    NVENC_PRESETS = ("p1", "p2", "p3", "p4", "p5", "p6", "p7")

    def __init__(
        self,
        *,
        source: VideoSourceAdapter,
        on_local_ice_candidate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        log_hook: Callable[[str], None] | None = None,
        encoder_backend: str = "auto",
        nvenc_preset: str = "p1",
        video_bitrate_bps: int = _DEFAULT_BITRATE_BPS,
    ) -> None:
        """Initialize sender with a frame source and optional ICE callback."""
        if encoder_backend not in self.ENCODER_BACKENDS:
            raise ValueError(
                f"Unsupported encoder backend {encoder_backend!r}; "
                f"expected one of {', '.join(self.ENCODER_BACKENDS)}"
            )
        if nvenc_preset not in self.NVENC_PRESETS:
            raise ValueError(
                f"Unsupported NVENC preset {nvenc_preset!r}; "
                f"expected one of {', '.join(self.NVENC_PRESETS)}"
            )
        self._source = source
        self._on_local_ice_candidate = on_local_ice_candidate
        self._log_hook = log_hook
        self._requested_encoder_backend = encoder_backend
        self._encoder_backend = "unknown"
        self._nvenc_preset = nvenc_preset
        self._video_bitrate_bps = max(
            self._MIN_BITRATE_BPS,
            min(self._MAX_BITRATE_BPS, int(video_bitrate_bps)),
        )
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
        self._encode_time_total_ms = 0.0
        self._encode_time_samples = 0

    async def start(self) -> None:
        """Start the source and create the outbound peer/video track."""
        self._configure_webrtc_encoding()
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
        # aiortc snapshots the answer codec order while applying the remote
        # offer, so preferences set afterwards leave VP8 as the active codec.
        self._force_h264_codec_if_possible()
        await self._pc.setRemoteDescription(offer)
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
            source_stats = self._source.get_runtime_stats()
            encode_ms = (
                self._encode_time_total_ms / self._encode_time_samples
                if self._encode_time_samples
                else 0.0
            )
            self._encode_time_total_ms = 0.0
            self._encode_time_samples = 0
            return VideoSenderStats(
                fps=fps,
                bitrate_kbps=bitrate_kbps,
                frame_drops=(
                    self._frame_drops + source_stats.overwritten_frames + source_stats.stale_frames
                ),
                rtt_ms=rtt_ms,
                source_overwrites=source_stats.overwritten_frames,
                stale_frame_drops=source_stats.stale_frames,
                capture_age_ms=source_stats.latest_frame_age_ms,
                encoder_backend=self._encoder_backend,
                encode_ms=encode_ms,
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

                    self._sender._log(f"recv() error: {exc}\n{''.join(traceback.format_exc())}")
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

    def _configure_webrtc_encoding(self) -> None:
        """Configure bounded adaptive bitrate and low-latency H.264 encoding."""
        import aiortc.codecs.h264 as h264_mod
        import aiortc.codecs.vpx as vpx_mod
        import av

        codec_modules: tuple[Any, Any] = (h264_mod, vpx_mod)
        for mod in codec_modules:
            mod.DEFAULT_BITRATE = self._video_bitrate_bps
            mod.MIN_BITRATE = self._MIN_BITRATE_BPS
            mod.MAX_BITRATE = self._MAX_BITRATE_BPS

        backend = self._select_encoder_backend(av, h264_mod)
        self._encoder_backend = backend
        self._install_h264_low_latency_cbr(h264_mod, backend)
        self._log(
            "webrtc H.264 encoder configured "
            f"requested={self._requested_encoder_backend} active={backend} "
            f"bitrate={self._video_bitrate_bps / 1_000_000:.1f}Mbps "
            f"min={self._MIN_BITRATE_BPS / 1_000_000:.1f}Mbps "
            f"max={self._MAX_BITRATE_BPS / 1_000_000:.1f}Mbps "
            f"gop={self._GOP_SIZE}"
        )

    def _select_encoder_backend(self, av_module: Any, h264_mod: Any) -> str:
        if self._requested_encoder_backend == "x264":
            return "x264"

        policy = self._encoder_policy(
            backend="nvenc",
            allow_software_fallback=False,
            max_fps=int(getattr(h264_mod, "MAX_FRAME_RATE", 30)),
        )
        try:
            # Creating a context alone does not open NVENC. Use a driver-safe
            # probe size: current NVIDIA drivers reject very small H.264 frames.
            frame = av_module.VideoFrame(640, 360, "yuv420p")
            frame.pts = 0
            frame.time_base = fractions.Fraction(1, policy.max_fps)
            codec = _create_h264_codec(
                av_module,
                policy,
                "nvenc",
                frame,
                self._video_bitrate_bps,
            )
            packets = list(codec.encode(frame))
            if not packets:
                raise RuntimeError("NVENC probe buffered the first frame")
            list(codec.encode(None))
        except Exception as exc:
            if self._requested_encoder_backend == "nvenc":
                raise RuntimeError(f"NVENC H.264 is unavailable: {exc}") from exc
            self._log(f"NVENC unavailable, falling back to libx264: {exc}")
            return "x264"
        return "nvenc"

    def _encoder_policy(
        self,
        *,
        backend: str,
        allow_software_fallback: bool,
        max_fps: int,
    ) -> _H264EncoderPolicy:
        return _H264EncoderPolicy(
            backend=backend,
            allow_software_fallback=allow_software_fallback,
            nvenc_preset=self._nvenc_preset,
            gop_size=self._GOP_SIZE,
            max_fps=max_fps,
            log_hook=self._log,
            backend_hook=self._record_encoder_backend,
            encode_time_hook=self._record_encode_time,
        )

    def _record_encoder_backend(self, backend: str) -> None:
        if backend != self._encoder_backend:
            self._encoder_backend = backend
            self._log(f"video encoder switched to {backend}")

    def _record_encode_time(self, encode_ms: float) -> None:
        self._encode_time_total_ms += encode_ms
        self._encode_time_samples += 1

    def _install_h264_low_latency_cbr(self, h264_mod: Any, backend: str) -> None:
        """Replace aiortc H264Encoder._encode_frame with bounded low-latency CBR.

        Fork of aiortc/codecs/h264.py H264Encoder._encode_frame; only rate/options change.
        """
        encoder_cls = h264_mod.H264Encoder
        max_fps = int(getattr(h264_mod, "MAX_FRAME_RATE", 30))
        encoder_cls._hts_encoder_policy = self._encoder_policy(
            backend=backend,
            allow_software_fallback=(
                self._requested_encoder_backend == "auto" and backend == "nvenc"
            ),
            max_fps=max_fps,
        )
        if getattr(encoder_cls, "_hts_cbr_patched", False):
            return

        import av

        def _encode_frame(enc_self: Any, frame: Any, force_keyframe: bool) -> Any:
            policy: _H264EncoderPolicy = type(enc_self)._hts_encoder_policy
            target_bps = max(
                h264_mod.MIN_BITRATE,
                min(int(enc_self.target_bitrate), h264_mod.MAX_BITRATE),
            )
            codec_recreated = False
            if enc_self.codec and (
                frame.width != enc_self.codec.width
                or frame.height != enc_self.codec.height
                or abs(target_bps - int(enc_self.codec.bit_rate))
                / max(int(enc_self.codec.bit_rate), 1)
                > 0.1
            ):
                enc_self.buffer_data = b""
                enc_self.buffer_pts = None
                enc_self.codec = None
                codec_recreated = True

            if force_keyframe or codec_recreated or enc_self.codec is None:
                frame.pict_type = av.video.frame.PictureType.I
            else:
                frame.pict_type = av.video.frame.PictureType.NONE

            backend_for_frame = getattr(
                enc_self, "_hts_backend_override", policy.backend
            )
            started_at = monotonic()
            try:
                if enc_self.codec is None:
                    enc_self.codec = _create_h264_codec(
                        av, policy, backend_for_frame, frame, target_bps
                    )
                    policy.backend_hook(backend_for_frame)
                packages = list(enc_self.codec.encode(frame))
            except Exception as exc:
                if backend_for_frame != "nvenc" or not policy.allow_software_fallback:
                    raise
                policy.log_hook(
                    f"NVENC encode failed, falling back to libx264: {exc}"
                )
                backend_for_frame = "x264"
                enc_self._hts_backend_override = backend_for_frame
                enc_self.buffer_data = b""
                enc_self.buffer_pts = None
                enc_self.codec = _create_h264_codec(
                    av, policy, backend_for_frame, frame, target_bps
                )
                frame.pict_type = av.video.frame.PictureType.I
                packages = list(enc_self.codec.encode(frame))
                policy.backend_hook(backend_for_frame)
            policy.encode_time_hook((monotonic() - started_at) * 1000.0)

            data_to_send = b"".join(bytes(package) for package in packages)
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
