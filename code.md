# 手柄末端 6DoF、按键与坐标轴串流实现方案

## 1. 目标

在不改变现有手部、腕部、头部姿态和视频串流框架的前提下，增加一种与手部映射互斥的“手柄输入模式”。手柄模式提供：

- 左、右或双手柄末端 6DoF 姿态。
- 扳机、握把、摇杆及主要按键状态。
- Quest 场景内的手柄末端 RGB 坐标轴。
- 与现有 UDP、无线 TCP、ADB TCP、调试帧头和断线处理一致的行为。

本功能需要三个目录协同修改，但保持各自现有职责：

- `hand_tracking_streamer`：Quest 端采集、显示和发送原始消息。
- `hand-tracking-sdk-main`：UDP/TCP 行协议解析、类型化、组帧和 Python API。
- `hand-tracking-sdk-ros2-main`：消费 Python SDK 事件并发布 ROS 2 Topic/TF。

这里的“手柄末端”统一定义为 Meta Interaction SDK 的 `IController.TryGetPointerPose()`。坐标轴原点、坐标轴旋转和网络导出的 6DoF 必须来自同一次 Pointer Pose 采样。

## 2. 不可破坏的兼容约束

1. 默认仍为现有手部模式。升级后未操作新选项的用户得到与当前版本相同的行为。
2. 不修改以下旧数据包的标签、字段顺序、坐标系和数值精度：
   - `Left/Right wrist:`
   - `Left/Right landmarks:`
   - `Head pose:`
3. 不替换现有 `HandLandmarkStreamer`、`HeadPoseStreamer`、`VideoStreamManager` 或 Meta Hand/Controller Building Blocks。
4. 不改变现有协议下拉框和侧别下拉框的数值含义：`0=Both`、`1=Left`、`2=Right`。
5. 不修改原有 PlayerPrefs 键。新增模式配置必须使用新键，并在该键不存在时回退到手部模式。
6. 手部和手柄遥测严格互斥；头部姿态与 PC→Quest 视频仍可和任一输入模式同时启用。
7. 左手柄 Menu 键继续作为停止串流按钮，不作为遥测字段；右侧 Meta 系统键由系统保留，也不导出。
8. 新增组件配置异常时，只阻止手柄模式启动，不得影响默认手部模式。
9. `controller pose` 是独立语义，任何模式下都不得改名或兼容性伪装为 `wrist`；接收端必须能够明确判断数据来自手部还是手柄。
10. Python SDK 现有 `WristPose`、`WristPacket`、`HandFrame`、`HandFrameAssembler` 及其导入路径保持兼容。
11. ROS 2 现有 `/hands/**` Topic、wrist TF、参数名、默认值和消息类型保持兼容；Controller 使用新的 `/controllers/**` 命名空间。

## 3. 总体设计

### 3.1 输入模式

在 `AppManager` 中新增：

```csharp
public enum InputMappingMode
{
    Hands = 0,
    Controllers = 1,
}

public InputMappingMode SelectedInputMode { get; private set; }
public bool IsHandMode => SelectedInputMode == InputMappingMode.Hands;
public bool IsControllerMode => SelectedInputMode == InputMappingMode.Controllers;
```

新增启动菜单 Toggle：

- 名称：`Controller Input`。
- 关闭：手部模式，默认值。
- 开启：手柄模式。
- PlayerPrefs 新键：`SavedInputMode`。
- 串流开始后锁定该 Toggle；停止后恢复交互，禁止在连接存活期间热切换发送器。

复用现有侧别下拉框：

- 手部模式显示 `Both Hands / Left Hand / Right Hand`。
- 手柄模式显示 `Both Controllers / Left Controller / Right Controller`。
- 仅替换显示文字，不改变下拉框 value。

现有 `Visualization` Toggle 复用为可视化总开关：

