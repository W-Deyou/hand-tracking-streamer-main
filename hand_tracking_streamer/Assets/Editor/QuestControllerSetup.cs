using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using TMPro;

/// <summary>
/// Installs Meta Interaction SDK controller tracking and ray interactors into
/// the existing streamer scene. The operation is idempotent and can safely be
/// run again after upgrading the Meta XR packages.
/// </summary>
[InitializeOnLoad]
public static class QuestControllerSetup
{
    private const string TargetSceneSuffix = "Assets/Scenes/Scene.unity";
    private const string ControllerInteractionBlockId = "f10154e0-16b2-492f-97d0-6639f69e7df6";
    private const string ProjectConfigPath = "Assets/Oculus/OculusProjectConfig.asset";
    private static readonly string[] ControllerModelProperties =
    {
        "m_modelOculusTouchQuestAndRiftSLeftController",
        "m_modelOculusTouchQuestAndRiftSRightController",
        "m_modelOculusTouchRiftLeftController",
        "m_modelOculusTouchRiftRightController",
        "m_modelOculusTouchQuest2LeftController",
        "m_modelOculusTouchQuest2RightController",
        "m_modelMetaTouchProLeftController",
        "m_modelMetaTouchProRightController",
        "m_modelMetaTouchPlusLeftController",
        "m_modelMetaTouchPlusRightController"
    };
    private static bool _isRunning;

    static QuestControllerSetup()
    {
        EditorApplication.delayCall += TryRunAutomatically;
        EditorSceneManager.sceneOpened += OnSceneOpened;
    }

    private static void OnSceneOpened(Scene scene, OpenSceneMode mode)
    {
        EditorApplication.delayCall += TryRunAutomatically;
    }

    [MenuItem("Tools/Hand Tracking Streamer/Setup Quest Controllers")]
    public static void RunFromMenu()
    {
        _ = ConfigureAsync(true);
    }

