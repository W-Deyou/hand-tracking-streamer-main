using Oculus.Interaction.Input;
using UnityEngine;

/// <summary>
/// Keeps the Interaction SDK controller ray origin locked to the same live
/// <see cref="IController.TryGetPointerPose"/> sample used for telemetry and
/// RGB axes. Does not invent offsets from the virtual/static controller mesh.
/// </summary>
[DefaultExecutionOrder(10000)]
public sealed class Quest3ControllerPointerPoseCorrector : MonoBehaviour
{
    public enum ControllerHand
    {
        Left,
        Right
    }

    [SerializeField] private ControllerHand hand;

    private IController _controller;

    public void Configure(ControllerHand controllerHand)
    {
        hand = controllerHand;
    }

    private void OnEnable()
    {
        Application.onBeforeRender += ApplyPointerPose;
        ResolveController();
    }

    private void OnDisable()
    {
        Application.onBeforeRender -= ApplyPointerPose;
    }

    private void LateUpdate()
    {
        ApplyPointerPose();
    }

    private void ApplyPointerPose()
    {
        if (_controller == null)
        {
            ResolveController();
        }

        if (_controller == null || !_controller.IsConnected)
        {
            return;
        }

        // Same pointer sample as ControllerInputStreamer / ControllerAxisVisualizer.
        // Ray origin must track real controller aim data, not mesh-local offsets.
        if (!_controller.TryGetPointerPose(out Pose pointerPose))
        {
            return;
        }

        transform.SetPositionAndRotation(pointerPose.position, pointerPose.rotation);
    }

    private void ResolveController()
    {
        _controller = GetComponentInParent<IController>();
    }
}
