using System;
using System.Reflection;
using UnityEngine;

/// <summary>
/// Loads the controller model supplied by the Meta runtime and keeps the
/// SDK's static controller model as a fallback until loading succeeds.
/// </summary>
[DisallowMultipleComponent]
[DefaultExecutionOrder(-10)]
public sealed class QuestRuntimeControllerVisual : MonoBehaviour
{
    public enum ControllerHand
    {
        Left,
        Right
    }

    [SerializeField] private ControllerHand hand;
    [SerializeField] private OVRControllerHelper staticFallback;

    private OVRRuntimeController _runtimeController;
    private bool _usingRuntimeModel;
    private MethodInfo _loadControllerModel;
    private float _nextLoadAttemptTime;
    private bool _reportedRuntimePaths;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void InstallRuntimeControllerVisuals()
    {
#if UNITY_ANDROID && !UNITY_EDITOR
        InstallForAnchor("LeftControllerAnchor", ControllerHand.Left);
        InstallForAnchor("RightControllerAnchor", ControllerHand.Right);
#endif
    }

    private static void InstallForAnchor(string anchorName, ControllerHand controllerHand)
    {
        GameObject anchorObject = GameObject.Find(anchorName);
        if (anchorObject == null ||
            anchorObject.transform.Find($"[Runtime] Controller Visual {controllerHand}") != null)
        {
            return;
        }

        // Keep the original GitHub controller tracking object, ray and input
        // pipeline untouched. The runtime GLB is display-only and sits directly
        // below OVRCameraRig's grip-pose anchor at an identity transform.
        OVRControllerHelper fallback = anchorObject.GetComponentInChildren<OVRControllerHelper>(true);
        GameObject visualObject = new GameObject($"[Runtime] Controller Visual {controllerHand}");
        visualObject.SetActive(false);
        visualObject.transform.SetParent(anchorObject.transform, false);
        visualObject.transform.localPosition = Vector3.zero;
        visualObject.transform.localRotation = Quaternion.identity;
        visualObject.transform.localScale = Vector3.one;

        QuestRuntimeControllerVisual visual = visualObject.AddComponent<QuestRuntimeControllerVisual>();
        visual.Configure(controllerHand, fallback);
        visualObject.SetActive(true);
    }

    public void Configure(ControllerHand controllerHand, OVRControllerHelper fallback)
    {
        hand = controllerHand;
        staticFallback = fallback;
    }

    private void Awake()
    {
        _runtimeController = GetComponent<OVRRuntimeController>();
        if (_runtimeController == null)
        {
            _runtimeController = gameObject.AddComponent<OVRRuntimeController>();
        }

        _runtimeController.m_controller = hand == ControllerHand.Left
            ? OVRInput.Controller.LTouch
            : OVRInput.Controller.RTouch;
        _runtimeController.m_supportAnimation = true;
        _runtimeController.m_controllerModelShader =
            Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
        _loadControllerModel = typeof(OVRRuntimeController).GetMethod(
            "LoadControllerModel",
            BindingFlags.Instance | BindingFlags.NonPublic);

        Debug.Log($"[QuestRuntimeControllerVisual] Starting Meta runtime controller model ({hand}, " +
                  $"shader={_runtimeController.m_controllerModelShader?.name ?? "none"}).");
    }

    private void Update()
    {
        if (!TryActivateLoadedModel())
        {
            TryLoadAfterOpenXrIsReady();
        }
    }

    private void LateUpdate()
    {
        // OVRRuntimeController can finish its coroutine after this component's
        // Update. Clear the SDK compatibility offset before that frame renders.
        TryActivateLoadedModel();
    }

    private bool TryActivateLoadedModel()
    {
        if (_usingRuntimeModel)
        {
            return true;
        }

        if (transform.childCount == 0)
        {
            return false;
        }

        _usingRuntimeModel = true;

        // XR_FB_render_model GLBs use the OpenXR grip pose as their origin.
        // Meta XR SDK v72 applies a legacy -3 cm/-4 cm/-60 degree transform to
        // the component object while loading, so clear exactly that transform.
        transform.localPosition = Vector3.zero;
        transform.localRotation = Quaternion.identity;
        transform.localScale = Vector3.one;

        DisableStaticFallback();
        Debug.Log($"[QuestRuntimeControllerVisual] Loaded Meta runtime controller model ({hand}, " +
                  $"pose=OpenXR grip, modelOffset=zero).");
        return true;
    }

    private void TryLoadAfterOpenXrIsReady()
    {
        if (Time.unscaledTime < _nextLoadAttemptTime)
        {
            return;
        }

        _nextLoadAttemptTime = Time.unscaledTime + 0.5f;
        if (_loadControllerModel == null)
        {
            return;
        }

        string modelPath = hand == ControllerHand.Left
            ? "/model_fb/controller/left"
            : "/model_fb/controller/right";
        string[] runtimePaths = OVRPlugin.GetRenderModelPaths();
        if (!_reportedRuntimePaths)
        {
            _reportedRuntimePaths = true;
            OVRInput.Controller controller = hand == ControllerHand.Left
                ? OVRInput.Controller.LTouch
                : OVRInput.Controller.RTouch;
            Debug.Log($"[QuestRuntimeControllerVisual] Runtime paths ({hand}, " +
                      $"connected={OVRInput.IsControllerConnected(controller)}): " +
                      (runtimePaths.Length == 0 ? "none" : string.Join(", ", runtimePaths)));
        }

        if (!Array.Exists(runtimePaths, path => path == modelPath))
        {
            return;
        }

        try
        {
            _loadControllerModel.Invoke(_runtimeController, new object[] { modelPath });
        }
        catch (Exception exception)
        {
            Debug.LogException(exception, this);
            _loadControllerModel = null;
        }
    }

    private void DisableStaticFallback()
    {
        if (staticFallback == null)
        {
            return;
        }

        staticFallback.enabled = false;
        SetInactive(staticFallback.m_modelOculusTouchQuestAndRiftSLeftController);
        SetInactive(staticFallback.m_modelOculusTouchQuestAndRiftSRightController);
        SetInactive(staticFallback.m_modelOculusTouchRiftLeftController);
        SetInactive(staticFallback.m_modelOculusTouchRiftRightController);
        SetInactive(staticFallback.m_modelOculusTouchQuest2LeftController);
        SetInactive(staticFallback.m_modelOculusTouchQuest2RightController);
        SetInactive(staticFallback.m_modelMetaTouchProLeftController);
        SetInactive(staticFallback.m_modelMetaTouchProRightController);
        SetInactive(staticFallback.m_modelMetaTouchPlusLeftController);
        SetInactive(staticFallback.m_modelMetaTouchPlusRightController);
    }

    private static void SetInactive(GameObject model)
    {
        if (model != null)
        {
            model.SetActive(false);
        }
    }
}
