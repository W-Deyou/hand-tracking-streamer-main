using System;
using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Oculus.Interaction.Input;
using UnityEngine;

public sealed class ControllerInputStreamer : MonoBehaviour
{
    public const string LeftSnapshotSource = "Controller.Left";
    public const string RightSnapshotSource = "Controller.Right";

    public enum ControllerSide { Left, Right }

    [SerializeField] private ControllerSide controllerSide;
    [SerializeField] private float frequencySeconds = 0.01f;
    [SerializeField] private bool logToHUD = true;
    [SerializeField] private string hudLogSource = "Right";
    [SerializeField] private ControllerAxisVisualizer axisVisualizer;

    private IController _controller;
    private UdpClient _udpClient;
    private TcpClient _tcpClient;
    private NetworkStream _tcpStream;
    private IPEndPoint _remoteEndPoint;
    private bool _isInitialized;
    private int _currentProtocol = -1;
    private float _timer;
    private uint _frameId;

    private readonly StringBuilder _packet = new StringBuilder(512);
    private readonly StringBuilder _hud = new StringBuilder(256);
    private static readonly double TicksToNs = 1_000_000_000.0 / Stopwatch.Frequency;

    public ControllerSide Side => controllerSide;

    public void Configure(ControllerSide side, ControllerAxisVisualizer visualizer, string logSource)
    {
        controllerSide = side;
        axisVisualizer = visualizer;
        hudLogSource = logSource;
    }

    private void Start()
    {
        _controller = GetComponent<IController>();
        if (_controller == null)
        {
            LogHUD("Controller streamer requires an IController component.");
            enabled = false;
            return;
        }
        if (axisVisualizer == null)
        {
            axisVisualizer = GetComponent<ControllerAxisVisualizer>();
        }
        _controller.WhenUpdated += OnControllerUpdated;
    }

    private void OnDestroy()
    {
        if (_controller != null)
        {
            _controller.WhenUpdated -= OnControllerUpdated;
        }
        axisVisualizer?.Hide();
        Disconnect();
    }

    private void Update()
    {
        AppManager manager = AppManager.Instance;
        if (manager == null || !manager.isStreaming || !manager.IsControllerMode
            || !IsSelectedSide(manager.SelectedHandMode))
        {
            if (_isInitialized) Disconnect();
            return;
        }

        // Establish and monitor the telemetry socket independently of tracking.
        // A controller can temporarily have no pointer pose while TCP is closed.
        if (!_isInitialized) InitializeNetwork();
        if (_isInitialized && _currentProtocol != 0 && TcpConnectionHealth.IsDisconnected(_tcpClient))
        {
            Disconnect();
            manager.HandleDisconnection("Telemetry host closed the TCP connection.");
        }
    }

    private void OnControllerUpdated()
    {
        AppManager manager = AppManager.Instance;
        if (manager == null || !manager.IsControllerMode)
        {
            axisVisualizer?.Hide();
            if (_isInitialized) Disconnect();
            return;
        }

        // While the menu is open, show axes for whichever controller is awake so
        // single-controller UI/setup works. Telemetry side-filter applies only
        // after Start Streaming.
        bool selectedForTelemetry = IsSelectedSide(manager.SelectedHandMode);
        if (manager.isStreaming && !selectedForTelemetry)
        {
            axisVisualizer?.Hide();
            if (_isInitialized) Disconnect();
            return;
        }

        Pose pointerPose = Pose.identity;
        bool hasPointerPose = _controller.IsConnected
            && _controller.TryGetPointerPose(out pointerPose);
        if (hasPointerPose)
        {
            // Exact Pointer Pose exported over the network. Axes, ray origin
            // (Quest3ControllerPointerPoseCorrector), and telemetry share this
            // live IController sample — never the virtual controller mesh.
            axisVisualizer?.SetPose(pointerPose, manager.ShowControllerAxes);
        }
        else
        {
            axisVisualizer?.Hide();
        }

        // Local endpoint axes are useful while configuring the menu and do not
        // depend on network streaming being active.
        if (!manager.isStreaming)
        {
            if (_isInitialized) Disconnect();
            return;
        }

        if (!hasPointerPose || !selectedForTelemetry)
        {
            return;
        }

        if (!_isInitialized) return;

        _timer += Time.deltaTime;
        if (_timer < frequencySeconds)
        {
            return;
        }
        _timer = 0f;
        ProcessControllerData(manager, pointerPose);
    }

    private bool IsSelectedSide(int mode)
    {
        return mode == 0
            || (mode == 1 && controllerSide == ControllerSide.Left)
            || (mode == 2 && controllerSide == ControllerSide.Right);
    }

    public void StopConnection()
    {
        Disconnect();
    }

