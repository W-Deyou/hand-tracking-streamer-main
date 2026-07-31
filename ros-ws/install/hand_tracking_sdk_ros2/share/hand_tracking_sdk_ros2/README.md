# hand_tracking_sdk_ros2

`hand_tracking_sdk_ros2` 是 Hand Tracking Streamer 的 ROS 2 桥接包。它复用 `hand-tracking-sdk` 的传输、解析和组帧能力，将手部、头部与控制器数据发布为 ROS Topic、TF、RViz Marker 和 `/diagnostics`。当前包版本为 `0.3.1`。

## 文档导航

- [工程总 README](../README.md)
- [Quest / Unity 包](../hand_tracking_streamer/README.md)
- [Python SDK](../hand-tracking-sdk-main/README.md)
- [连接与协议](../CONNECTIONS.md)
- [默认参数](config/bridge.params.yaml)

## 兼容性与依赖

- ROS 2 Jazzy：主要测试版本。
- ROS 2 Humble：支持目标。
- ROS 2 Kilted：周期性冒烟测试目标。
- Python SDK：`hand-tracking-sdk>=1.2.0,<2.0.0`。
- ROS 依赖：`rclpy`、`geometry_msgs`、`sensor_msgs`、`visualization_msgs`、`tf2_ros`、`diagnostic_msgs`、`std_msgs`、`launch_ros`、`rviz2` 等，完整列表见 `package.xml`。

Python SDK 必须安装到 ROS 2 实际使用的同一个 Python 解释器中。

## 包结构

| 路径 | 作用 |
|---|---|
| `hand_tracking_sdk_ros2/bridge_node.py` | 节点编排、参数、发布器与诊断 |
| `hand_tracking_sdk_ros2/runtime.py` | 后台 SDK 接收线程和有界队列 |
| `hand_tracking_sdk_ros2/adapters.py` | SDK 帧到 ROS 消息的确定性映射 |
| `hand_tracking_sdk_ros2/markers.py` | 手部骨架 Marker 定义 |
| `hand_tracking_sdk_ros2/tf_broadcaster.py` | 手腕、控制器和头部 TF |
| `config/bridge.params.yaml` | 默认运行参数 |
| `launch/` | 桥接节点、桥接节点 + RViz 启动文件 |
| `rviz/hand_tracking.rviz` | 预置 RViz 视图 |
| `test/` | 参数、适配器、Launch 和诊断测试 |

## 构建

在已加载 ROS 2 环境的 Bash 中，从本工程根目录执行：

```bash
source /opt/ros/jazzy/setup.bash
python3 -m pip install -e ./hand-tracking-sdk-main

colcon build --base-paths ./hand-tracking-sdk-ros2-main \
  --symlink-install --packages-select hand_tracking_sdk_ros2
source install/setup.bash
```

如果已将本包放入标准工作空间的 `src/` 下，则在工作空间根目录执行：

```bash
python3 -m pip install "hand-tracking-sdk>=1.2.0,<2.0.0"
colcon build --symlink-install --packages-select hand_tracking_sdk_ros2
source install/setup.bash
```

## 运行

仅启动桥接节点：

```bash
ros2 launch hand_tracking_sdk_ros2 bridge.launch.py
```

启动桥接节点和 RViz：

```bash
ros2 launch hand_tracking_sdk_ros2 view_hands.launch.py
```

默认配置作为 TCP Server 监听 `0.0.0.0:8000`。有线 Quest 连接需在主机执行：

```bash
adb reverse tcp:8000 tcp:8000
```

`view_hands.launch.py` 会把 QoS 可靠性覆盖为 `reliable`，以兼容 RViz；普通实时运行默认采用低延迟的 `best_effort`。

自定义参数文件：

```bash
ros2 launch hand_tracking_sdk_ros2 bridge.launch.py \
  params_file:=/absolute/path/to/bridge.params.yaml
```

## 发布接口

