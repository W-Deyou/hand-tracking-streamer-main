using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Android;
using UnityEditor.Build.Reporting;

public static class CodexAndroidBuild
{
    public static void BuildApk()
    {
        // Cap IL2CPP/Bee parallelism for low-RAM hosts (avoid machine freeze).
        Environment.SetEnvironmentVariable("UNITY_PLAYER_PROCESS_COUNT", "1");
        Environment.SetEnvironmentVariable("BEE_RESOURCE_CALCULATION_HALF_PROJECTED_MACHINE_MEMORY_USAGE", "1");

        // Unity defaults Gradle JVM heap to 4096 MB; that freezes this 15 Gi host.
        // Docs: AndroidExternalToolsSettings.maxJvmHeapSize (min 128).
        AndroidExternalToolsSettings.maxJvmHeapSize = 1536;

        string outputPath = GetArgument("-apkPath");
        if (string.IsNullOrWhiteSpace(outputPath))
        {
            outputPath = Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(), "..", "hand_tracking_streamer.apk"));
        }

        string outputDirectory = Path.GetDirectoryName(outputPath);
        if (!string.IsNullOrEmpty(outputDirectory))
        {
            Directory.CreateDirectory(outputDirectory);
        }

        string[] scenes = EditorBuildSettings.scenes
            .Where(scene => scene.enabled)
            .Select(scene => scene.path)
            .ToArray();
        if (scenes.Length == 0)
        {
            throw new InvalidOperationException("No enabled scenes are configured for the Android build.");
        }

        EditorUserBuildSettings.buildAppBundle = false;
        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = scenes,
            locationPathName = outputPath,
            target = BuildTarget.Android,
            options = BuildOptions.None,
        });

        if (report.summary.result != BuildResult.Succeeded)
        {
            throw new InvalidOperationException(
                $"Android build failed: {report.summary.result}, errors={report.summary.totalErrors}"
            );
        }
    }

    private static string GetArgument(string name)
    {
        string[] arguments = Environment.GetCommandLineArgs();
        for (int i = 0; i < arguments.Length - 1; i++)
        {
            if (string.Equals(arguments[i], name, StringComparison.OrdinalIgnoreCase))
            {
                return arguments[i + 1];
            }
        }
        return null;
    }
}
