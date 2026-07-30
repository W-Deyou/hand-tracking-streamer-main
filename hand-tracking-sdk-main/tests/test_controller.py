from collections.abc import Iterator

import pytest

from hand_tracking_sdk import (
    ControllerFrame,
    ControllerFrameAssembler,
    ControllerInputPacket,
    ControllerInputState,
    ControllerPose,
    ControllerPosePacket,
    ErrorPolicy,
    HandFilter,
    HandSide,
    HTSClient,
    HTSClientConfig,
    PacketType,
    ParseError,
    StreamOutput,
    convert_controller_frame_unity_left_to_right,
    parse_line,
)

POSE = "Left controller pose:, 1, 2, 3, 0, 0, 0, 1"
INPUT = "Left controller input:, 0.25, 0.75, -0.5, 0.5, 1, 0, 1, 0, 1"


class FakeLineReceiver:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> "FakeLineReceiver":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines


def _client(config: HTSClientConfig, lines: list[str]) -> HTSClient:
    return HTSClient(config, receiver_factory=lambda _: FakeLineReceiver(lines))


def test_parse_controller_pose_and_input() -> None:
    pose = parse_line(POSE)
    inputs = parse_line(INPUT)

    assert isinstance(pose, ControllerPosePacket)
    assert pose.kind == PacketType.CONTROLLER_POSE
    assert pose.data == ControllerPose(1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0)
    assert isinstance(inputs, ControllerInputPacket)
    assert inputs.kind == PacketType.CONTROLLER_INPUT
    assert inputs.data.primary is True
    assert inputs.data.secondary is False
    assert inputs.data.stick_click is True


def test_parse_controller_debug_metadata() -> None:
    packet = parse_line(
        "Right controller pose | f = 9 | t = 1234:, 1, 2, 3, 0, 0, 0, 1"
    )
    assert isinstance(packet, ControllerPosePacket)
    assert packet.side == HandSide.RIGHT
    assert packet.debug is not None
    assert packet.debug.source_frame_seq == 9
    assert packet.debug.source_ts_ns == 1234


@pytest.mark.parametrize(
    "line",
    [
        "Left controller pose:, 1, 2, 3",
        "Left controller input:, 0, 0, 0, 0, 1, 0",
        "Left controller input:, 1.1, 0, 0, 0, 1, 0, 0, 0, 0",
        "Left controller input:, 0, 0, -1.1, 0, 1, 0, 0, 0, 0",
        "Left controller input:, 0, 0, 0, 0, 2, 0, 0, 0, 0",
        "Head controller pose:, 1, 2, 3, 0, 0, 0, 1",
    ],
)
def test_invalid_controller_packets_raise(line: str) -> None:
    with pytest.raises(ParseError):
        parse_line(line)


def test_controller_frame_requires_matching_pair() -> None:
    assembler = ControllerFrameAssembler(include_wall_time=False)
    assert assembler.push_line(POSE, recv_ts_ns=10) is None
    frame = assembler.push_line(INPUT, recv_ts_ns=11)

    assert isinstance(frame, ControllerFrame)
    assert frame.frame_id == "hts_left_controller_endpoint"
    assert frame.sequence_id == 0
    assert frame.pose_recv_ts_ns == 10
    assert frame.input_recv_ts_ns == 11


def test_controller_frame_debug_sequence_must_match() -> None:
    assembler = ControllerFrameAssembler(include_wall_time=False)
    assert (
        assembler.push_line(
            "Left controller pose | f = 1 | t = 100:, 1, 2, 3, 0, 0, 0, 1",
            recv_ts_ns=10,
        )
        is None
    )
    assert (
        assembler.push_line(
            "Left controller input | f = 2 | t = 200:, 0, 0, 0, 0, 0, 0, 0, 0, 0",
            recv_ts_ns=20,
        )
        is None
    )
    frame = assembler.push_line(
        "Left controller pose | f = 2 | t = 200:, 4, 5, 6, 0, 0, 0, 1",
        recv_ts_ns=30,
    )
    assert isinstance(frame, ControllerFrame)
    assert frame.source_frame_seq == 2
    assert frame.source_ts_ns == 200


def test_controller_frame_requires_both_components_to_advance() -> None:
    assembler = ControllerFrameAssembler(include_wall_time=False)
    assert assembler.push_line(POSE, recv_ts_ns=10) is None
    assert isinstance(assembler.push_line(INPUT, recv_ts_ns=11), ControllerFrame)

    assert assembler.push_line(POSE, recv_ts_ns=20) is None
    next_frame = assembler.push_line(INPUT, recv_ts_ns=21)
    assert isinstance(next_frame, ControllerFrame)
    assert next_frame.sequence_id == 1
    assert next_frame.pose_recv_ts_ns == 20
    assert next_frame.input_recv_ts_ns == 21


def test_controller_frame_does_not_mix_debug_and_plain_components() -> None:
    assembler = ControllerFrameAssembler(include_wall_time=False)
    assert assembler.push_line(POSE, recv_ts_ns=10) is None
    assert (
        assembler.push_line(
            "Left controller input | f = 1 | t = 100:, 0, 0, 0, 0, 0, 0, 0, 0, 0",
            recv_ts_ns=11,
        )
        is None
    )


def test_controller_frame_dict_roundtrip_and_conversion() -> None:
    frame = ControllerFrame(
        side=HandSide.RIGHT,
        frame_id="right_endpoint",
        pose=ControllerPose(1, 2, 3, 0, 0, 0, 1),
        input=ControllerInputState(0.1, 0.2, 0.3, -0.4, True, False, True, False, True),
        sequence_id=3,
        recv_ts_ns=4,
        recv_time_unix_ns=5,
        source_ts_ns=6,
        source_frame_seq=7,
        pose_recv_ts_ns=8,
        input_recv_ts_ns=9,
    )
    restored = ControllerFrame.from_dict(frame.to_dict())
    converted = convert_controller_frame_unity_left_to_right(restored)

    assert restored == frame
    assert converted.pose.y == -2
    assert converted.input == frame.input
    assert converted.source_frame_seq == 7


def test_client_controller_packet_frame_and_filter_paths() -> None:
    both_events = list(
        _client(HTSClientConfig(output=StreamOutput.BOTH), [POSE, INPUT]).iter_events()
    )
    assert len(both_events) == 3
    assert isinstance(both_events[0], ControllerPosePacket)
    assert isinstance(both_events[1], ControllerInputPacket)
    assert isinstance(both_events[2], ControllerFrame)

    filtered = list(
        _client(
            HTSClientConfig(output=StreamOutput.FRAMES, hand_filter=HandFilter.RIGHT),
            [POSE, INPUT],
        ).iter_events()
    )
    assert filtered == []


def test_tolerant_policy_skips_bad_controller_line() -> None:
    events = list(
        _client(
            HTSClientConfig(output=StreamOutput.PACKETS, error_policy=ErrorPolicy.TOLERANT),
            ["Left controller input:, bad", POSE],
        ).iter_events()
    )
    assert len(events) == 1
    assert isinstance(events[0], ControllerPosePacket)
