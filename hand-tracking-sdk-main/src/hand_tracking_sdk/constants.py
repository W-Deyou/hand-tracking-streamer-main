"""Protocol constants for Hand Tracking Streamer packet parsing."""

WRIST_VALUE_COUNT = 7
CONTROLLER_POSE_VALUE_COUNT = 7
CONTROLLER_INPUT_VALUE_COUNT = 9
LANDMARK_COUNT = 21
LANDMARK_VALUE_COUNT = LANDMARK_COUNT * 3

STREAMED_JOINT_NAMES: tuple[str, ...] = (
    "Wrist",
    "ThumbMetacarpal",
    "ThumbProximal",
    "ThumbDistal",
    "ThumbTip",
    "IndexProximal",
    "IndexIntermediate",
    "IndexDistal",
    "IndexTip",
    "MiddleProximal",
    "MiddleIntermediate",
    "MiddleDistal",
    "MiddleTip",
    "RingProximal",
    "RingIntermediate",
    "RingDistal",
    "RingTip",
    "LittleProximal",
    "LittleIntermediate",
    "LittleDistal",
    "LittleTip",
)