    private static void TryRunAutomatically()
    {
        if (Application.isBatchMode || EditorApplication.isPlayingOrWillChangePlaymode)
        {
            return;
        }

        Scene scene = SceneManager.GetActiveScene();
        if (!scene.IsValid() || !scene.path.Replace('\\', '/').EndsWith(TargetSceneSuffix, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        // Once the scene contains the controller block and AppManager has both
        // hand rays plus both controller rays, future script reloads are no-ops.
        AppManager configuredManager = Resources.FindObjectsOfTypeAll<AppManager>()
            .FirstOrDefault(manager => manager.gameObject.scene == scene);
        bool controllerBlockExists = GameObject.Find("[BuildingBlock] Controller Interactions") != null;
        int quest3PointerCorrectors = Resources.FindObjectsOfTypeAll<Quest3ControllerPointerPoseCorrector>()
            .Count(corrector => corrector.gameObject.scene == scene);
        int calibratedControllerVisuals = scene.GetRootGameObjects()
            .SelectMany(root => root.GetComponentsInChildren<Transform>(true))
            .Count(transform =>
                transform.name == "[BuildingBlock] Controller Tracking Left"
                    ? Approximately(transform.localPosition, new Vector3(-0.005f, 0f, 0.06f))
                    : transform.name == "[BuildingBlock] Controller Tracking Right" &&
                      Approximately(transform.localPosition, new Vector3(0.005f, 0f, 0.06f)));
        bool quest3OnlyControllerPreview = IsQuest3OnlyControllerPreview(scene);
        int controllerStreamers = Resources.FindObjectsOfTypeAll<ControllerInputStreamer>()
            .Count(streamer => streamer.gameObject.scene == scene);
        if (controllerBlockExists && configuredManager != null &&
            configuredManager.rayInteractors != null && configuredManager.rayInteractors.Length >= 4 &&
            quest3PointerCorrectors >= 2 && calibratedControllerVisuals >= 2 &&
            quest3OnlyControllerPreview && controllerStreamers >= 2 &&
            configuredManager.controllerInputToggle != null)
        {
            return;
        }

        _ = ConfigureAsync(false);
    }

    private static async Task ConfigureAsync(bool showDialog)
    {
        if (_isRunning || EditorApplication.isCompiling || EditorApplication.isUpdating)
        {
            return;
        }

        _isRunning = true;
        try
        {
            Scene scene = SceneManager.GetActiveScene();
            if (!scene.IsValid() || !scene.path.Replace('\\', '/').EndsWith(TargetSceneSuffix, StringComparison.OrdinalIgnoreCase))
            {
                if (showDialog)
                {
                    EditorUtility.DisplayDialog("Quest Controller Setup", "Please open Assets/Scenes/Scene.unity first.", "OK");
                }
                return;
            }

            ConfigureTrackingModes();
            await EnsureControllerInteractionBlockAsync();

            List<GameObject> controllerRays = EnsureControllerRays(scene);
            RegisterRaysWithAppManager(scene, controllerRays);
            ConfigureQuest3PointerCorrectors(scene);
            ConfigureControllerVisualTranslation(scene);
            ConfigureQuest3ControllerModelPreview(scene);
            ConfigureControllerTelemetry(scene);
            ConfigureControllerModeToggle(scene);

            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
            AssetDatabase.SaveAssets();

            Debug.Log($"[QuestControllerSetup] Complete. Registered {controllerRays.Count} controller ray(s). " +
                      "Quest Touch trigger can now operate the existing menu while hand tracking remains enabled.");

            if (showDialog)
            {
                EditorUtility.DisplayDialog(
                    "Quest Controller Setup",
                    $"Setup complete. Registered {controllerRays.Count} controller ray(s).\n\nBuild and Run on Quest, then use the Touch trigger to click the menu.",
                    "OK");
            }
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            if (showDialog)
            {
                EditorUtility.DisplayDialog("Quest Controller Setup Failed", exception.GetBaseException().Message, "OK");
            }
        }
        finally
        {
            _isRunning = false;
        }
    }

    private static void ConfigureTrackingModes()
    {
        UnityEngine.Object config = AssetDatabase.LoadMainAssetAtPath(ProjectConfigPath);
        if (config != null)
        {
            SerializedObject serializedConfig = new SerializedObject(config);
            SerializedProperty handTrackingSupport = serializedConfig.FindProperty("handTrackingSupport");
            if (handTrackingSupport != null)
            {
                // OVRProjectConfig.HandTrackingSupport.ControllersAndHands
                handTrackingSupport.intValue = 1;
                serializedConfig.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(config);
            }
        }

        Type managerType = FindType("OVRManager");
        foreach (Component manager in FindSceneComponents(managerType, SceneManager.GetActiveScene()))
        {
            SerializedObject serializedManager = new SerializedObject(manager);
            SetBoolean(serializedManager, "launchSimultaneousHandsControllersOnStartup", true);
            SetBoolean(serializedManager, "SimultaneousHandsAndControllersEnabled", true);
            serializedManager.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(manager);
        }
    }

    private static void SetBoolean(SerializedObject owner, string propertyName, bool value)
    {
        SerializedProperty property = owner.FindProperty(propertyName);
        if (property != null)
        {
            property.boolValue = value;
        }
    }

    private static async Task EnsureControllerInteractionBlockAsync()
    {
        Type utilsType = FindType("Meta.XR.BuildingBlocks.Editor.Utils");
        MethodInfo getBlock = utilsType.GetMethod("GetBlock", BindingFlags.Public | BindingFlags.Static, null,
            new[] { typeof(string) }, null);
        if (getBlock != null && getBlock.Invoke(null, new object[] { ControllerInteractionBlockId }) != null)
        {
            return;
        }

        MethodInfo getBlockData = utilsType.GetMethod("GetBlockData", BindingFlags.Public | BindingFlags.Static, null,
            new[] { typeof(string) }, null);
        object blockData = getBlockData?.Invoke(null, new object[] { ControllerInteractionBlockId });
        if (blockData == null)
        {
            throw new InvalidOperationException("Meta XR Controller Interactions Building Block was not found. " +
                                                "Make sure Meta XR Interaction SDK OVR is installed.");
        }

        MethodInfo install = blockData.GetType().GetMethod(
            "InstallWithDependencies",
            BindingFlags.Instance | BindingFlags.NonPublic,
            null,
            new[] { typeof(GameObject) },
            null);
        if (install == null)
        {
            throw new MissingMethodException(blockData.GetType().FullName, "InstallWithDependencies(GameObject)");
        }

        Task installTask = install.Invoke(blockData, new object[] { null }) as Task;
        if (installTask == null)
        {
            throw new InvalidOperationException("Meta XR controller Building Block installation did not return a task.");
        }
        await installTask;
    }

    private static List<GameObject> EnsureControllerRays(Scene scene)
    {
        Type controllerType = FindType("Oculus.Interaction.Input.Controller");
        Type hmdType = FindType("Oculus.Interaction.Input.Hmd");
        Type rayInteractorType = FindType("Oculus.Interaction.RayInteractor");
        Type interactorGroupType = FindType("Oculus.Interaction.InteractorGroup");
        Type interactorUtilsType = FindType("Oculus.Interaction.Editor.QuickActions.InteractorUtils");
        Type interactorTypesType = FindType("Oculus.Interaction.Editor.QuickActions.InteractorTypes");

        Component hmd = FindSceneComponents(hmdType, scene).FirstOrDefault();
        if (hmd == null)
        {
            throw new InvalidOperationException("Interaction SDK HMD input component was not found in the scene.");
        }

        MethodInfo addControllerInteractors = interactorUtilsType.GetMethod(
            "AddInteractorsToController",
            BindingFlags.Public | BindingFlags.Static);
        if (addControllerInteractors == null)
        {
            throw new MissingMethodException(interactorUtilsType.FullName, "AddInteractorsToController");
        }

        object rayFlag = Enum.Parse(interactorTypesType, "Ray");
        List<GameObject> rayRoots = new List<GameObject>();

        foreach (Component controller in FindSceneComponents(controllerType, scene))
        {
            Transform parent = controller.transform.Find("ControllerInteractors") ?? controller.transform;
            Component existingRay = controller.GetComponentsInChildren(rayInteractorType, true)
                .OfType<Component>()
                .FirstOrDefault();

            if (existingRay == null)
            {
                Component group = parent.GetComponent(interactorGroupType);
                IEnumerable created = addControllerInteractors.Invoke(null,
                    new object[] { rayFlag, controller, hmd, parent, group }) as IEnumerable;
                if (created != null)
                {
                    foreach (object item in created)
                    {
                        if (item is GameObject createdObject)
                        {
                            rayRoots.Add(createdObject);
                        }
                    }
                }
            }
            else
            {
                Transform root = existingRay.transform;
                while (root.parent != null && root.parent != parent && root.IsChildOf(parent))
                {
                    root = root.parent;
                }
                rayRoots.Add(root.gameObject);
            }
        }

        return rayRoots.Where(ray => ray != null).Distinct().ToList();
    }

    private static void RegisterRaysWithAppManager(Scene scene, IEnumerable<GameObject> controllerRays)
    {
        AppManager appManager = Resources.FindObjectsOfTypeAll<AppManager>()
            .FirstOrDefault(manager => manager.gameObject.scene == scene);
        if (appManager == null)
        {
            throw new InvalidOperationException("AppManager was not found in the active streamer scene.");
        }

        List<GameObject> rays = (appManager.rayInteractors ?? Array.Empty<GameObject>())
            .Where(ray => ray != null)
            .ToList();
        foreach (GameObject controllerRay in controllerRays)
        {
            if (controllerRay != null && !rays.Contains(controllerRay))
            {
                rays.Add(controllerRay);
            }
        }

        Undo.RecordObject(appManager, "Register Quest controller rays");
        appManager.rayInteractors = rays.ToArray();
        EditorUtility.SetDirty(appManager);
    }

    private static void ConfigureQuest3PointerCorrectors(Scene scene)
    {
        foreach (GameObject root in scene.GetRootGameObjects())
        {
            foreach (Transform transform in root.GetComponentsInChildren<Transform>(true))
            {
                if (transform.name != "ControllerPointerPose")
                {
                    continue;
                }

                Transform owner = transform.parent;
                while (owner != null && owner.name != "LeftController" && owner.name != "RightController")
                {
                    owner = owner.parent;
                }
                if (owner == null)
                {
                    continue;
                }

                Quest3ControllerPointerPoseCorrector corrector =
                    transform.GetComponent<Quest3ControllerPointerPoseCorrector>();
                if (corrector == null)
                {
                    corrector = Undo.AddComponent<Quest3ControllerPointerPoseCorrector>(transform.gameObject);
                }

                Undo.RecordObject(corrector, "Configure Quest 3 controller pointer pose");
                corrector.Configure(owner.name == "LeftController"
                    ? Quest3ControllerPointerPoseCorrector.ControllerHand.Left
                    : Quest3ControllerPointerPoseCorrector.ControllerHand.Right);
                EditorUtility.SetDirty(corrector);
            }
        }
    }

    private static void ConfigureControllerVisualTranslation(Scene scene)
    {
        foreach (GameObject root in scene.GetRootGameObjects())
        {
            foreach (Transform transform in root.GetComponentsInChildren<Transform>(true))
            {
                if (transform.name != "[BuildingBlock] Controller Tracking Left" &&
                    transform.name != "[BuildingBlock] Controller Tracking Right")
                {
                    continue;
                }

                Undo.RecordObject(transform, "Calibrate Quest 3 controller visual position");
                transform.localPosition = transform.name.EndsWith("Left", StringComparison.Ordinal)
                    ? new Vector3(-0.005f, 0f, 0.06f)
                    : new Vector3(0.005f, 0f, 0.06f);
                EditorUtility.SetDirty(transform);
            }
        }
    }

    private static bool Approximately(Vector3 first, Vector3 second)
    {
        return (first - second).sqrMagnitude < 0.00000001f;
    }

    private static void ConfigureQuest3ControllerModelPreview(Scene scene)
    {
        Type helperType = FindType("OVRControllerHelper");
        foreach (Component helper in FindSceneComponents(helperType, scene))
        {
            SerializedObject serializedHelper = new SerializedObject(helper);
            bool isLeft = serializedHelper.FindProperty("m_controller")?.intValue == 1;
            string visibleProperty = isLeft
                ? "m_modelMetaTouchPlusLeftController"
                : "m_modelMetaTouchPlusRightController";

            foreach (string propertyName in ControllerModelProperties)
            {
                GameObject model = serializedHelper.FindProperty(propertyName)?.objectReferenceValue as GameObject;
                if (model == null)
                {
                    continue;
                }

                bool shouldBeActive = propertyName == visibleProperty;
                if (model.activeSelf != shouldBeActive)
                {
                    Undo.RecordObject(model, "Configure Quest 3 controller model preview");
                    model.SetActive(shouldBeActive);
                    EditorUtility.SetDirty(model);
                }
            }
        }
    }

    private static bool IsQuest3OnlyControllerPreview(Scene scene)
    {
        Type helperType = FindType("OVRControllerHelper");
        Component[] helpers = FindSceneComponents(helperType, scene).ToArray();
        if (helpers.Length < 2)
        {
            return false;
        }

        foreach (Component helper in helpers)
        {
            SerializedObject serializedHelper = new SerializedObject(helper);
            bool isLeft = serializedHelper.FindProperty("m_controller")?.intValue == 1;
            string visibleProperty = isLeft
                ? "m_modelMetaTouchPlusLeftController"
                : "m_modelMetaTouchPlusRightController";

            foreach (string propertyName in ControllerModelProperties)
            {
                GameObject model = serializedHelper.FindProperty(propertyName)?.objectReferenceValue as GameObject;
                if (model != null && model.activeSelf != (propertyName == visibleProperty))
                {
                    return false;
                }
            }
        }

        return true;
    }

    private static void ConfigureControllerTelemetry(Scene scene)
    {
        Type controllerType = FindType("Oculus.Interaction.Input.Controller");
        foreach (Component controller in FindSceneComponents(controllerType, scene))
        {
            bool isLeft = controller.gameObject.name.IndexOf("Left", StringComparison.OrdinalIgnoreCase) >= 0;
            bool isRight = controller.gameObject.name.IndexOf("Right", StringComparison.OrdinalIgnoreCase) >= 0;
            if (!isLeft && !isRight)
            {
                continue;
            }

            ControllerAxisVisualizer visualizer = controller.GetComponent<ControllerAxisVisualizer>();
            if (visualizer == null)
            {
                visualizer = Undo.AddComponent<ControllerAxisVisualizer>(controller.gameObject);
            }

            ControllerInputStreamer streamer = controller.GetComponent<ControllerInputStreamer>();
            if (streamer == null)
            {
                streamer = Undo.AddComponent<ControllerInputStreamer>(controller.gameObject);
            }

            SerializedObject serializedStreamer = new SerializedObject(streamer);
            serializedStreamer.FindProperty("controllerSide").enumValueIndex = isLeft ? 0 : 1;
            serializedStreamer.FindProperty("hudLogSource").stringValue = isLeft ? "Left" : "Right";
            serializedStreamer.FindProperty("axisVisualizer").objectReferenceValue = visualizer;
            serializedStreamer.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(streamer);
        }
    }

    private static void ConfigureControllerModeToggle(Scene scene)
    {
        AppManager appManager = Resources.FindObjectsOfTypeAll<AppManager>()
            .FirstOrDefault(manager => manager.gameObject.scene == scene);
        if (appManager == null)
        {
            throw new InvalidOperationException("AppManager was not found in the active streamer scene.");
        }

        Toggle controllerToggle = Resources.FindObjectsOfTypeAll<Toggle>()
            .FirstOrDefault(toggle => toggle.gameObject.scene == scene && toggle.gameObject.name == "Tgl_ControllerInput");
        if (controllerToggle == null)
        {
            Toggle template = Resources.FindObjectsOfTypeAll<Toggle>()
                .FirstOrDefault(toggle => toggle.gameObject.scene == scene && toggle.gameObject.name == "Tgl_HeadPose");
            Transform row = scene.GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<Transform>(true))
                .FirstOrDefault(transform => transform.name == "Debug_Experimental");
            if (template == null || row == null)
            {
                throw new InvalidOperationException("Controller mode toggle template or Debug_Experimental row was not found.");
            }

            controllerToggle = UnityEngine.Object.Instantiate(template, row, false);
            controllerToggle.gameObject.name = "Tgl_ControllerInput";
            controllerToggle.isOn = false;
            controllerToggle.onValueChanged = new Toggle.ToggleEvent();
            TextMeshProUGUI label = controllerToggle.GetComponentInChildren<TextMeshProUGUI>(true);
            if (label != null)
            {
                label.text = "Controller Input";
            }
            Undo.RegisterCreatedObjectUndo(controllerToggle.gameObject, "Add controller input toggle");
        }

        Undo.RecordObject(appManager, "Register controller input toggle");
        appManager.controllerInputToggle = controllerToggle;
        EditorUtility.SetDirty(controllerToggle);
        EditorUtility.SetDirty(appManager);
    }

    private static IEnumerable<Component> FindSceneComponents(Type type, Scene scene)
    {
        if (type == null)
        {
            return Enumerable.Empty<Component>();
        }

        return Resources.FindObjectsOfTypeAll(type)
            .OfType<Component>()
            .Where(component => component != null && component.gameObject.scene == scene);
    }

    private static Type FindType(string fullName)
    {
        Type type = AppDomain.CurrentDomain.GetAssemblies()
            .Select(assembly => assembly.GetType(fullName, false))
            .FirstOrDefault(candidate => candidate != null);
        if (type == null)
        {
            throw new TypeLoadException($"Required Meta XR type was not found: {fullName}");
        }
        return type;
    }
}
