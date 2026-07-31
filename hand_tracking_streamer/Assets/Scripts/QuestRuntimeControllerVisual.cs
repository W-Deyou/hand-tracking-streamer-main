using UnityEngine;

/// <summary>
/// Hides Meta controller mesh models while leaving Interaction SDK rays and
/// <see cref="ControllerAxisVisualizer"/> endpoint axes untouched.
/// </summary>
[DisallowMultipleComponent]
[DefaultExecutionOrder(-10)]
public sealed class QuestRuntimeControllerVisual : MonoBehaviour
{
    [SerializeField] private OVRControllerHelper staticFallback;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void HideControllerModelsPreserveRaysAndAxes()
    {
        ApplyAllVisibility(visible: false);
    }

    /// <summary>
    /// Virtual controller meshes are display-only. When <paramref name="visible"/>
    /// is false (default for teleoperation), hide static and runtime meshes.
    /// </summary>
    public static void ApplyAllVisibility(bool visible)
    {
        foreach (OVRControllerHelper helper in FindObjectsByType<OVRControllerHelper>(
                     FindObjectsInactive.Include,
                     FindObjectsSortMode.None))
        {
            if (!visible)
            {
                HideHelperModels(helper);
            }
        }

        foreach (Transform transform in FindObjectsByType<Transform>(
                     FindObjectsInactive.Include,
                     FindObjectsSortMode.None))
        {
            if (transform == null)
            {
                continue;
            }

            string objectName = transform.name;
            if (objectName != "[Runtime] Controller Visual Left" &&
                objectName != "[Runtime] Controller Visual Right")
            {
                continue;
            }

            if (!visible)
            {
                Destroy(transform.gameObject);
            }
        }
    }

    private void Awake()
    {
        if (staticFallback != null)
        {
            HideHelperModels(staticFallback);
        }
    }

    private void LateUpdate()
    {
        // OVRControllerHelper can re-enable a mesh when the active controller
        // type changes; keep meshes suppressed every frame.
        if (staticFallback != null)
        {
            HideHelperModels(staticFallback);
        }
    }

    public void Configure(OVRControllerHelper fallback)
    {
        staticFallback = fallback;
        HideHelperModels(staticFallback);
    }

    private static void HideHelperModels(OVRControllerHelper helper)
    {
        if (helper == null)
        {
            return;
        }

        helper.enabled = false;
        SetInactive(helper.m_modelOculusTouchQuestAndRiftSLeftController);
        SetInactive(helper.m_modelOculusTouchQuestAndRiftSRightController);
        SetInactive(helper.m_modelOculusTouchRiftLeftController);
        SetInactive(helper.m_modelOculusTouchRiftRightController);
        SetInactive(helper.m_modelOculusTouchQuest2LeftController);
        SetInactive(helper.m_modelOculusTouchQuest2RightController);
        SetInactive(helper.m_modelMetaTouchProLeftController);
        SetInactive(helper.m_modelMetaTouchProRightController);
        SetInactive(helper.m_modelMetaTouchPlusLeftController);
        SetInactive(helper.m_modelMetaTouchPlusRightController);
    }

    private static void SetInactive(GameObject model)
    {
        if (model != null && model.activeSelf)
        {
            model.SetActive(false);
        }
    }
}