- 手部模式：保持当前手部关键点显示逻辑。
- 手柄模式：控制末端 RGB 坐标轴显示。
- 切换模式不能覆盖用户已保存的 Visualization 选择。

### 3.2 组件边界

采用旁路新增，避免把手柄分支塞入 `HandLandmarkStreamer`：

- `HandLandmarkStreamer`：仅增加一条手部模式门控，其余处理保持原样。
- `ControllerInputStreamer`：新增组件，每侧一个实例，负责采样、组包、HUD 和网络连接。
- `ControllerAxisVisualizer`：新增组件，每侧一个实例，只负责显示已采样的 Pointer Pose。
- `AppManager`：负责模式、UI、持久化和启动前配置验证，不负责采样或组包。

左右 `ControllerInputStreamer` 应绑定场景中已有的 `LeftController`、`RightController` 上的 `IController`，不新建或替换 Controller Tracking Building Block。

### 3.3 分层复用原则

采用“上层分开、底层复用”的结构：

```text
Hands 模式
  HandLandmarkStreamer
    ├─ Left/Right wrist
    └─ Left/Right landmarks

Controllers 模式
  ControllerInputStreamer
    ├─ Left/Right controller pose
    └─ Left/Right controller input

两种模式共同使用
  ├─ AppManager 中的 IP、端口和 UDP/TCP 配置
  ├─ 相同的帧号、时间戳和数值格式约定
  └─ 可抽取的 Pose 格式化及网络清理辅助方法
```

具体边界：

- **必须分开**：采集组件、有效性判断、消息标签、消息字段和 HUD 语义。
- **可以复用**：7-float Pose 序列化、StringBuilder 格式化、目标 IP/端口、UDP/TCP 建连参数、断线通知和资源清理模式。
- **不得合并**：不要让 `ControllerInputStreamer` 继承或调用 `HandLandmarkStreamer`，也不要在同一个 Streamer 中堆叠 Hands/Controllers 大量分支。
- **不得冒充**：Pointer Pose 即使也是 `x,y,z,qx,qy,qz,qw`，仍只能使用 `controller pose` 标签，不能发送为 `wrist`。
- **不新增端口配置**：手柄模式继续发送到用户在原窗口填写的目标 IP 和端口，不增加第二套端口、ADB reverse 或防火墙设置。
- **不要求共用同一个 socket 实例**：保持当前“每个遥测源自行管理连接”的框架；复用的是目标配置和底层规则。UDP 可由每侧独立 client 发往同一端点，TCP 延续每侧独立连接。

## 4. 手柄采样与发送

### 4.1 生命周期

`ControllerInputStreamer` 参考现有 `HandLandmarkStreamer` 的生命周期：

1. `Start()` 获取同对象或序列化引用中的 `IController`，订阅 `WhenUpdated`。
2. 仅当以下条件全部满足时工作：
   - `AppManager.Instance != null`。
   - `AppManager.Instance.isStreaming == true`。
   - `AppManager.Instance.IsControllerMode == true`。
   - 当前侧别包含在 `SelectedHandMode` 中。
3. 首次有效更新时按现有配置建立独立 UDP/TCP 连接。
4. 使用 `0.01 s` 节流，目标约 100 Hz。
5. 停止串流、切回非手柄模式、网络失败或对象销毁时关闭连接并取消订阅。

手部模式下禁止初始化手柄 socket。手柄模式下 `HandLandmarkStreamer` 应在模式检查处直接返回，并在已有连接存在时执行原有 `Disconnect()`。

### 4.2 有效性检查

每帧发送前依次检查：

```text
IController.IsConnected
IController.TryGetPointerPose(out Pose pointerPose)
```

任一检查失败时：

- 本帧不发送该侧姿态或按键。
- 隐藏该侧末端坐标轴。
- 不把短暂跟踪丢失当作网络断线。
- 跟踪恢复后自动继续，无需重启串流。

### 4.3 同步采样

