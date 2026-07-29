$ErrorActionPreference = 'Stop'

$workspace = 'D:\hand-tracking-streamer-main\hand-tracking-streamer-main'
$downloads = Join-Path $workspace 'unity-android-downloads'
$sevenZip = Join-Path $workspace '7zip-portable\7z.exe'
$android = 'C:\Program Files\Unity\Hub\Editor\6000.0.65f1\Editor\Data\PlaybackEngines\AndroidPlayer'
$sdk = Join-Path $android 'SDK'
$log = Join-Path $workspace 'unity-android-toolchain-install.log'

Start-Transcript -Path $log -Force

function Expand-ToolArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    & $sevenZip x $Archive "-o$Destination" -y
    if ($LASTEXITCODE -gt 1) {
        throw "7-Zip failed with exit code $LASTEXITCODE for $Archive"
    }
}

Expand-ToolArchive (Join-Path $downloads 'openjdk.zip') (Join-Path $android 'OpenJDK')
Expand-ToolArchive (Join-Path $downloads 'sdk-tools.zip') $sdk

Expand-ToolArchive (Join-Path $downloads 'ndk-r27c.zip') $android
Move-Item -LiteralPath (Join-Path $android 'android-ndk-r27c') -Destination (Join-Path $android 'NDK') -Force

Expand-ToolArchive (Join-Path $downloads 'cmake-3.22.1.zip') (Join-Path $sdk 'cmake\3.22.1')

$buildTools = Join-Path $sdk 'build-tools'
Expand-ToolArchive (Join-Path $downloads 'build-tools-36.zip') $buildTools
Move-Item -LiteralPath (Join-Path $buildTools 'android-16') -Destination (Join-Path $buildTools '36.0.0') -Force

Expand-ToolArchive (Join-Path $downloads 'platform-tools-36.zip') $sdk
Expand-ToolArchive (Join-Path $downloads 'platform-34.zip') (Join-Path $sdk 'platforms')
Expand-ToolArchive (Join-Path $downloads 'platform-35.zip') (Join-Path $sdk 'platforms')
Expand-ToolArchive (Join-Path $downloads 'platform-36.zip') (Join-Path $sdk 'platforms')

$commandLineTools = Join-Path $sdk 'cmdline-tools'
Expand-ToolArchive (Join-Path $downloads 'cmdline-tools-16.zip') $commandLineTools
Move-Item -LiteralPath (Join-Path $commandLineTools 'cmdline-tools') -Destination (Join-Path $commandLineTools '16.0') -Force

$requiredFiles = @(
    (Join-Path $android 'OpenJDK\bin\java.exe'),
    (Join-Path $android 'NDK\ndk-build.cmd'),
    (Join-Path $sdk 'platform-tools\adb.exe'),
    (Join-Path $sdk 'build-tools\36.0.0\aapt2.exe'),
    (Join-Path $sdk 'platforms\android-34\android.jar'),
    (Join-Path $sdk 'platforms\android-35\android.jar'),
    (Join-Path $sdk 'platforms\android-36\android.jar'),
    (Join-Path $sdk 'cmdline-tools\16.0\bin\sdkmanager.bat'),
    (Join-Path $sdk 'cmake\3.22.1\bin\cmake.exe')
)

$missing = $requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) {
    throw "Missing required Android toolchain files: $($missing -join ', ')"
}

Write-Output 'UNITY_ANDROID_TOOLCHAIN_INSTALL_COMPLETE'
Stop-Transcript
