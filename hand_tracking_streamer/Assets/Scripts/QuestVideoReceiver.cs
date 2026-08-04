using System;
using System.Collections;
using System.Reflection;
using System.Threading.Tasks;
using Unity.WebRTC;
using UnityEngine;

public readonly struct VideoReceiverStatsSnapshot
{
    public readonly double Fps;
    public readonly uint Width;
    public readonly uint Height;
    public readonly double JitterBufferMs;
    public readonly double JitterTargetMs;
    public readonly double DecodeMs;
    public readonly double ProcessingMs;
    public readonly uint FramesDropped;
    public readonly int PacketsLost;
    public readonly ulong PacketsDiscarded;
    public readonly uint NackCount;
    public readonly uint PliCount;
    public readonly uint FreezeCount;

    public VideoReceiverStatsSnapshot(
        double fps,
        uint width,
        uint height,
        double jitterBufferMs,
        double jitterTargetMs,
        double decodeMs,
        double processingMs,
        uint framesDropped,
        int packetsLost,
        ulong packetsDiscarded,
        uint nackCount,
        uint pliCount,
        uint freezeCount)
    {
        Fps = fps;
        Width = width;
        Height = height;
        JitterBufferMs = jitterBufferMs;
        JitterTargetMs = jitterTargetMs;
        DecodeMs = decodeMs;
        ProcessingMs = processingMs;
        FramesDropped = framesDropped;
        PacketsLost = packetsLost;
        PacketsDiscarded = packetsDiscarded;
        NackCount = nackCount;
        PliCount = pliCount;
        FreezeCount = freezeCount;
    }
}

public class QuestVideoReceiver : MonoBehaviour
{
    public event Action<string> OnLocalOfferReady;
    public event Action<string, string, int?> OnLocalIceCandidate;
    public event Action<Texture> OnRemoteTexture;
    public event Action<string> OnPeerStateChanged;
    public event Action<string> OnError;
    public event Action<VideoReceiverStatsSnapshot> OnStats;

    private RTCPeerConnection _peer;
    private VideoStreamTrack _remoteTrack;
    private RTCRtpReceiver _remoteReceiver;
    private Coroutine _updateCoroutine;
    private Coroutine _statsCoroutine;
    private bool _updateRunning;
    private ulong _previousJitterEmittedCount;
    private double _previousJitterBufferDelay;
    private double _previousJitterTargetDelay;
    private uint _previousFramesDecoded;
    private double _previousDecodeTime;
    private double _previousProcessingDelay;

    public void InitializePeer()
    {
        if (!_updateRunning)
        {
            _updateCoroutine = StartCoroutine(WebRTC.Update());
            _updateRunning = true;
        }

        ClosePeer();
        _peer = new RTCPeerConnection();
        TryAddVideoRecvTransceiver();
        _peer.OnConnectionStateChange = state => OnPeerStateChanged?.Invoke(state.ToString());
        _peer.OnIceConnectionChange = state => OnPeerStateChanged?.Invoke($"ICE {state}");
        _peer.OnIceCandidate = candidate =>
        {
            if (candidate == null) return;
            OnLocalIceCandidate?.Invoke(candidate.Candidate, candidate.SdpMid, candidate.SdpMLineIndex);
        };
        _peer.OnTrack = e =>
        {
            if (e.Track is VideoStreamTrack vt)
            {
                _remoteTrack = vt;
                _remoteReceiver = e.Receiver;
                ResetStatsBaseline();
                _remoteTrack.OnVideoReceived += texture => OnRemoteTexture?.Invoke(texture);
                if (_statsCoroutine == null)
                {
                    _statsCoroutine = StartCoroutine(PollReceiverStats());
                }
            }
        };
    }

    public Task<bool> CreateAndSendOfferAsync()
    {
        var tcs = new TaskCompletionSource<bool>();
        StartCoroutine(CreateOfferRoutine(tcs));
        return tcs.Task;
    }

    public Task<bool> SetRemoteAnswerAsync(string sdp)
    {
        var tcs = new TaskCompletionSource<bool>();
        StartCoroutine(SetRemoteAnswerRoutine(sdp, tcs));
        return tcs.Task;
    }

    public void AddRemoteIceCandidate(string candidate, string sdpMid, int? sdpMLineIndex)
    {
        if (_peer == null || string.IsNullOrWhiteSpace(candidate)) return;
        var init = new RTCIceCandidateInit
        {
            candidate = candidate,
            sdpMid = sdpMid,
            sdpMLineIndex = sdpMLineIndex ?? 0,
        };
        _peer.AddIceCandidate(new RTCIceCandidate(init));
    }

    public void ClosePeer()
    {
        if (_statsCoroutine != null)
        {
            StopCoroutine(_statsCoroutine);
            _statsCoroutine = null;
        }
        _remoteReceiver = null;
        ResetStatsBaseline();
        if (_remoteTrack != null)
        {
            _remoteTrack.Dispose();
            _remoteTrack = null;
        }
        if (_peer != null)
        {
            _peer.Close();
            _peer.Dispose();
            _peer = null;
        }
    }