一次有效更新只读取一次 `TryGetPointerPose()`，然后把同一个 `Pose` 同时交给：

- 网络姿态组包。
- `ControllerAxisVisualizer.SetPose()`。
- HUD 调试显示。

姿态和输入快照使用相同 `frameId` 和 `sendTimestampNs`，避免可视化、姿态包和按键包来自不同帧。

## 5. 网络协议

### 5.1 普通模式

每侧一次更新包含两行：

```text
Left controller pose:, x, y, z, qx, qy, qz, qw
Left controller input:, trigger, grip, stick_x, stick_y, primary, secondary, trigger_button, grip_button, stick_click
```

右侧将 `Left` 替换为 `Right`。

字段定义和固定顺序：

| 字段 | 类型/范围 | 左手柄 | 右手柄 |
|---|---:|---|---|
| `trigger` | float, 0–1 | 食指扳机 | 食指扳机 |
| `grip` | float, 0–1 | 握把 | 握把 |
| `stick_x` | float, -1–1 | 左摇杆 X | 右摇杆 X |
| `stick_y` | float, -1–1 | 左摇杆 Y | 右摇杆 Y |
| `primary` | 0/1 | X | A |
| `secondary` | 0/1 | Y | B |
| `trigger_button` | 0/1 | SDK 扳机按下判定 | SDK 扳机按下判定 |
| `grip_button` | 0/1 | SDK 握把按下判定 | SDK 握把按下判定 |
| `stick_click` | 0/1 | 左摇杆按下 | 右摇杆按下 |

姿态只复用腕部包的数值编码方式：位置 `F4`，四元数 `F3`；这不代表复用 `wrist` 消息类型。模拟量建议使用 `F4`，数字状态严格输出 `0` 或 `1`。

### 5.2 调试模式

沿用当前帧头语法：

```text
Left controller pose | f = 123 | t = 456789:, x, y, z, qx, qy, qz, qw
Left controller input | f = 123 | t = 456789:, trigger, grip, stick_x, stick_y, primary, secondary, trigger_button, grip_button, stick_click
```

- 同侧两行共用 `frameId` 和单调纳秒时间戳。
- 左右手柄各自维护帧计数器，与现有左右手部行为一致。

### 5.3 传输边界

- UDP：同侧 pose 和 input 两行放在同一个 datagram 中，不追加额外 datagram。
- TCP：同侧两行作为一个字符串写入，并在末尾追加 `\n`，保持现有接收方式。
- 继续使用 `TCP_NODELAY`、现有超时、断线回调和 `AppManager.HandleDisconnection()`。
- 不在同一模式下发送旧 hand 包与新 controller 包。
- Hands 和 Controllers 复用同一目标 IP/端口配置，但由于模式互斥，不会在同一运行会话中竞争发送两套输入遥测。
- 不为 controller pose 与 controller input 分配两个端口；两行属于同侧、同帧的一次逻辑快照。

### 5.4 接收端接口

接收端应先按消息标签解析为不同领域类型，再在应用层选择是否映射为统一末端控制量：

```text
Left/Right wrist           -> HandWristPose
Left/Right landmarks       -> HandLandmarks
Left/Right controller pose -> ControllerEndpointPose
Left/Right controller input-> ControllerInputState
```

如果机器人控制层需要统一入口，可在解析完成后做显式映射：

```text
Hands 模式       HandWristPose           -> EndpointPose
Controllers 模式 ControllerEndpointPose  -> EndpointPose
```

统一发生在业务层，不得通过把网络标签改成 `wrist` 来实现。旧接收器遇到未知的 `controller` 标签时应忽略或报告“不支持的消息类型”，不能误解析成手部数据。

## 6. 末端坐标轴

### 6.1 坐标定义

坐标轴根对象的世界姿态直接设置为 Pointer Pose：

```csharp
axisRoot.SetPositionAndRotation(pointerPose.position, pointerPose.rotation);
```

