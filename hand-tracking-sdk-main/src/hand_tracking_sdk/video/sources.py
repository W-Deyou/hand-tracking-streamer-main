"""Video source adapters for host-side WebRTC transmission."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import monotonic, monotonic_ns
from typing import Any


@dataclass(frozen=True, slots=True)
class VideoFormat:
    """Video format contract produced by a source adapter."""

    width: int
    height: int
    fps: int


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


class OrbbecUvcSourceAdapter(VideoSourceAdapter):
    """Orbbec Gemini UVC RGB source with timed V4L2 node discovery.

    Orbbec devices expose multiple `/dev/videoN` nodes (RGB/IR/depth/metadata).
    IR often appears as HxWx3 with near-identical channels (looks black-and-white),
    so discovery scores colorfulness and prefers a true RGB node.
    """

    # Gemini 336 on this host: IR often lands on early indexes; RGB nearer to 6.
    _DEFAULT_CANDIDATES = (6, 4, 2, 0, 1, 3, 5, 7)
    _PROBE_TIMEOUT_S = 3.0
    # Mean |R-G|+|G-B|+|R-B|; IR duplicated to 3ch is typically ~0.
    _MIN_COLOR_SCORE = 3.0

    def __init__(
        self,
        *,
        device_index: int = -1,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        log_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._format = VideoFormat(width=width, height=height, fps=fps)
        self._preferred_index = device_index
        self._log_hook = log_hook
        self._capture: Any = None
        self._selected_index: int | None = None

    @property
    def selected_device_index(self) -> int | None:
        return self._selected_index

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

        probe_log: list[str] = []
        best: tuple[float, int, Any, str] | None = None  # score, index, capture, detail

        for index in self._candidate_indices():
            # Fresh executor per candidate so a hung open does not block the next probe.
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(self._probe_index, index)
                ok, detail, score, capture = future.result(timeout=self._PROBE_TIMEOUT_S)
            except TimeoutError:
                probe_log.append(f"{index}:timeout>{self._PROBE_TIMEOUT_S:.0f}s")
                executor.shutdown(wait=False, cancel_futures=True)
                continue
            except Exception as exc:
                probe_log.append(f"{index}:error={exc}")
                executor.shutdown(wait=False, cancel_futures=True)
                continue
            executor.shutdown(wait=False, cancel_futures=True)
            if not ok or capture is None:
                probe_log.append(f"{index}:{detail}")
                continue

            probe_log.append(f"{index}:ok({detail})")
            if best is None or score > best[0]:
                if best is not None:
                    best[2].release()
                best = (score, index, capture, detail)
            else:
                capture.release()

            # Preferred index that already looks like RGB: take it immediately.
            if self._preferred_index >= 0 and index == self._preferred_index:
                break

        if best is None:
            raise RuntimeError(
                "Failed to find Orbbec UVC RGB node (skipped IR/gray nodes). "
                f"Probe results: [{', '.join(probe_log) or 'none'}]. "
                "Try --webcam-index N with a known color node (often 6 on Gemini 336)."
            )

        score, index, capture, detail = best
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._format.width))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._format.height))
        capture.set(cv2.CAP_PROP_FPS, float(self._format.fps))
        self._selected_index = index
        if self._log_hook is not None:
            self._log_hook(
                f"orbbec UVC RGB selected index={index} detail={detail} "
                f"probed=[{', '.join(probe_log)}]"
            )
        return capture

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._capture = await loop.run_in_executor(None, self._open_selected)

    async def stop(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._selected_index = None

    def get_format(self) -> VideoFormat:
        return self._format

    async def next_frame(self) -> Any:
        import av
        import cv2

        if self._capture is None:
            raise RuntimeError("Orbbec UVC source not started.")

        ok, frame_bgr = self._capture.read()
        if not ok:
            raise RuntimeError(
                f"Failed to read Orbbec frame from index {self._selected_index}."
            )
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return av.VideoFrame.from_ndarray(frame_rgb, format="rgb24")


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
            self._perf_hook({
                "pre_step_ms": (t1 - t0) * ns_to_ms,
                "physics_ms": (t2 - t1) * ns_to_ms,
                "render_ms": (t3 - t2) * ns_to_ms,
                "total_ms": (t3 - t0) * ns_to_ms,
                "n_physics_steps": n_steps,
                "frame_interval_ms": dt * 1000.0,
            })

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