    private IEnumerator PollReceiverStats()
    {
        var interval = new WaitForSecondsRealtime(1f);
        while (_remoteReceiver != null)
        {
            yield return interval;
            RTCRtpReceiver receiver = _remoteReceiver;
            if (receiver == null) continue;

            RTCStatsReportAsyncOperation op = receiver.GetStats();
            yield return op;
            if (op.IsError || op.Value == null) continue;

            RTCStatsReport report = op.Value;
            try
            {
                foreach (RTCStats value in report.Stats.Values)
                {
                    if (!(value is RTCInboundRTPStreamStats stats) || stats.kind != "video")
                    {
                        continue;
                    }

                    ulong emittedDelta = Delta(stats.jitterBufferEmittedCount, _previousJitterEmittedCount);
                    uint decodedDelta = Delta(stats.framesDecoded, _previousFramesDecoded);
                    double jitterMs = PerFrameMs(
                        stats.jitterBufferDelay,
                        _previousJitterBufferDelay,
                        emittedDelta);
                    double targetMs = PerFrameMs(
                        stats.jitterBufferTargetDelay,
                        _previousJitterTargetDelay,
                        emittedDelta);
                    double decodeMs = PerFrameMs(
                        stats.totalDecodeTime,
                        _previousDecodeTime,
                        decodedDelta);
                    double processingMs = PerFrameMs(
                        stats.totalProcessingDelay,
                        _previousProcessingDelay,
                        decodedDelta);

                    _previousJitterEmittedCount = stats.jitterBufferEmittedCount;
                    _previousJitterBufferDelay = stats.jitterBufferDelay;
                    _previousJitterTargetDelay = stats.jitterBufferTargetDelay;
                    _previousFramesDecoded = stats.framesDecoded;
                    _previousDecodeTime = stats.totalDecodeTime;
                    _previousProcessingDelay = stats.totalProcessingDelay;

                    OnStats?.Invoke(new VideoReceiverStatsSnapshot(
                        stats.framesPerSecond,
                        stats.frameWidth,
                        stats.frameHeight,
                        jitterMs,
                        targetMs,
                        decodeMs,
                        processingMs,
                        stats.framesDropped,
                        stats.packetsLost,
                        stats.packetsDiscarded,
                        stats.nackCount,
                        stats.pliCount,
                        stats.freezeCount));
                    break;
                }
            }
            finally
            {
                report.Dispose();
            }
        }
        _statsCoroutine = null;
    }

    private void ResetStatsBaseline()
    {
        _previousJitterEmittedCount = 0;
        _previousJitterBufferDelay = 0;
        _previousJitterTargetDelay = 0;
        _previousFramesDecoded = 0;
        _previousDecodeTime = 0;
        _previousProcessingDelay = 0;
    }

    private static ulong Delta(ulong current, ulong previous)
    {
        return current >= previous ? current - previous : current;
    }

    private static uint Delta(uint current, uint previous)
    {
        return current >= previous ? current - previous : current;
    }

    private static double PerFrameMs(double current, double previous, ulong count)
    {
        if (count == 0) return 0;
        double delta = current >= previous ? current - previous : current;
        return delta * 1000.0 / count;
    }

    private IEnumerator CreateOfferRoutine(TaskCompletionSource<bool> tcs)
    {
        if (_peer == null)
        {
            tcs.SetResult(false);
            yield break;
        }

        var op = _peer.CreateOffer();
        yield return op;
        if (op.IsError)
        {
            OnError?.Invoke($"CreateOffer failed: {op.Error.message}");
            tcs.SetResult(false);
            yield break;
        }

        var desc = op.Desc;
        var setLocal = _peer.SetLocalDescription(ref desc);
        yield return setLocal;
        if (setLocal.IsError)
        {
            OnError?.Invoke($"SetLocalDescription failed: {setLocal.Error.message}");
            tcs.SetResult(false);
            yield break;
        }

        OnLocalOfferReady?.Invoke(desc.sdp);
        tcs.SetResult(true);
    }

    private IEnumerator SetRemoteAnswerRoutine(string sdp, TaskCompletionSource<bool> tcs)
    {
        if (_peer == null)
        {
            tcs.SetResult(false);
            yield break;
        }

        var desc = new RTCSessionDescription
        {
            type = RTCSdpType.Answer,
            sdp = sdp,
        };
        var op = _peer.SetRemoteDescription(ref desc);
        yield return op;
        if (op.IsError)
        {
            OnError?.Invoke($"SetRemoteDescription failed: {op.Error.message}");
            tcs.SetResult(false);
            yield break;
        }

        tcs.SetResult(true);
    }

    private void OnDestroy()
    {
        ClosePeer();
        if (_updateRunning)
        {
            if (_updateCoroutine != null)
            {
                StopCoroutine(_updateCoroutine);
                _updateCoroutine = null;
            }
            _updateRunning = false;
        }
    }

    private void TryAddVideoRecvTransceiver()
    {
        if (_peer == null) return;
        try
        {
            MethodInfo[] methods = typeof(RTCPeerConnection).GetMethods();
            foreach (MethodInfo method in methods)
            {
                if (method.Name != "AddTransceiver") continue;
                ParameterInfo[] parameters = method.GetParameters();
                if (parameters.Length < 1) continue;
                if (parameters[0].ParameterType != typeof(TrackKind)) continue;

                object[] args = new object[parameters.Length];
                args[0] = TrackKind.Video;

                if (parameters.Length >= 2)
                {
                    Type initType = parameters[1].ParameterType;
                    object initValue = Activator.CreateInstance(initType);
                    PropertyInfo directionProp = initType.GetProperty("direction");
                    if (directionProp != null)
                    {
                        object recvOnly = Enum.Parse(directionProp.PropertyType, "RecvOnly");
                        directionProp.SetValue(initValue, recvOnly);
                    }
                    args[1] = initValue;
                }

                method.Invoke(_peer, args);
                OnPeerStateChanged?.Invoke("Added video recv transceiver");
                return;
            }

            OnError?.Invoke("No compatible AddTransceiver(TrackKind, ...) overload found.");
        }
        catch (Exception ex)
        {
            OnError?.Invoke($"AddTransceiver failed: {ex.Message}");
        }
    }
}