- 原点：手柄 Pointer Pose 末端点。
- +X：红色。
- +Y：绿色。
- +Z：蓝色。
- 默认轴长：`0.08 m`。
- 箭头应明确表达正方向，不绘制负半轴。

### 6.2 渲染要求

- 三轴由细圆柱和末端圆锥组成，或使用等价的低开销 Mesh。
- 所有 Renderer 禁用 Cast/Receive Shadows。
- 不添加 Collider、Rigidbody、RayInteractor 或事件组件。
- 使用共享材质或实例初始化时缓存材质，不在 `Update()` 中创建对象或材质。
- 坐标轴对象在场景加载时创建/绑定，运行中仅更新 Transform 与 Active 状态。

### 6.3 显隐规则

仅当以下条件全部满足时显示某侧坐标轴：

```text
正在串流
当前为 Controllers 模式
Visualization 已开启
当前侧别已选择
手柄已连接且 Pointer Pose 有效
```

停止串流、取消 Visualization、选择另一侧或跟踪无效时立即隐藏。

## 7. AppManager 集成顺序

启动串流时：

1. 读取 IP、端口、协议和侧别，保持现有验证流程。
2. 从 Toggle 固化 `SelectedInputMode`。
3. 若为 Controllers，验证左右手柄发送器引用；只验证当前选中的侧别。
4. 保存 `SavedInputMode`，锁定模式 UI。
5. 设置 `isStreaming = true`，由对应发送器按原有事件驱动方式建立连接。
6. 视频勾选时继续执行现有 WebRTC 启动流程。

停止或断线时：

1. 先设置 `isStreaming = false`。
2. 手部和手柄发送器根据原有状态自行关闭 socket。
3. 隐藏全部末端坐标轴。
4. 保持现有视频停止、菜单恢复、射线恢复和错误提示顺序。
5. 解锁模式 UI。

不要在 `AppManager` 内直接创建或持有 UDP/TCP 客户端，避免改变现有每个遥测源独立连接的框架。`AppManager` 只向当前模式的发送器提供同一套目标 IP、端口和协议配置。

## 8. 场景接线

在现有 `Scene.unity` 中执行以下增量配置：

1. 在启动菜单合适行加入 `Controller Input` Toggle，并绑定到 `AppManager` 新字段。
2. 在已有 `LeftController` 和 `RightController` 对象上分别添加 `ControllerInputStreamer`。
3. 分别配置 Side 为 Left/Right，并引用现有 `IController`。
4. 为每侧建立一个坐标轴根对象，添加 `ControllerAxisVisualizer`，初始为隐藏。
5. 将发送器与对应 Visualizer 连接。
6. 不删除、不重建已有 Hand Tracking、Controller Tracking、Controller Interactions 或视频对象。

如果继续使用 `QuestControllerSetup` 自动配置脚本，应只扩展其幂等检查与新增组件接线，不改变它安装现有 Controller Building Block 和注册菜单射线的职责。

## 9. HUD 与错误处理

- 手柄模式 HUD 显示当前侧 Pointer Pose 位置、扳机、握把和摇杆摘要。
- Debug Info 关闭时避免逐帧输出额外网络调试日志。
- 找不到 `IController`、Visualizer 或场景引用时，在用户选择 Controllers 并点击 Start 后给出明确错误；Hands 模式不检查这些引用。
- 网络发送异常继续通过 `HandleDisconnection()` 返回菜单。
- 短暂 `IsConnected=false` 或 Pointer Pose 无效只视为跟踪状态，不停止整个网络会话。

## 10. 文档更新

在实现完成后同步更新 `CONNECTIONS.md`：

- 增加 Controllers 模式说明。
- 记录 Pointer Pose 是末端姿态及 Unity 世界坐标。
- 给出普通和调试包示例。
- 固定说明 input 字段顺序、范围及左右按键映射。
- 提醒左 Menu 键用于停止串流，不会导出。

