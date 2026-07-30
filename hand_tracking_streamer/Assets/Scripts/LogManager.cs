using UnityEngine;
using System.Collections.Generic;

public class LogManager : MonoBehaviour
{
    public static LogManager Instance { get; private set; }

    // Dictionary mapping a source name to its log messages
    private Dictionary<string, List<string>> logMessages = new Dictionary<string, List<string>>();
    // High-rate telemetry should replace its previous value instead of growing
    // the history forever. Displays can combine independent snapshots without
    // relying on the relative update order of the left and right devices.
    private readonly Dictionary<string, string> latestSnapshots = new Dictionary<string, string>();

    private void Awake()
    {
        if (Instance == null)
        {
            Instance = this;
            DontDestroyOnLoad(gameObject);
        }
        else
        {
            Destroy(gameObject);
        }
    }

    // Log a message to a specific source
    public void Log(string source, string message)
    {
        if (!logMessages.ContainsKey(source))
        {
            logMessages[source] = new List<string>();
        }
        logMessages[source].Add(message);
        Debug.Log($"[{source}] {message}");
    }

    // Get log messages for a specific source
    public List<string> GetLogMessages(string source)
    {
        if (logMessages.ContainsKey(source))
        {
            return logMessages[source];
        }
        return new List<string>();
    }

    public void SetLatestSnapshot(string source, string message)
    {
        if (string.IsNullOrEmpty(source)) return;
        latestSnapshots[source] = message ?? string.Empty;
        Debug.Log($"[{source}] {message}");
    }

    public bool TryGetLatestSnapshot(string source, out string message)
    {
        if (string.IsNullOrEmpty(source))
        {
            message = string.Empty;
            return false;
        }
        return latestSnapshots.TryGetValue(source, out message);
    }

    public void ClearLatestSnapshot(string source)
    {
        if (!string.IsNullOrEmpty(source))
        {
            latestSnapshots.Remove(source);
        }
    }
}
