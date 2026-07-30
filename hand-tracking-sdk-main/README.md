# Hand Tracking SDK（Python）

`hand-tracking-sdk` 是 Hand Tracking Streamer 的主机端 Python SDK，负责接收 UDP/TCP 遥测、校验文本协议、组装手/头/控制器帧，并提供坐标转换、日志、三维可视化、WebRTC 视频回传和仿真遥操作能力。当前包版本为 `1.2.0`，支持 Python `>=3.10`。

## 文档导航

- [工程总 README](../README.md)
- [Quest / Unity 包](../hand_tracking_streamer/README.md)
- [ROS 2 桥接包](../hand-tracking-sdk-ros2-main/README.md)
- [连接与协议](../CONNECTIONS.md)
- [视频回传示例](examples/video/README.md)
- [Sphinx API 文档入口](docs/index.rst)

## 包结构

| 路径 | 作用 |
|---|---|
| `src/hand_tracking_sdk/client.py` | 高层同步客户端、过滤、错误策略和统计 |
| `src/hand_tracking_sdk/transport.py` | UDP、TCP Server、TCP Client 传输层 |
| `src/hand_tracking_sdk/parser.py` | HTS CSV 行解析与严格字段校验 |
| `src/hand_tracking_sdk/frame.py` | 手、头、控制器数据组帧 |
| `src/hand_tracking_sdk/models.py` | 类型化数据模型 |
| `src/hand_tracking_sdk/convert.py` | Unity 左手系到右手系的转换 |
| `src/hand_tracking_sdk/visualization.py` | Rerun 三维可视化 |
| `src/hand_tracking_sdk/video/` | WebSocket 信令、WebRTC 发送和视频源适配 |
| `examples/` | 收帧、日志、抖动报告、可视化和视频/仿真示例 |
| `tests/` | 单元与集成测试 |

## 安装依赖

基础依赖为 `lark>=1.3.1` 和 `pyyaml>=6.0.3`。其他能力按需安装：

| extra | 主要依赖 | 用途 |
|---|---|---|
| `visualization` | `rerun-sdk` | 实时 3D 可视化 |
| `video` | `aiortc`、`av`、`numpy`、`opencv-python`、`websockets` | WebRTC 视频回传和摄像头输入 |
| `sim` | `mujoco`、`numpy`、`mink`、`daqp` | MuJoCo 仿真和机器人重定向 |

从 PyPI 安装：

```bash
python -m pip install hand-tracking-sdk
python -m pip install "hand-tracking-sdk[visualization]"
python -m pip install "hand-tracking-sdk[video,sim]"
```

从当前源码目录安装：

```bash
python -m pip install -e .
python -m pip install -e ".[visualization,video,sim]"
```

使用 uv 开发：

```bash
uv sync
# 同时安装全部可选依赖
uv sync --all-extras
```

## 快速运行

### 接收组装后的帧

默认示例作为 TCP Server 监听 `0.0.0.0:8000`：

```bash
uv run python examples/stream_frames.py \
  --transport tcp_server --host 0.0.0.0 --port 8000
```

接收 UDP：

```bash
uv run python examples/stream_frames.py \
  --transport udp --host 0.0.0.0 --port 9000
```

有线 TCP 使用前先执行：

```bash
adb reverse tcp:8000 tcp:8000
```

### 在代码中使用

```python
from hand_tracking_sdk import HTSClient, HTSClientConfig, StreamOutput, TransportMode

client = HTSClient(
    HTSClientConfig(
        transport_mode=TransportMode.TCP_SERVER,
        host="0.0.0.0",
        port=8000,
        output=StreamOutput.FRAMES,
    )
)

for frame in client.iter_events():
    print(frame)
```

`StreamOutput` 可选择原始包、组装帧或二者同时输出。`HTSClientConfig` 还支持左右手过滤、严格/容错解析、超时和 TCP 重连设置。

### Rerun 三维可视化

```bash
uv run python examples/visualize_rerun.py \
  --transport tcp_server --host 0.0.0.0 --port 8000 \
  --show-coordinate-frames --show-jitter
```

### 日志与时序分析

```bash
uv run python examples/log_to_jsonl.py --help
uv run python examples/jitter_report.py --help
```

## 数据模型

SDK 识别以下消息族：

- Wrist Pose：位置 `x, y, z` 与四元数 `qx, qy, qz, qw`。
- Hand Landmarks：21 个关键点，每点 3 个坐标，共 63 个浮点数。
- Head Pose：Quest 中心眼位姿。
- Controller Pose：Touch Pointer Pose 端点位姿。
- Controller Input：扳机、握把、摇杆轴以及 5 个按键状态。

`HandFrameAssembler` 将手腕与关键点关联为 `HandFrame`；`ControllerFrameAssembler` 将控制器位姿与输入关联为 `ControllerFrame`。启用 Quest Debug Info 后，组帧器会使用源帧序号关联同一快照。

按关节访问示例：

```python
from hand_tracking_sdk import JointName

x, y, z = frame.get_joint(JointName.INDEX_TIP)
index_finger = frame.get_finger("index")
```

## 坐标转换

原始 HTS 数据采用 Unity 左手坐标系。转换到 SDK 提供的右手 RFU（right-forward-up）坐标系：

```python
from hand_tracking_sdk import convert_hand_frame_unity_left_to_right

right_handed_frame = convert_hand_frame_unity_left_to_right(frame)
```

`hand_tracking_sdk.convert` 还提供位置、旋转矩阵和基变换等更细粒度函数。

## 视频回传与仿真

先安装 `video` extra，然后启动无硬件测试图：

```bash
uv sync --extra video
uv run python examples/video/test_pattern_video_host.py
```

主机默认监听：

- 遥测 TCP：`0.0.0.0:8000`
- WebSocket 信令：`0.0.0.0:8765`

Quest 应用使用 `ws://<主机局域网IP>:8765` 建立视频信令。MuJoCo 示例需同时安装 `video` 与 `sim` extra：

```bash
uv sync --extra video --extra sim
uv run python examples/video/shadow_hand_video_host.py --mocap-tcp-port 8000
```

更多参数和模型说明见 [examples/video/README.md](examples/video/README.md)。

## 测试与质量检查

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy src
```

构建文档：

```bash
uv pip install -r docs/requirements.txt
uv run sphinx-build -b html docs docs/_build/html
```

## 常见问题

- `Address already in use`：已有调试脚本、ROS 2 节点或 SDK 实例占用同一端口。
- TCP 一直等待：确保 Quest 选择 TCP 且目标 IP/端口正确；USB 模式需先配置 ADB reverse。
- 可视化模块导入失败：安装 `.[visualization]`。
- 视频模块缺依赖或编码失败：安装 `.[video]`，并确认系统具备 aiortc/PyAV 所需运行环境。
- 关节方向与机器人不一致：先使用 `convert_hand_frame_unity_left_to_right` 转换坐标系。

## 许可证

Apache-2.0，详见 [LICENSE](LICENSE)。