`scripts/sockets.py` 是通用 UTF-8 行接收器，无需为新标签修改。结构化消息处理由 `hand-tracking-sdk-main` 和 `hand-tracking-sdk-ros2-main` 按下述方案扩展。

## 11. Python SDK 消息接口方案

目录：`hand-tracking-sdk-main`。保持现有 transport 层和 `HTSClient` 使用方式不变，通过并行类型扩展支持 Controller。

### 11.1 类型模型

在 `models.py` 增加独立类型，不继承或别名到 Wrist 类型：

```python
class PacketType(StrEnum):
    WRIST = "wrist"                    # 保留
    LANDMARKS = "landmarks"            # 保留
    POSE = "pose"                      # 保留 Head pose
    CONTROLLER_POSE = "controller pose"
    CONTROLLER_INPUT = "controller input"

@dataclass(frozen=True, slots=True)
class ControllerPose:
    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float

@dataclass(frozen=True, slots=True)
class ControllerInputState:
    trigger: float
    grip: float
    stick_x: float
    stick_y: float
    primary: bool
    secondary: bool
    trigger_button: bool
    grip_button: bool
    stick_click: bool

@dataclass(frozen=True, slots=True)
class ControllerPosePacket:
    side: HandSide
    kind: PacketType
    data: ControllerPose
    debug: PacketDebugInfo | None = None

@dataclass(frozen=True, slots=True)
class ControllerInputPacket:
    side: HandSide
    kind: PacketType
    data: ControllerInputState
    debug: PacketDebugInfo | None = None
```

- `ParsedPacket` 扩展为旧三种 Packet 加上述两种 Packet 的 union。
- `ControllerPose` 和 `ControllerInputState` 实现与现有模型一致的 `to_dict()`/`from_dict()`。
- input 的五个数字字段在解析后转换为 `bool`；只接受数值 `0` 或 `1`，其他值视为协议错误。
- `trigger/grip` 校验 `0–1`，`stick_x/stick_y` 校验 `-1–1`；允许浮点边界误差时只做极小 epsilon 容差，不静默裁剪数据。
- 在 `constants.py` 增加 `CONTROLLER_POSE_VALUE_COUNT = 7` 和 `CONTROLLER_INPUT_VALUE_COUNT = 9`，不复用名为 `WRIST_VALUE_COUNT` 的常量。

### 11.2 解析接口

扩展 `parser.parse_line()`，保留全部旧标签路径，并显式接受：

```text
Left controller pose
Right controller pose
Left controller input
Right controller input
```

解析规则：

- `Head pose` 继续是两段标签；`Left/Right controller pose/input` 是三段标签，不能再用当前固定 `len(parts) == 2` 的判断覆盖所有类型。
- Controller Pose 必须正好 7 个数值，Controller Input 必须正好 9 个数值。
- 普通帧头与 `| f = ... | t = ...` 调试帧头均生成相同 Packet 类型。
- 未支持的标签、错误侧别、缺字段、多字段、非数字、非法 bool 和越界模拟量继续抛出 `ParseError`。
- `ErrorPolicy.STRICT/TOLERANT` 行为保持原样；旧消息在 strict 模式下的结果不得变化。

### 11.3 Controller 组帧

在 `frame.py` 新增：

```python
@dataclass(frozen=True, slots=True)
class ControllerFrame:
    side: HandSide
    frame_id: str
    pose: ControllerPose
    input: ControllerInputState
    sequence_id: int
    recv_ts_ns: int
    recv_time_unix_ns: int | None
    source_ts_ns: int | None
    pose_recv_ts_ns: int
    input_recv_ts_ns: int
    source_frame_seq: int | None = None
```

