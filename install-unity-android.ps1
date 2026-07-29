$ErrorActionPreference = 'Stop'

$unityRoot = 'C:\Program Files\Unity\Hub\Editor\6000.0.65f1'
$unityExe = Join-Path $unityRoot 'Editor\Unity.exe'
$installer = 'D:\hand-tracking-streamer-main\hand-tracking-streamer-main\UnitySetup-Android-Support-for-Editor-6000.0.65f1.exe'
$registryKey = 'HKLM:\Software\Unity Technologies\Installer\Unity 6000.0.65f1'

if (-not (Test-Path -LiteralPath $unityExe)) {
    throw "Unity editor not found at $unityExe"
}

if (-not (Test-Path -LiteralPath $installer)) {
    throw "Android support installer not found at $installer"
}

New-Item -Path $registryKey -Force | Out-Null
New-ItemProperty -Path $registryKey -Name 'Location x64' -PropertyType String -Value $unityRoot -Force | Out-Null
New-ItemProperty -Path $registryKey -Name 'Version' -PropertyType String -Value '6000.0.65f1' -Force | Out-Null

Get-Process -Name 'UnitySetup-Android-Support-for-Editor-6000.0.65f1' -ErrorAction SilentlyContinue |
    Stop-Process -Force

$process = Start-Process -FilePath $installer -ArgumentList '/S' -Wait -PassThru
exit $process.ExitCode
