using UnityEngine;

/// <summary>
/// Applies the official Meta Touch Plus pointer pose used by current Meta
/// Interaction SDK releases while keeping the v72 OVR controller root pose.
/// Only the controller ray origin is corrected; the controller model and
/// tracking anchors are deliberately left untouched.
/// </summary>
[DefaultExecutionOrder(10000)]
public sealed class Quest3ControllerPointerPoseCorrector : MonoBehaviour
{
    public enum ControllerHand
    {
        Left,
        Right
    }

    private static readonly Vector3 LeftPointerPosition = new Vector3(0.008f, 0.00014f, 0.03f);
    private static readonly Vector3 RightPointerPosition = new Vector3(-0.008f, 0.00014f, 0.03f);
    private static readonly Vector3 RayTipOffset = new Vector3(0f, 0f, 0.02f);
    // Project-specific controller-local translation calibration. The lateral
    // component is mirrored while both controllers move 6 cm forward.
    private static readonly Vector3 LeftControllerCalibrationOffset = new Vector3(-0.005f, 0f, 0.06f);
    private static readonly Vector3 RightControllerCalibrationOffset = new Vector3(0.005f, 0f, 0.06f);

    [SerializeField] private ControllerHand hand;

    private OVRCameraRig _cameraRig;

    public void Configure(ControllerHand controllerHand)
    {
        hand = controllerHand;
    }

    private void OnEnable()
    {
        Application.onBeforeRender += ApplyCorrectedPose;
        ResolveCameraRig();
    }

    private void OnDisable()
    {
        Application.onBeforeRender -= ApplyCorrectedPose;
    }

    private void LateUpdate()
    {
        ApplyCorrectedPose();
    }

    private void ApplyCorrectedPose()
    {
        if (_cameraRig == null)
        {
            ResolveCameraRig();
        }

        if (_cameraRig == null || _cameraRig.trackingSpace == null)
        {
            return;
        }

        OVRInput.Controller controller = hand == ControllerHand.Left
            ? OVRInput.Controller.LTouch
            : OVRInput.Controller.RTouch;
        if (!OVRInput.IsControllerConnected(controller))
        {
            return;
        }

        Vector3 rootPosition = OVRInput.GetLocalControllerPosition(controller);
        Quaternion rootRotation = OVRInput.GetLocalControllerRotation(controller);
        Vector3 pointerPosition = hand == ControllerHand.Left ? LeftPointerPosition : RightPointerPosition;
        Vector3 calibrationOffset = hand == ControllerHand.Left
            ? LeftControllerCalibrationOffset
            : RightControllerCalibrationOffset;
        Quaternion pointerRotation = Quaternion.AngleAxis(
            hand == ControllerHand.Left ? 5f : -5f,
            Vector3.up);

        // Match the current Interaction SDK controller ray template, which
        // starts the rendered ray 2 cm forward from the pointer pose.
        Vector3 trackingPosition = rootPosition +
                                   rootRotation * (calibrationOffset + pointerPosition +
                                                   pointerRotation * RayTipOffset);
        Quaternion trackingRotation = rootRotation * pointerRotation;

        Transform trackingSpace = _cameraRig.trackingSpace;
        transform.SetPositionAndRotation(
            trackingSpace.TransformPoint(trackingPosition),
            trackingSpace.rotation * trackingRotation);
    }

    private void ResolveCameraRig()
    {
        _cameraRig = FindFirstObjectByType<OVRCameraRig>();
    }
}