- 新增独立 `ControllerFrameAssembler`，不得把 controller 状态字段加入现有 `_SideAssemblyState`。
- 每侧同时收到 pose 和 input 后才发出 `ControllerFrame`。
- 有调试元数据时，优先按同侧相同 `source_frame_seq` 配对；不匹配的旧分量不得和新帧混合。
- 无调试元数据时，沿用 Hand assembler 的“每侧最新有效分量”策略；因为 Quest 将两行放在同一 datagram/写入中，可按到达顺序完成一帧。
- 默认 frame id：`hts_left_controller_endpoint`、`hts_right_controller_endpoint`。
- `AssembledFrame` 扩展为 `HandFrame | HeadFrame | ControllerFrame`；旧类名称和构造参数不变。

### 11.4 HTSClient 与公开 API

- `HTSClient` 同时持有原 `HandFrameAssembler` 和新增 `ControllerFrameAssembler`，按 Packet 实际类型路由。
- `StreamOutput.PACKETS` 输出新增 Controller Packet；`FRAMES` 输出新增 `ControllerFrame`；`BOTH` 继续先 Packet 后对应 Frame。
- `HandFilter.LEFT/RIGHT/BOTH` 继续按 side 过滤 Hand 与 Controller，不新增破坏性配置项。
- 只接收旧 wrist/landmarks/head 流时，事件类型、顺序、统计计数和异常策略必须与当前版本一致。
- `StreamEvent` union 和包根目录 `__init__.__all__` 导出所有新增公开类型。
- `convert.py` 增加 `convert_controller_pose_unity_left_to_right()` 和 `convert_controller_frame_unity_left_to_right()`；input 数值原样保留。
- Rerun 可视化作为同版本扩展：ControllerFrame 显示末端坐标轴，input 作为标量记录；不得改变 HandFrame 的现有可视化路径。
- Python SDK 版本从 `1.1.0` 升为 `1.2.0`，属于向后兼容的功能性 minor release。

## 12. ROS 2 消息接口方案

目录：`hand-tracking-sdk-ros2-main`。ROS 2 层只消费 Python SDK 的 `ControllerFrame`，不得重复解析 CSV 文本。

### 12.1 Topic 与消息类型

新增相对 Topic（展示时以根命名空间表示）：

```text
/controllers/left/pose   geometry_msgs/msg/PoseStamped
/controllers/right/pose  geometry_msgs/msg/PoseStamped
/controllers/left/input  sensor_msgs/msg/Joy
/controllers/right/input sensor_msgs/msg/Joy
```

`Joy` 固定布局：

```text
axes[0] = trigger
axes[1] = grip
axes[2] = stick_x
axes[3] = stick_y

buttons[0] = primary
buttons[1] = secondary
buttons[2] = trigger_button
buttons[3] = grip_button
buttons[4] = stick_click
```

- `Joy.header.stamp` 与同帧 PoseStamped 相同。
- `Joy.header.frame_id` 使用对应 `left_controller_endpoint` 或 `right_controller_endpoint`。
- 摇杆和扳机保持 Quest 原始数值方向与范围；仅 Pose 执行 Unity-left 到 ROS FLU 的坐标转换。
- 不创建自定义 `.msg`，优先使用标准 `PoseStamped` 和 `Joy`，避免引入额外消息包及构建依赖。
- `package.xml` 增加 `sensor_msgs` 依赖；现有 geometry/visualization/tf/diagnostics 依赖保留。

### 12.2 TF 与参数

新增 TF：

```text
world -> left_controller_endpoint
world -> right_controller_endpoint
```

新增参数及默认值：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `left_controller_frame` | string | `left_controller_endpoint` | 左手柄末端 TF child frame |
| `right_controller_frame` | string | `right_controller_endpoint` | 右手柄末端 TF child frame |
| `enable_controller_topics` | bool | `true` | 发布 Controller Pose/Joy |

- 现有 `enable_tf` 同时控制 wrist 和 controller TF；旧流没有 ControllerFrame 时不会产生新增 TF。
- `world_frame`、QoS、transport、queue 和 diagnostics 参数继续复用。
- 将新参数追加到 `BridgeConfig`、`bridge.params.yaml`、launch 参数透传和 README 参数表；不得重命名旧参数。