| Topic | 消息类型 | 说明 |
|---|---|---|
| `/hands/left/wrist_pose` | `geometry_msgs/PoseStamped` | 左手腕位姿 |
| `/hands/right/wrist_pose` | `geometry_msgs/PoseStamped` | 右手腕位姿 |
| `/hands/left/landmarks` | `geometry_msgs/PoseArray` | 左手关键点；默认关闭 |
| `/hands/right/landmarks` | `geometry_msgs/PoseArray` | 右手关键点；默认关闭 |
| `/hands/left/markers` | `visualization_msgs/MarkerArray` | 左手 RViz 骨架 |
| `/hands/right/markers` | `visualization_msgs/MarkerArray` | 右手 RViz 骨架 |
| `/hands/joint_names` | `std_msgs/String` | 逗号分隔的标准关节顺序 |
| `/controllers/left/pose` | `geometry_msgs/PoseStamped` | 左控制器 Pointer Pose |
| `/controllers/right/pose` | `geometry_msgs/PoseStamped` | 右控制器 Pointer Pose |
| `/controllers/left/input` | `sensor_msgs/Joy` | 左控制器轴与按键 |
| `/controllers/right/input` | `sensor_msgs/Joy` | 右控制器轴与按键 |
| `/head/pose` | `geometry_msgs/PoseStamped` | Quest 中心眼位姿 |
| `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 接收与丢帧诊断 |

控制器 `Joy.axes` 固定为 `trigger, grip, stick_x, stick_y`；`Joy.buttons` 固定为 `primary, secondary, trigger_button, grip_button, stick_click`。

默认 TF 树：

```text
world
├── left_wrist
├── right_wrist
├── left_controller_endpoint
├── right_controller_endpoint
└── head
```

## 主要参数

完整配置见 [config/bridge.params.yaml](config/bridge.params.yaml)。

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `transport_mode` | `tcp_server` | `udp`、`tcp_server` 或 `tcp_client` |
| `host` | `0.0.0.0` | 绑定或连接地址 |
| `port` | `8000` | 遥测端口 |
| `timeout_s` | `1.0` | Socket 超时（秒） |
| `reconnect_delay_s` | `0.25` | TCP Client 重连间隔（秒） |
| `world_frame` | `world` | 世界坐标系名称 |
| `landmarks_are_wrist_relative` | `true` | 发布前将关键点变换到世界坐标 |
| `qos_reliability` | `best_effort` | `best_effort` 或 `reliable` |
| `queue_size` | `256` | 接收帧队列容量，满时丢弃最旧帧 |
| `enable_tf` | `true` | 发布 TF |
| `enable_pose_array` | `false` | 发布关键点 PoseArray |
| `enable_markers` | `true` | 发布手骨架 Marker |
| `enable_controller_topics` | `true` | 发布控制器 Topic/TF |
| `enable_head_topics` | `true` | 发布头部 Topic/TF |
| `enable_diagnostics` | `true` | 发布诊断信息 |

桥接包会将 Unity 左手坐标输入映射为 ROS 常用的 FLU 坐标表达。

## 验证与测试

启动后在另一个已加载工作空间的终端检查：

```bash
ros2 topic list | grep -E 'hands|controllers|head'
ros2 topic hz /hands/left/markers
ros2 topic echo /hands/joint_names --once
ros2 run tf2_ros tf2_echo world left_wrist
ros2 topic echo /diagnostics --once
```

运行包测试：

```bash
colcon test --packages-select hand_tracking_sdk_ros2 --event-handlers console_direct+
colcon test-result --verbose --all
```

## 常见问题

- 启动后只有 `/rosout`：检查进程是否仍在运行，以及 Quest 和 `transport_mode/host/port` 是否匹配。
- `ModuleNotFoundError: hand_tracking_sdk`：用 ROS 所使用的 `python3` 重新安装 Python SDK。
- RViz 无 Marker：用 `view_hands.launch.py` 启动，并确认 `/hands/*/markers` 有消息。
- 实时画面延迟：普通运行使用 `best_effort`，保持 `enable_pose_array: false`，优先消费 Marker 或业务所需 Topic。
- 缺少 TF：确认 `enable_tf: true`，并检查所选 Quest 模式是否确实产生对应的手、头或控制器帧。

## 许可证

Apache-2.0，详见 [LICENSE](LICENSE)。
