using UnityEngine;

public sealed class ControllerAxisVisualizer : MonoBehaviour
{
    [SerializeField] private float axisLength = 0.08f;
    [SerializeField] private float axisThickness = 0.003f;

    private GameObject _axisRoot;

    private void Awake()
    {
        EnsureAxes();
        Hide();
    }

    public void SetPose(Pose pose, bool visible)
    {
        EnsureAxes();
        _axisRoot.transform.SetPositionAndRotation(pose.position, pose.rotation);
        _axisRoot.SetActive(visible);
    }

    public void Hide()
    {
        if (_axisRoot != null)
        {
            _axisRoot.SetActive(false);
        }
    }

    private void EnsureAxes()
    {
        if (_axisRoot != null)
        {
            return;
        }

        _axisRoot = new GameObject("ControllerEndpointAxes");
        _axisRoot.transform.SetParent(null, false);
        CreateAxis("X", Vector3.right, Color.red);
        CreateAxis("Y", Vector3.up, Color.green);
        CreateAxis("Z", Vector3.forward, Color.blue);
    }

    private void CreateAxis(string name, Vector3 direction, Color color)
    {
        GameObject shaft = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        shaft.name = name + "Axis";
        shaft.transform.SetParent(_axisRoot.transform, false);
        shaft.transform.localPosition = direction * (axisLength * 0.5f);
        shaft.transform.localRotation = Quaternion.FromToRotation(Vector3.up, direction);
        shaft.transform.localScale = new Vector3(axisThickness, axisLength * 0.5f, axisThickness);
        ConfigurePrimitive(shaft, color);

        GameObject tip = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        tip.name = name + "AxisTip";
        tip.transform.SetParent(_axisRoot.transform, false);
        tip.transform.localPosition = direction * axisLength;
        tip.transform.localScale = Vector3.one * axisThickness * 3f;
        ConfigurePrimitive(tip, color);
    }

    private static void ConfigurePrimitive(GameObject primitive, Color color)
    {
        Collider collider = primitive.GetComponent<Collider>();
        if (collider != null)
        {
            Destroy(collider);
        }

        Renderer renderer = primitive.GetComponent<Renderer>();
        if (renderer == null)
        {
            return;
        }
        renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        renderer.receiveShadows = false;

        Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
        if (shader == null) shader = Shader.Find("Unlit/Color");
        if (shader == null) shader = Shader.Find("Sprites/Default");
        Material material = new Material(shader) { color = color };
        renderer.material = material;
    }

    private void OnDestroy()
    {
        if (_axisRoot != null)
        {
            Destroy(_axisRoot);
            _axisRoot = null;
        }
    }
}