### 12.3 ROS 2 内部扩展

- `runtime.py` 将队列元素类型从 `HandFrame` 扩展为 `HandFrame | ControllerFrame`，保留 `FrameRuntime` 和 `pop_frame()` 名称以避免调用端破坏。
- `bridge_node._drain_frames()` 使用 `isinstance` 分派：原 HandFrame 分支逐行保持，ControllerFrame 进入新增发布分支。
- `adapters.py` 新增 `to_controller_pose_stamped()`、`to_controller_joy()` 和 `to_controller_transform()`；复用现有位置/四元数 FLU 转换辅助函数。
- `BridgePublishers` 追加四个 controller publishers 和 `publish_controller_pose/input()`，不得修改旧 `/hands/**` publisher。
- 新增 `ControllerTfPublisher`，与 `WristTfPublisher` 并列；不把 Controller 分支塞入 wrist 类型。
- diagnostics 保留 `frames_in/out` 等旧字段，可追加 `hand_frames_in`、`controller_frames_in`，但不得删除或改名旧 KeyValue。
- RViz 配置可增加两个 TF Axes 显示；不改变现有手部 Marker 配置。
- ROS 2 包版本从 `0.2.0` 升为 `0.3.0`，并将 Python 依赖下限改为 `hand-tracking-sdk>=1.2.0,<2.0.0`。

## 13. 跨目录实施顺序

1. 在 `AppManager` 增加模式枚举、Toggle 引用、保存/加载、UI 锁定和模式只读属性。
2. 给 `HandLandmarkStreamer` 添加最小的 Hands 模式门控并做旧功能回归。
3. 实现 `ControllerAxisVisualizer`，在编辑器中验证坐标轴朝向、比例与显隐。
4. 实现 `ControllerInputStreamer` 的采样、独立 controller 标签、固定格式组包、UDP/TCP 和清理逻辑。
5. 增量修改场景，连接左右 Controller、Streamer、Visualizer 和 UI。
6. 在 Python SDK 完成 models → parser → ControllerFrameAssembler → HTSClient → convert/public exports，并发布/安装 `1.2.0`。
7. 在 ROS 2 SDK 完成 dependency → runtime union → adapters → publishers/TF → node dispatch → params/launch/RViz，并升级到 `0.3.0`。
8. 更新三个目录各自的 README/连接文档，重新构建 APK 和 ROS 2 包。
9. 先用 Python SDK 做协议集成测试，再启动 ROS 2 bridge 和 Quest 做端到端验证。

## 14. 测试与验收

### 14.1 原功能回归

- 新安装默认进入 Hands 模式。
- 旧 PlayerPrefs 中没有 `SavedInputMode` 时仍进入 Hands 模式。
- Both/Left/Right 手部模式分别验证腕部和 21 点数据。
- UDP、无线 TCP、ADB TCP 输出与改动前逐字段一致。
- Hands 模式的网络中不得出现 `controller pose/input`，Controllers 模式的网络中不得出现 `wrist/landmarks`。
- Debug Info、Head Pose、Video Stream、菜单停止键、手部可视化和断线返回菜单保持有效。
- Python SDK 现有 parser/frame/client/convert/teleop/video/visualization 测试全部通过，旧公开导入仍有效。
- ROS 2 现有 `/hands/**` Topic、wrist TF、launch、参数、Marker、diagnostics 测试全部通过。

### 14.2 手柄与 Python SDK

