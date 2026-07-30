# hand_tracking_streamer（Unity / Quest）

`hand_tracking_streamer` 是 Hand Tracking Streamer 的 Meta Quest 客户端 Unity 工程。它通过 OpenXR/Meta XR 获取手部、头部或 Touch 控制器数据，在头显内显示追踪状态，并通过 UDP/TCP 将遥测发往主机；可选的视频模块还能接收主机通过 WebRTC 返回的画面。

## 文档导航

- [工程总 README](../README.md)
- [Python SDK](../hand-tracking-sdk-main/README.md)
- [ROS 2 桥接包](../hand-tracking-sdk-ros2-main/README.md)
- [连接与数据协议](../CONNECTIONS.md)

## 环境与包依赖

- Unity Editor：`6000.0.65f1`。
- 构建平台：Android。
- Unity Hub 模块：Android Build Support、Android SDK & NDK Tools、OpenJDK。
- 目标设备：支持手部追踪的 Meta Quest；侧载与 USB TCP 需要 ADB 和开发者模式。

主要 Unity 包由 `Packages/manifest.json` 锁定：

| 包 | 版本 | 用途 |
|---|---:|---|
| Meta XR SDK All | `72.0.0` | Meta Quest XR 能力 |
| OpenXR Plugin | `1.17.1` | OpenXR 运行时接入 |
| XR Hands | `1.8.0` | 手骨架和关节追踪 |
| Input System | `1.19.0` | 控制器输入 |
| Universal RP | `17.5.0` | 移动端渲染管线 |
| WebRTC | `3.0.0-pre.8` | 主机视频回传 |
| Newtonsoft Json | `3.2.2` | 信令数据序列化 |

首次用 Unity 打开项目时，Package Manager 会根据 manifest 自动恢复依赖。

## 目录结构

| 路径 | 作用 |
|---|---|
| `Assets/Scenes/Scene.unity` | 当前唯一启用的应用场景 |
| `Assets/Scripts/AppManager.cs` | 菜单、运行模式、网络配置和应用状态 |
| `Assets/Scripts/HandLandmarkStreamer.cs` | 手腕与 21 个手部关键点采集/发送 |
| `Assets/Scripts/ControllerInputStreamer.cs` | 控制器 Pointer Pose、轴和按键发送 |
| `Assets/Scripts/HeadPoseStreamer.cs` | Quest 中心眼位姿发送 |
| `Assets/Scripts/TcpConnectionHealth.cs` | TCP 连接状态与重连辅助 |
| `Assets/Scripts/QuestVideoReceiver.cs` | Quest 端 WebRTC 接收流程 |
| `Assets/Scripts/VideoSignalingClient.cs` | WebSocket 视频信令客户端 |
| `Assets/Scripts/VideoPanelRenderer.cs` | 回传画面渲染到面板 |
| `Assets/Editor/CodexAndroidBuild.cs` | 命令行 APK 构建入口 |
| `Assets/Plugins/Android/AndroidManifest.xml` | Android 应用配置与权限 |
| `Packages/manifest.json` | Unity 包版本 |
| `ProjectSettings/` | Unity、OpenXR、Android 和渲染设置 |

## 在 Unity Editor 中运行和构建

1. 用 Unity Hub 安装 Unity `6000.0.65f1` 与 Android Build Support。
2. 在 Unity Hub 中选择 **Add project from disk**，打开本目录。
3. 等待 Package Manager 完成依赖恢复。
4. 打开 `Assets/Scenes/Scene.unity`。
5. 在 **File > Build Profiles** 中选择 Android 并切换平台。
6. 连接已开启开发者模式的 Quest，使用 **Build And Run**；或使用 **Build** 生成 APK。

XR 输入在普通桌面 Play Mode 中不一定可用；完整追踪、网络与视频链路应在 Quest 真机验证。

## 命令行构建

从工程总仓库根目录执行以下 PowerShell 命令：

```powershell
& "C:\Program Files\Unity\Hub\Editor\6000.0.65f1\Editor\Unity.exe" `
  -batchmode -quit `
  -projectPath "$PWD\hand_tracking_streamer" `
  -executeMethod CodexAndroidBuild.BuildApk `
  -apkPath "$PWD\hand_tracking_streamer.apk" `
  -logFile "$PWD\unity-build.log"
```

构建脚本会读取 `EditorBuildSettings` 中已启用的场景，生成 APK；没有启用场景或 Unity 构建失败时会返回失败状态。若 Unity 安装目录不同，请调整可执行文件路径。

## 安装 APK

```powershell
adb devices
adb install -r ..\hand_tracking_streamer.apk
```

首次连接时需在 Quest 内确认 USB 调试授权。

## 应用运行方式

启动应用后配置：

- Input：Hands 或 Controller Input，二者互斥。
- Side：Both、Left 或 Right。
- Protocol：UDP、USB TCP 或 Wireless TCP。
- IP / Port：与主机接收端一致。
- Debug Info：可为相关消息附加源帧号和时间戳。
- Head Pose / Video：按需要启用头部位姿和主机视频回传。

### UDP

默认目标为 `255.255.255.255:9000`。Quest 和主机需位于同一局域网，主机通常绑定 `0.0.0.0:9000`：

```powershell
python ..\scripts\sockets.py --protocol udp --host 0.0.0.0 --port 9000
```

### USB TCP

主机先建立反向端口映射并启动 TCP Server：

```powershell
adb reverse tcp:8000 tcp:8000
python ..\scripts\sockets.py --protocol tcp --host localhost --port 8000
```

Quest 端选择 USB TCP，端口设为 `8000`。

### 无线 TCP

主机监听 `0.0.0.0:8000`，Quest 目标 IP 填写主机的局域网 IPv4，端口填写 `8000`。请确保系统防火墙允许该端口入站。

## 视频回传

视频链路与遥测链路独立。主机使用 Python SDK 启动 WebSocket 信令与 WebRTC 视频服务：

```bash
cd ../hand-tracking-sdk-main
python -m pip install -e ".[video]"
python examples/video/test_pattern_video_host.py
```

默认信令端口为 `8765`；Quest 使用当前主机 IP 连接 `ws://<主机IP>:8765`。视频媒体由 WebRTC 传输，不应把 `8765` 当作遥测端口。

## 数据输出

- 手部模式：每侧输出 Wrist Pose 和 21 个关键点。
- 控制器模式：每侧输出独立的 Controller Pose 与 Controller Input。
- Head Pose：输出 Quest 中心眼的位置与旋转。
- 数据编码：UTF-8、逗号分隔、按行组织；TCP 以换行符定界。

原始数据遵循 Unity 左手坐标约定。字段顺序、控制器按键布局与调试元数据说明见 [CONNECTIONS.md](../CONNECTIONS.md)。

## 常见问题

- Unity 打开后包报错：确认 Editor 版本准确，并等待 Package Manager 完成恢复。
- Android 构建失败：通过 Unity Hub 检查 Android SDK、NDK 和 OpenJDK 模块是否安装。
- Quest 不可见：执行 `adb devices`，在头显内接受调试授权。
- UDP 有明显批处理或延迟：优先尝试 USB TCP，或检查无线路由器 DTIM/客户端隔离设置。
- TCP 显示未连接：主机必须先监听；USB 模式还必须先执行 `adb reverse`。
- 视频面板无画面：确认主机 `8765` 端口可达、视频 Host 已启动，且信令 IP 是主机局域网地址。

## 许可证

本包随主工程采用 Apache-2.0，详见 [根目录 LICENSE](../LICENSE)。
