using UnityEngine;
using Oculus.Interaction.Input;

public class HandLandmarkVisualizer : MonoBehaviour
{
    [SerializeField] private HandLandmarkStreamer _streamer;
    [SerializeField] private GameObject _axisPrefab;
    [SerializeField] private float _scale = 0.02f;

    private GameObject[] _visualizerPool;
    private bool _poolCreated = false;

    // Keep visualization aligned with the stream by selecting semantic joints
    // rather than skeleton-version-specific integer indices.
    private readonly int[] _jointsToTrack = {
        (int)HandJointId.HandWristRoot,
        (int)HandJointId.HandThumb1, (int)HandJointId.HandThumb2,
        (int)HandJointId.HandThumb3, (int)HandJointId.HandThumbTip,
        (int)HandJointId.HandIndex1, (int)HandJointId.HandIndex2,
        (int)HandJointId.HandIndex3, (int)HandJointId.HandIndexTip,
        (int)HandJointId.HandMiddle1, (int)HandJointId.HandMiddle2,
        (int)HandJointId.HandMiddle3, (int)HandJointId.HandMiddleTip,
        (int)HandJointId.HandRing1, (int)HandJointId.HandRing2,
        (int)HandJointId.HandRing3, (int)HandJointId.HandRingTip,
        (int)HandJointId.HandPinky1, (int)HandJointId.HandPinky2,
        (int)HandJointId.HandPinky3, (int)HandJointId.HandPinkyTip
    };

    private void Start()
    {
        if (_streamer == null) _streamer = GetComponent<HandLandmarkStreamer>();
        CreatePool();
    }

    private void CreatePool()
    {
        _visualizerPool = new GameObject[_jointsToTrack.Length];
        for (int i = 0; i < _jointsToTrack.Length; i++)
        {
            _visualizerPool[i] = Instantiate(_axisPrefab, transform);
            _visualizerPool[i].transform.localScale = Vector3.one * _scale;
            _visualizerPool[i].SetActive(false);
        }
        _poolCreated = true;
    }

    private void Update()
    {
        if (!AppManager.Instance.isStreaming || !AppManager.Instance.IsHandMode || !AppManager.Instance.ShowLandmarks)
        {
            ToggleAllVisualizers(false);
            return;
        }

        // Check if this hand should even be active based on AppManager selection
        int mode = AppManager.Instance.SelectedHandMode;
        if ((mode == 1 && _streamer.Side == HandLandmarkStreamer.HandSide.Right) ||
            (mode == 2 && _streamer.Side == HandLandmarkStreamer.HandSide.Left))
        {
            ToggleAllVisualizers(false);
            return;
        }

        UpdateVisuals();
    }

private void UpdateVisuals()
    {
        IHand hand = _streamer.Hand;
        if (hand == null || !hand.IsTrackedDataValid)
        {
            ToggleAllVisualizers(false);
            return;
        }

        // 1. Get the Wrist (Root) pose in World Space
        // 2. Get the relative Joint poses
        if (hand.GetRootPose(out Pose rootPose) && 
            hand.GetJointPosesFromWrist(out ReadOnlyHandJointPoses joints))
        {
            for (int i = 0; i < _jointsToTrack.Length; i++)
            {
                int jointIndex = _jointsToTrack[i];
                if (jointIndex < joints.Count)
                {
                    _visualizerPool[i].SetActive(true);

                    // Calculate World Position: 
                    // Wrist Position + (Wrist Rotation * Local Joint Offset)
                    Vector3 worldPos = rootPose.position + (rootPose.rotation * joints[jointIndex].position);
                    
                    // Calculate World Rotation:
                    // Wrist Rotation * Local Joint Rotation
                    Quaternion worldRot = rootPose.rotation * joints[jointIndex].rotation;

                    _visualizerPool[i].transform.SetPositionAndRotation(worldPos, worldRot);
                }
            }
        }
    }

    private void ToggleAllVisualizers(bool state)
    {
        if (!_poolCreated) return;
        foreach (var obj in _visualizerPool)
        {
            if (obj.activeSelf != state) obj.SetActive(state);
        }
    }
}