- Both/Left/Right 手柄选择只发送对应侧别。
- 移动和旋转手柄时，Pointer Pose 的 7 个数值连续、四元数有效。
- X/Y/A/B、扳机、握把、摇杆和摇杆按下均正确映射。
- 同侧 pose/input 的调试帧号及时间戳相同。
- UDP 每次收到包含两行的完整 datagram；TCP 可按行连续解析。
- controller pose 使用独立标签，接收端不会将其实例化为 HandWristPose。
- Controller 模式沿用原目标 IP 和端口，无需新增监听端口或 ADB reverse 规则。
- parser 覆盖左右侧、普通/调试头、精确字段数、非法 bool、模拟量越界、未知标签和 strict/tolerant 策略。
- ControllerFrameAssembler 覆盖同帧配对、左右隔离、乱序、陈旧包、缺 pose、缺 input、reset 和无调试元数据。
- HTSClient 的 PACKETS/FRAMES/BOTH 分别验证事件类型与顺序，HandFilter 对 ControllerFrame 生效。
- ControllerFrame 和其两个 payload 的 `to_dict/from_dict` 可无损往返。
- Unity-left 到右手/FLU 转换只修改 Pose，不修改 input。

### 14.3 ROS 2 消息接口

- `ControllerFrame` 分别转换为正确的 `PoseStamped`、`Joy` 和 `TransformStamped`。
- Joy axes/buttons 顺序严格符合接口表，左右手柄映射一致。
- `/controllers/**` 使用配置的 QoS；关闭 `enable_controller_topics` 时不发布 Pose/Joy。
- `enable_tf=true` 时发布 world→controller endpoint，关闭时 wrist/controller TF 都停止。
- 混合单元测试队列可分派 HandFrame 与 ControllerFrame，旧 HandFrame 分支结果不变。
- `package.xml` 的 sensor_msgs 依赖、参数文件、launch、README 和 RViz 配置通过安装空间验证。
- 执行 `colcon test`、topic echo/hz 和 `tf2_echo world left_controller_endpoint` 验证。

### 14.4 坐标轴

- 坐标轴原点与手柄 Pointer 射线起点重合。
- 红、绿、蓝轴分别对应导出四元数的局部 +X、+Y、+Z。
- 将网络 Pose 在 PC/Unity 测试场景重建后，与 Quest 中坐标轴朝向一致。
- Visualization、侧别、停止串流和跟踪丢失均能立即正确控制显隐。
- 坐标轴不遮挡 UI 点击、不产生物理碰撞、不引入逐帧对象分配。

### 14.5 故障场景

- 单侧手柄断开不产生零姿态包；恢复后自动继续。
- TCP 主机关闭时返回现有错误菜单并释放全部连接。
- Controller 组件缺失只阻止 Controllers 模式，重新选择 Hands 后可以正常启动。
- 重复开始/停止不会重复订阅事件、残留坐标轴或占用端口。
- Controller 一侧只收到 pose 或 input 时，Python/ROS 不发布不完整帧，且不会影响另一侧或 HandFrame。
- ROS 2 使用旧手部流时不会因等待 ControllerFrame 产生延迟、警告或空 Topic 数据。

## 15. 完成标准

只有满足以下条件才视为完成：

- Unity 无 C# 编译错误，Android APK 构建成功。
- Hands 模式所有旧功能通过回归，旧包格式没有变化。
- Controllers 模式在 Quest 真机完成左右/双侧姿态、按键和三轴验证。
- 手部与手柄在协议类型和组件上保持独立，只复用底层传输配置及公共格式化能力。
- 全仓库不存在将 Pointer Pose 发送为 `wrist` 标签的兼容分支。
- 网络导出的 Pointer Pose 与可视化坐标轴严格一致。
- 所有新增连接、事件订阅和可视化对象能在停止及销毁时正确清理。
- Python SDK 能把两条 controller 行解析并组装为独立 `ControllerFrame`，同时保持旧 API 与测试兼容。
- ROS 2 能发布 `/controllers/**` Pose/Joy 和末端 TF，同时保持 `/hands/**` 与旧参数兼容。
- `CONNECTIONS.md`、两个 SDK README 与实际实现的数据格式、版本和 Topic 一致。
