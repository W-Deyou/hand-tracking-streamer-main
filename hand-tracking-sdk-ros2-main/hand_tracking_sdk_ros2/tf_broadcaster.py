"""TF broadcasting helpers for wrist transforms."""

from __future__ import annotations

from builtin_interfaces.msg import Time
from hand_tracking_sdk import ControllerFrame, HandFrame, HandSide, HeadFrame
from tf2_ros import TransformBroadcaster

from .adapters import to_controller_transform, to_head_transform, to_wrist_transform


class WristTfPublisher:
    """Publish wrist transforms from SDK frames."""

    def __init__(
        self,
        broadcaster: TransformBroadcaster,
        *,
        enabled: bool,
        world_frame: str,
        left_wrist_frame: str,
        right_wrist_frame: str,
    ) -> None:
        """Create TF publisher with configured frame names."""
        self._broadcaster = broadcaster
        self._enabled = enabled
        self._world_frame = world_frame
        self._left_wrist_frame = left_wrist_frame
        self._right_wrist_frame = right_wrist_frame

    def publish(self, frame: HandFrame, stamp: Time) -> None:
        """Publish one transform if TF output is enabled."""
        if not self._enabled:
            return

        if frame.side == HandSide.LEFT:
            child_frame_id = self._left_wrist_frame
        else:
            child_frame_id = self._right_wrist_frame

        transform = to_wrist_transform(
            frame,
            stamp=stamp,
            world_frame=self._world_frame,
            child_frame_id=child_frame_id,
        )
        self._broadcaster.sendTransform(transform)


class ControllerTfPublisher:
    """Publish controller endpoint transforms independently from wrist TF."""

    def __init__(
        self,
        broadcaster: TransformBroadcaster,
        *,
        enabled: bool,
        world_frame: str,
        left_controller_frame: str,
        right_controller_frame: str,
    ) -> None:
        self._broadcaster = broadcaster
        self._enabled = enabled
        self._world_frame = world_frame
        self._left_controller_frame = left_controller_frame
        self._right_controller_frame = right_controller_frame

    def publish(self, frame: ControllerFrame, stamp: Time) -> None:
        """Publish one endpoint transform if TF output is enabled."""
        if not self._enabled:
            return
        child_frame_id = (
            self._left_controller_frame
            if frame.side == HandSide.LEFT
            else self._right_controller_frame
        )
        self._broadcaster.sendTransform(
            to_controller_transform(
                frame,
                stamp=stamp,
                world_frame=self._world_frame,
                child_frame_id=child_frame_id,
            )
        )


class HeadTfPublisher:
    """Publish the Quest center-eye transform independently from hand/controller TF."""

    def __init__(
        self,
        broadcaster: TransformBroadcaster,
        *,
        enabled: bool,
        world_frame: str,
        head_frame: str,
    ) -> None:
        self._broadcaster = broadcaster
        self._enabled = enabled
        self._world_frame = world_frame
        self._head_frame = head_frame

    def publish(self, frame: HeadFrame, stamp: Time) -> None:
        """Publish one world-to-head transform if TF output is enabled."""
        if not self._enabled:
            return
        self._broadcaster.sendTransform(
            to_head_transform(
                frame,
                stamp=stamp,
                world_frame=self._world_frame,
                child_frame_id=self._head_frame,
            )
        )
