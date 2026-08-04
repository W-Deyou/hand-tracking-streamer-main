"""Video source adapters for host-side WebRTC transmission."""

from __future__ import annotations

import asyncio
import fractions
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic, monotonic_ns
from typing import Any


@dataclass(frozen=True, slots=True)
class VideoFormat:
    """Video format contract produced by a source adapter."""

    width: int
    height: int
    fps: int


@dataclass(frozen=True, slots=True)
class VideoSourceStats:
    """Runtime counters for a latency-sensitive video source."""

    frames_captured: int = 0
    frames_delivered: int = 0
    overwritten_frames: int = 0
    stale_frames: int = 0
    capture_errors: int = 0
    latest_frame_age_ms: float | None = None


class VideoSourceAdapter(ABC):
    """Abstract source adapter used by the WebRTC sender."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize source resources."""

    @abstractmethod
    async def stop(self) -> None:
        """Release source resources."""

    @abstractmethod
    async def next_frame(self) -> Any:
        """Return next video frame object compatible with `av.VideoFrame`."""

    @abstractmethod
    def get_format(self) -> VideoFormat:
        """Return source format."""

    def get_runtime_stats(self) -> VideoSourceStats:
        """Return source counters when the adapter exposes them."""
        return VideoSourceStats()


class TestPatternSourceAdapter(VideoSourceAdapter):
    """Synthetic color-bar test source."""

    def __init__(self, *, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self._format = VideoFormat(width=width, height=height, fps=fps)
        self._frame_index = 0

    async def start(self) -> None:
        self._frame_index = 0

    async def stop(self) -> None:
        return None

    def get_format(self) -> VideoFormat:
        return self._format

    async def next_frame(self) -> Any:
        # Lazy imports keep video dependencies optional for non-video SDK usage.
        import av
        import numpy as np

        w, h = self._format.width, self._format.height
        image = np.zeros((h, w, 3), dtype=np.uint8)
        t = self._frame_index

        # Horizontal RGB bars + moving luminance stripe.
        band_w = max(1, w // 6)
        image[:, 0:band_w, :] = (255, 0, 0)
        image[:, band_w : 2 * band_w, :] = (0, 255, 0)
        image[:, 2 * band_w : 3 * band_w, :] = (0, 0, 255)
        image[:, 3 * band_w : 4 * band_w, :] = (255, 255, 0)
        image[:, 4 * band_w : 5 * band_w, :] = (255, 0, 255)
        image[:, 5 * band_w :, :] = (0, 255, 255)
        stripe_x = (t * 7) % w
        image[:, max(0, stripe_x - 8) : min(w, stripe_x + 8), :] = (255, 255, 255)

        # Encode frame index timestamp pattern as low-cost modulation.
        pulse = int((monotonic_ns() // 100_000_000) % 2) * 30
        image[0:20, 0:200, :] = (pulse, pulse, pulse)

        self._frame_index += 1
        return av.VideoFrame.from_ndarray(image, format="rgb24")


class WebcamSourceAdapter(VideoSourceAdapter):
    """Webcam-backed source adapter."""

    def __init__(
        self,
        *,
        device_index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
    ) -> None:
        self._format = VideoFormat(width=width, height=height, fps=fps)
        self._device_index = device_index
        self._capture: Any = None

    async def start(self) -> None:
        import cv2

        capture = cv2.VideoCapture(self._device_index)
        if not capture.isOpened():
            raise RuntimeError(f"Failed to open webcam index {self._device_index}.")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._format.width))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._format.height))
        capture.set(cv2.CAP_PROP_FPS, float(self._format.fps))
        self._capture = capture

    async def stop(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def get_format(self) -> VideoFormat:
        return self._format

    async def next_frame(self) -> Any:
        import av
        import cv2

        if self._capture is None:
            raise RuntimeError("Webcam source not started.")

        ok, frame_bgr = self._capture.read()
        if not ok:
            raise RuntimeError("Failed to read webcam frame.")
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return av.VideoFrame.from_ndarray(frame_rgb, format="rgb24")


class UvcCameraSourceAdapter(VideoSourceAdapter):
    """Low-latency UVC RGB source with timed V4L2 node discovery.

    Some cameras expose multiple `/dev/videoN` nodes (RGB/IR/metadata). IR often
    appears as HxWx3 with near-identical channels, so automatic discovery scores
    colorfulness and prefers a true RGB node. A stable `/dev/v4l/by-id` path can
    be supplied to bypass discovery and Linux video index changes.
    """

    # Gemini 336 on this host: IR often lands on early indexes; RGB nearer to 6.
    _DEFAULT_CANDIDATES = (6, 4, 2, 0, 1, 3, 5, 7)
    _PROBE_TIMEOUT_S = 3.0
    _RTP_CLOCK_RATE = 90_000
    # Mean |R-G|+|G-B|+|R-B|; IR duplicated to 3ch is typically ~0.
    _MIN_COLOR_SCORE = 3.0

    def __init__(
        self,
        *,
        device_index: int = -1,
        device_path: str | None = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        log_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._format = VideoFormat(width=width, height=height, fps=fps)
        self._preferred_index = device_index
        self._device_path = device_path
        self._log_hook = log_hook
        self._capture: Any = None
        self._selected_index: int | None = None
        # The single worker continuously drains V4L2. Consumers read a separate
        # one-frame slot, so encoding or networking can never build a camera queue.
        self._io_executor = ThreadPoolExecutor(max_workers=1)
        self._capture_future: Future[None] | None = None
        self._capture_stop = Event()
        self._frame_lock = Lock()
        self._frame_ready: asyncio.Event | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._latest_frame_rgb: Any = None
        self._latest_video_frame: Any = None
        self._latest_capture_ns = 0
        self._latest_sequence = 0
        self._consumed_sequence = 0
        self._capture_error: Exception | None = None
        self._pts_epoch_ns: int | None = None
        self._frames_captured = 0
        self._frames_delivered = 0
        self._overwritten_frames = 0
        self._stale_frames = 0
        self._capture_errors = 0

    @property
    def selected_device_index(self) -> int | None:
        return self._selected_index

    @property
    def selected_device_path(self) -> str | None:
        return self._device_path

    def _candidate_indices(self) -> list[int]:
        if self._preferred_index >= 0:
            ordered = [self._preferred_index]
            ordered.extend(i for i in self._DEFAULT_CANDIDATES if i != self._preferred_index)
            return ordered
        return list(self._DEFAULT_CANDIDATES)

    @staticmethod
    def _color_score(frame: Any) -> float:
        """Higher means more likely true RGB (not IR grayscale in 3 channels)."""
        import numpy as np

        if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
            return 0.0
        # OpenCV delivers BGR; channel differences still measure chroma.
        b = frame[:, :, 0].astype(np.float32)
        g = frame[:, :, 1].astype(np.float32)
        r = frame[:, :, 2].astype(np.float32)
        return float(np.mean(np.abs(r - g) + np.abs(g - b) + np.abs(r - b)))

    @classmethod
    def _probe_index(cls, index: int) -> tuple[bool, str, float, Any | None]:
        """Blocking probe: open + read one frame.

        Returns (ok, detail, color_score, capture). Caller owns a successful capture.
        """
        import cv2

        capture = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            return False, "open_failed", 0.0, None
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            return False, "read_failed", 0.0, None
        if frame.ndim != 3 or frame.shape[2] < 3:
            capture.release()
            return False, f"bad_shape={getattr(frame, 'shape', None)}", 0.0, None
        score = cls._color_score(frame)
        h, w = frame.shape[:2]
        detail = f"shape={w}x{h}x{frame.shape[2]} color={score:.2f}"
        if score < cls._MIN_COLOR_SCORE:
            capture.release()
            return False, f"ir_or_gray({detail})", score, None
        return True, detail, score, capture

    def _open_selected(self) -> Any:
        """Blocking discovery + configure capture for streaming."""
        import cv2

        if self._preferred_index >= 0:
            capture = cv2.VideoCapture(self._preferred_index, cv2.CAP_V4L2)
            if not capture.isOpened():
                capture.release()
                raise RuntimeError(
                    f"Failed to open preferred UVC RGB node /dev/video{self._preferred_index}."
                )
            try:
                # Configure before the first read. Gemini can stall when a probe
                # starts streaming and the mode is changed afterward.
                self._configure_capture(capture)
            except Exception:
                capture.release()
                raise
            self._selected_index = self._preferred_index
            if self._log_hook is not None:
                self._log_hook(
                    f"UVC RGB selected preferred index={self._preferred_index} "
                    f"format={self._format.width}x{self._format.height}@{self._format.fps}"
                )
            return capture

        probe_log: list[str] = []
        best: tuple[float, int, Any, str] | None = None  # score, index, capture, detail

        for index in self._candidate_indices():
            # Fresh executor per candidate so a hung open does not block the next probe.
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(self._probe_index, index)
                ok, detail, score, probed_capture = future.result(timeout=self._PROBE_TIMEOUT_S)
            except TimeoutError:
                probe_log.append(f"{index}:timeout>{self._PROBE_TIMEOUT_S:.0f}s")
                executor.shutdown(wait=False, cancel_futures=True)
                continue
            except Exception as exc:
                probe_log.append(f"{index}:error={exc}")
                executor.shutdown(wait=False, cancel_futures=True)
                continue
            executor.shutdown(wait=False, cancel_futures=True)
            if not ok or probed_capture is None:
                probe_log.append(f"{index}:{detail}")
                continue

            probe_log.append(f"{index}:ok({detail})")
            if best is None or score > best[0]:
                if best is not None:
                    best[2].release()
                best = (score, index, probed_capture, detail)
            else:
                probed_capture.release()

            # Preferred index that already looks like RGB: take it immediately.
            if self._preferred_index >= 0 and index == self._preferred_index:
                break

        if best is None:
            raise RuntimeError(
                "Failed to find a UVC RGB node (skipped metadata/IR/gray nodes). "
                f"Probe results: [{', '.join(probe_log) or 'none'}]. "
                "Use --video-device with a stable /dev/v4l/by-id capture path."
            )

        score, index, capture, detail = best
        self._configure_capture(capture)
        self._selected_index = index
        if self._log_hook is not None:
            self._log_hook(
                f"UVC RGB selected index={index} "
                f"format={self._format.width}x{self._format.height}@{self._format.fps} "
                f"probe={detail} probed=[{', '.join(probe_log)}]"
            )
        return capture

    def _configure_capture(self, capture: Any) -> None:
        """Negotiate MJPG at the requested size (avoid accidental 1080p overload)."""
        import cv2

        target_w = self._format.width
        target_h = self._format.height
        target_fps = self._format.fps
        target_area = target_w * target_h
        # YUYV typically caps at 640x480; MJPG unlocks 720p/1080p.
        # Only try modes at or below the requested preset so 720p stays real-time.
        ladder = [(1920, 1080), (1280, 720), (640, 480)]
        sizes: list[tuple[int, int]] = [(target_w, target_h)]
        for width, height in ladder:
            if (width, height) == (target_w, target_h):
                continue
            if width * height <= target_area:
                sizes.append((width, height))

        attempts: list[tuple[str, int, int]] = []
        for width, height in sizes:
            attempts.append(("MJPG", width, height))
        for width, height in sizes:
            attempts.append(("YUYV", width, height))

        for fourcc, width, height in attempts:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cv2_module: Any = cv2
            capture.set(cv2.CAP_PROP_FOURCC, cv2_module.VideoWriter_fourcc(*fourcc))
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
            capture.set(cv2.CAP_PROP_FPS, float(target_fps))
            frame = None
            ok = False
            for _ in range(5):
                ok, frame = capture.read()
                if ok and frame is not None and frame.ndim == 3:
                    break
            if not ok or frame is None or frame.ndim != 3:
                continue
            got_h, got_w = int(frame.shape[0]), int(frame.shape[1])
            if self._color_score(frame) < self._MIN_COLOR_SCORE:
                continue
            self._format = VideoFormat(width=got_w, height=got_h, fps=target_fps)
            if self._log_hook is not None:
                self._log_hook(
                    f"UVC capture mode fourcc={fourcc} "
                    f"requested={width}x{height} actual={got_w}x{got_h}"
                )
            return

        if self._log_hook is not None:
            self._log_hook(
                "UVC capture mode negotiation failed; "
                f"keeping requested {target_w}x{target_h}@{target_fps}"
            )

    def _read_rgb_frame(self) -> tuple[Any, int]:
        import cv2

        if self._capture is None:
            raise RuntimeError("UVC camera source not started.")
        ok = self._capture.grab()
        capture_ns = self._capture_timestamp_ns(self._capture, cv2)
        if not ok:
            raise RuntimeError("Failed to grab a UVC camera frame.")
        ok, frame_bgr = self._capture.retrieve()
        if not ok or frame_bgr is None:
            ok, frame_bgr = self._capture.read()
            capture_ns = self._capture_timestamp_ns(self._capture, cv2)
        if not ok or frame_bgr is None:
            raise RuntimeError("Failed to read a UVC camera frame.")
        return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB), capture_ns

    @staticmethod
    def _capture_timestamp_ns(capture: Any, cv2: Any) -> int:
        """Use the V4L2 timestamp when OpenCV exposes the monotonic clock."""
        dequeue_ns = monotonic_ns()
        try:
            timestamp_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
        except Exception:
            return dequeue_ns
        timestamp_ns = int(timestamp_ms * 1_000_000.0)
        if timestamp_ns <= 0 or abs(dequeue_ns - timestamp_ns) > 5_000_000_000:
            return dequeue_ns
        return timestamp_ns

    def _notify_frame_ready(self) -> None:
        if self._event_loop is None or self._frame_ready is None:
            return
        try:
            self._event_loop.call_soon_threadsafe(self._frame_ready.set)
        except RuntimeError:
            return

    def _open_pyav_device(self) -> Any:
        """Open a known UVC device without OpenCV's synchronous MJPEG bottleneck."""
        import av

        if self._device_path is None:
            raise RuntimeError("A stable UVC device path is required.")
        options = {
            "video_size": f"{self._format.width}x{self._format.height}",
            "framerate": str(self._format.fps),
            "input_format": "mjpeg",
            "fflags": "nobuffer",
            "flags": "low_delay",
        }
        container = av.open(self._device_path, format="v4l2", mode="r", options=options)
        stream = container.streams.video[0]
        width = int(stream.codec_context.width)
        height = int(stream.codec_context.height)
        self._format = VideoFormat(width=width, height=height, fps=self._format.fps)
        if self._log_hook is not None:
            self._log_hook(
                f"UVC capture mode backend=pyav fourcc=MJPG "
                f"actual={width}x{height}@{self._format.fps}"
            )
            self._log_hook(
                f"UVC RGB selected device={self._device_path} "
                f"format={width}x{height}@{self._format.fps}"
            )
        return container

    @staticmethod
    def _video_frame_timestamp_ns(frame: Any) -> int:
        dequeue_ns = monotonic_ns()
        if frame.pts is None or frame.time_base is None:
            return dequeue_ns
        timestamp_ns = int(frame.pts * frame.time_base * 1_000_000_000)
        if timestamp_ns <= 0 or abs(dequeue_ns - timestamp_ns) > 5_000_000_000:
            return dequeue_ns
        return timestamp_ns

    def _capture_loop_pyav(self) -> None:
        try:
            for frame in self._capture.decode(video=0):
                if self._capture_stop.is_set():
                    break
                capture_ns = self._video_frame_timestamp_ns(frame)
                with self._frame_lock:
                    if self._latest_sequence > self._consumed_sequence:
                        self._overwritten_frames += 1
                    self._latest_video_frame = frame
                    self._latest_frame_rgb = None
                    self._latest_capture_ns = capture_ns
                    self._latest_sequence += 1
                    self._frames_captured += 1
                self._notify_frame_ready()
        except Exception as exc:
            if not self._capture_stop.is_set():
                with self._frame_lock:
                    self._capture_error = exc
                    self._capture_errors += 1
                if self._log_hook is not None:
                    self._log_hook(f"UVC PyAV capture loop failed: {exc}")
        finally:
            if self._capture is not None:
                self._capture.close()
                self._capture = None
            self._notify_frame_ready()

    def _capture_loop(self) -> None:
        try:
            while not self._capture_stop.is_set():
                frame_rgb, capture_ns = self._read_rgb_frame()
                with self._frame_lock:
                    if self._latest_sequence > self._consumed_sequence:
                        self._overwritten_frames += 1
                    self._latest_frame_rgb = frame_rgb
                    self._latest_video_frame = None
                    self._latest_capture_ns = capture_ns
                    self._latest_sequence += 1
                    self._frames_captured += 1
                self._notify_frame_ready()
        except Exception as exc:
            if not self._capture_stop.is_set():
                with self._frame_lock:
                    self._capture_error = exc
                    self._capture_errors += 1
                if self._log_hook is not None:
                    self._log_hook(f"UVC OpenCV capture loop failed: {exc}")
        finally:
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            self._notify_frame_ready()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._event_loop = loop
        self._frame_ready = asyncio.Event()
        self._capture_stop.clear()
        with self._frame_lock:
            self._latest_frame_rgb = None
            self._latest_video_frame = None
            self._latest_capture_ns = 0
            self._latest_sequence = 0
            self._consumed_sequence = 0
            self._capture_error = None
            self._pts_epoch_ns = None
            self._frames_captured = 0
            self._frames_delivered = 0
            self._overwritten_frames = 0
            self._stale_frames = 0
            self._capture_errors = 0
        if self._device_path is not None:
            self._capture = await loop.run_in_executor(self._io_executor, self._open_pyav_device)
            self._capture_future = self._io_executor.submit(self._capture_loop_pyav)
        else:
            self._capture = await loop.run_in_executor(self._io_executor, self._open_selected)
            self._capture_future = self._io_executor.submit(self._capture_loop)

    async def stop(self) -> None:
        self._capture_stop.set()
        self._notify_frame_ready()
        if self._capture_future is not None:
            try:
                await asyncio.wait_for(
                    asyncio.wrap_future(self._capture_future),
                    timeout=max(2.0, 3.0 / max(1, self._format.fps)),
                )
            except TimeoutError:
                if self._capture is not None:
                    close = getattr(self._capture, "close", None)
                    if close is None:
                        close = self._capture.release
                    close()
            self._capture_future = None
        self._io_executor.shutdown(wait=False, cancel_futures=True)
        self._io_executor = ThreadPoolExecutor(max_workers=1)
        self._selected_index = None
        self._frame_ready = None
        self._event_loop = None

    def get_format(self) -> VideoFormat:
        return self._format

    def get_runtime_stats(self) -> VideoSourceStats:
        with self._frame_lock:
            latest_age_ms = (
                None
                if self._latest_capture_ns <= 0
                else max(0.0, (monotonic_ns() - self._latest_capture_ns) / 1_000_000.0)
            )
            return VideoSourceStats(
                frames_captured=self._frames_captured,
                frames_delivered=self._frames_delivered,
                overwritten_frames=self._overwritten_frames,
                stale_frames=self._stale_frames,
                capture_errors=self._capture_errors,
                latest_frame_age_ms=latest_age_ms,
            )

    async def next_frame(self) -> Any:
        import av

        if self._frame_ready is None:
            raise RuntimeError("UVC camera source not started.")

        max_age_ns = int((2.0 / max(1, self._format.fps)) * 1_000_000_000)
        while True:
            await self._frame_ready.wait()
            self._frame_ready.clear()
            with self._frame_lock:
                if self._capture_error is not None:
                    raise RuntimeError("UVC capture loop stopped.") from self._capture_error
                if self._latest_sequence <= self._consumed_sequence:
                    if self._capture_stop.is_set():
                        raise RuntimeError("UVC camera source stopped.")
                    continue
                frame_rgb = self._latest_frame_rgb
                video_frame = self._latest_video_frame
                capture_ns = self._latest_capture_ns
                self._consumed_sequence = self._latest_sequence
                age_ns = max(0, monotonic_ns() - capture_ns)
                if age_ns > max_age_ns:
                    self._stale_frames += 1
                    continue
                if self._pts_epoch_ns is None:
                    self._pts_epoch_ns = capture_ns
                pts = round(
                    (capture_ns - self._pts_epoch_ns) * self._RTP_CLOCK_RATE / 1_000_000_000
                )
                self._frames_delivered += 1

            frame = (
                video_frame
                if video_frame is not None
                else av.VideoFrame.from_ndarray(frame_rgb, format="rgb24")
            )
            frame.pts = pts
            frame.time_base = fractions.Fraction(1, self._RTP_CLOCK_RATE)
            return frame


# Backwards-compatible name for existing Orbbec integrations.
OrbbecUvcSourceAdapter = UvcCameraSourceAdapter


class MujocoSourceAdapter(VideoSourceAdapter):
    """MuJoCo offscreen renderer source adapter."""

    def __init__(
        self,
        *,
        model_path: str,
        camera: str | None = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        pre_step: Callable[[Any, Any], None] | None = None,
        perf_hook: Callable[[dict[str, float]], None] | None = None,
    ) -> None:
        self._format = VideoFormat(width=width, height=height, fps=fps)
        self._model_path = model_path
        self._camera = camera
        self._camera_arg: Any = camera
        self._pre_step = pre_step
        self._perf_hook = perf_hook
        self._mujoco: Any = None
        self._model: Any = None
        self._data: Any = None
        self._renderer: Any = None
        self._last_render_ts = 0.0
        # Single-thread executor: OpenGL contexts are thread-local, so init
        # and every render call must happen on the same OS thread.
        self._gl_executor = ThreadPoolExecutor(max_workers=1)

    def _init_mujoco(self) -> None:
        """Blocking MuJoCo setup — runs in a worker thread."""
        import mujoco

        self._mujoco = mujoco
        self._model = mujoco.MjModel.from_xml_path(self._model_path)
        self._data = mujoco.MjData(self._model)
        self._renderer = mujoco.Renderer(
            self._model,
            width=self._format.width,
            height=self._format.height,
        )
        if self._camera is not None and self._camera.isdigit():
            self._camera_arg = int(self._camera)
        mujoco.mj_forward(self._model, self._data)
        # Cap catch-up physics steps at 2x target to prevent spiral-of-death
        # after render hiccups or on the very first frame.
        frame_interval_s = 1.0 / max(1, self._format.fps)
        self._max_physics_steps = max(1, round(2.0 * frame_interval_s / self._model.opt.timestep))
        self._last_render_ts = monotonic()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._gl_executor, self._init_mujoco)
        except ImportError as exc:
            raise RuntimeError(
                "mujoco is required for sim source. "
                "Install with: pip install hand-tracking-sdk[sim]"
            ) from exc

    async def stop(self) -> None:
        if self._renderer is not None:
            close_fn = getattr(self._renderer, "close", None)
            if callable(close_fn):
                close_fn()
        self._renderer = None
        self._data = None
        self._model = None
        self._mujoco = None
        self._gl_executor.shutdown(wait=False)

    def get_format(self) -> VideoFormat:
        return self._format

    def _step_and_render(self) -> Any:
        """Blocking sim step + render — runs in a worker thread."""
        import numpy as np

        perf = self._perf_hook is not None

        # Best-effort frame production — no artificial pacing.  Downstream
        # H.264 encoding + async round-trip provides natural throttling.
        now = monotonic()
        dt = now - self._last_render_ts
        self._last_render_ts = now

        if perf:
            t0 = monotonic_ns()

        if self._pre_step is not None:
            self._pre_step(self._model, self._data)

        if perf:
            t1 = monotonic_ns()

        # Compute physics steps from actual elapsed wall time so simulation
        # advances at real-time speed regardless of frame rate jitter.
        n_steps = max(1, round(dt / self._model.opt.timestep))
        n_steps = min(n_steps, self._max_physics_steps)

        for _ in range(n_steps):
            self._mujoco.mj_step(self._model, self._data)

        if perf:
            t2 = monotonic_ns()

        self._renderer.update_scene(self._data, camera=self._camera_arg)
        # Copy the rendered pixels so the renderer buffer can be reused.
        pixels = np.array(self._renderer.render())

        if perf:
            t3 = monotonic_ns()
            ns_to_ms = 1e-6
            assert self._perf_hook is not None
            self._perf_hook(
                {
                    "pre_step_ms": (t1 - t0) * ns_to_ms,
                    "physics_ms": (t2 - t1) * ns_to_ms,
                    "render_ms": (t3 - t2) * ns_to_ms,
                    "total_ms": (t3 - t0) * ns_to_ms,
                    "n_physics_steps": n_steps,
                    "frame_interval_ms": dt * 1000.0,
                }
            )

        return pixels

    async def next_frame(self) -> Any:
        import av

        if (
            self._mujoco is None
            or self._model is None
            or self._data is None
            or self._renderer is None
        ):
            raise RuntimeError("MuJoCo source not started.")

        loop = asyncio.get_running_loop()
        frame_rgb = await loop.run_in_executor(self._gl_executor, self._step_and_render)
        return av.VideoFrame.from_ndarray(frame_rgb, format="rgb24")