    private void ProcessControllerData(AppManager manager, Pose pointerPose)
    {
        ControllerInput input = _controller.ControllerInput;
        bool addDebugHeader = manager.ShowDebugInfo;
        uint frameId = 0;
        ulong timestampNs = 0;
        if (addDebugHeader)
        {
            frameId = ++_frameId;
            timestampNs = GetMonotonicTimestampNs();
        }

        _packet.Clear();
        AppendHeader(_packet, "controller pose", addDebugHeader, frameId, timestampNs);
        _packet.Append(", ");
        AppendVector3(_packet, pointerPose.position);
        _packet.Append(", ");
        AppendQuaternion(_packet, pointerPose.rotation);

        _packet.Append('\n');
        AppendHeader(_packet, "controller input", addDebugHeader, frameId, timestampNs);
        _packet.Append(", ");
        AppendFloat(_packet, input.Trigger);
        _packet.Append(", ");
        AppendFloat(_packet, input.Grip);
        _packet.Append(", ");
        AppendFloat(_packet, input.Primary2DAxis.x);
        _packet.Append(", ");
        AppendFloat(_packet, input.Primary2DAxis.y);
        AppendButton(_packet, input.PrimaryButton);
        AppendButton(_packet, input.SecondaryButton);
        AppendButton(_packet, input.TriggerButton);
        AppendButton(_packet, input.GripButton);
        AppendButton(_packet, input.Primary2DAxisClick);

        if (logToHUD && manager.ShowDebugInfo)
        {
            _hud.Clear();
            _hud.Append("=== [").Append(controllerSide).AppendLine("] Controller ===");
            _hud.Append("Endpoint: ").AppendLine(pointerPose.position.ToString("F3"));
            _hud.Append("Trigger: ").Append(input.Trigger.ToString("F2", CultureInfo.InvariantCulture));
            _hud.Append(" Grip: ").AppendLine(input.Grip.ToString("F2", CultureInfo.InvariantCulture));
            _hud.Append("Stick: ").AppendLine(input.Primary2DAxis.ToString("F2"));
            LogHUDSnapshot(_hud.ToString());
        }

        SendData(_packet.ToString());
    }

    private void InitializeNetwork()
    {
        AppManager manager = AppManager.Instance;
        if (manager == null) return;
        _currentProtocol = manager.SelectedProtocol;
        try
        {
            if (_currentProtocol == 0)
            {
                _udpClient = new UdpClient();
                _udpClient.Client.SendBufferSize = 0;
                _remoteEndPoint = new IPEndPoint(IPAddress.Parse(manager.ServerIP), manager.ServerPort);
                LogHUD($"Controller UDP Ready: {manager.ServerIP}:{manager.ServerPort}");
            }
            else
            {
                _tcpClient = new TcpClient(AddressFamily.InterNetwork)
                {
                    NoDelay = true,
                    SendTimeout = 1000,
                    ReceiveTimeout = 1000,
                };
                _tcpClient.Connect(manager.ServerIP, manager.ServerPort);
                _tcpStream = _tcpClient.GetStream();
                LogHUD($"Controller TCP Connected: {manager.ServerIP}:{manager.ServerPort}");
            }
            _isInitialized = true;
        }
        catch (Exception exception)
        {
            Disconnect();
            manager.HandleDisconnection("Controller connection failed: " + exception.Message);
        }
    }

    private void SendData(string message)
    {
        if (AppManager.Instance == null || !AppManager.Instance.isStreaming) return;
        try
        {
            byte[] data = Encoding.UTF8.GetBytes(_currentProtocol == 0 ? message : message + "\n");
            if (_currentProtocol == 0 && _udpClient != null)
            {
                _udpClient.Send(data, data.Length, _remoteEndPoint);
            }
            else if (_tcpStream != null && _tcpStream.CanWrite)
            {
                _tcpStream.Write(data, 0, data.Length);
            }
        }
        catch (Exception exception)
        {
            Disconnect();
            AppManager.Instance?.HandleDisconnection("Controller send failed: " + exception.Message);
        }
    }

    private void Disconnect()
    {
        try { _udpClient?.Close(); } catch { }
        try { _tcpStream?.Close(); } catch { }
        try { _tcpClient?.Close(); } catch { }
        _udpClient = null;
        _tcpStream = null;
        _tcpClient = null;
        _remoteEndPoint = null;
        _isInitialized = false;
    }

    private void AppendHeader(StringBuilder builder, string section, bool debug, uint frameId, ulong timestampNs)
    {
        builder.Append(controllerSide).Append(' ').Append(section);
        if (debug)
        {
            builder.Append(" | f = ").Append(frameId).Append(" | t = ").Append(timestampNs);
        }
        builder.Append(':');
    }

    private static void AppendVector3(StringBuilder builder, Vector3 value)
    {
        builder.Append(value.x.ToString("F4", CultureInfo.InvariantCulture)).Append(", ")
            .Append(value.y.ToString("F4", CultureInfo.InvariantCulture)).Append(", ")
            .Append(value.z.ToString("F4", CultureInfo.InvariantCulture));
    }

    private static void AppendQuaternion(StringBuilder builder, Quaternion value)
    {
        builder.Append(value.x.ToString("F3", CultureInfo.InvariantCulture)).Append(", ")
            .Append(value.y.ToString("F3", CultureInfo.InvariantCulture)).Append(", ")
            .Append(value.z.ToString("F3", CultureInfo.InvariantCulture)).Append(", ")
            .Append(value.w.ToString("F3", CultureInfo.InvariantCulture));
    }

    private static void AppendFloat(StringBuilder builder, float value)
    {
        builder.Append(value.ToString("F4", CultureInfo.InvariantCulture));
    }

    private static void AppendButton(StringBuilder builder, bool value)
    {
        builder.Append(", ").Append(value ? '1' : '0');
    }

    private static ulong GetMonotonicTimestampNs()
    {
        return (ulong)(Stopwatch.GetTimestamp() * TicksToNs);
    }

    private void LogHUD(string message)
    {
        if (logToHUD && LogManager.Instance != null)
        {
            LogManager.Instance.Log(hudLogSource, message);
        }
    }

    private void LogHUDSnapshot(string message)
    {
        if (!logToHUD || LogManager.Instance == null) return;
        string source = controllerSide == ControllerSide.Left
            ? LeftSnapshotSource
            : RightSnapshotSource;
        LogManager.Instance.SetLatestSnapshot(source, message);
    }
}
