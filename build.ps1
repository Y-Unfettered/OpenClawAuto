$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$exeName = "OpenClawSetupTool"
$distRoot = Join-Path $projectRoot ".dist-build"
$buildRoot = Join-Path $projectRoot ".pyinstaller-build"
$releaseBaseRoot = Join-Path $projectRoot "release"
$releaseRoot = Join-Path $releaseBaseRoot $exeName
$releaseStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fallbackReleaseRoot = Join-Path $releaseBaseRoot "$exeName-$releaseStamp"

Push-Location $projectRoot
try {
    python -m PyInstaller --version | Out-Null

    if (Test-Path $buildRoot) {
        Remove-Item -Recurse -Force $buildRoot
    }
    if (Test-Path $distRoot) {
        Remove-Item -Recurse -Force $distRoot
    }

    $outputRoot = $releaseRoot
    if (Test-Path $releaseRoot) {
        try {
            Remove-Item -Recurse -Force $releaseRoot
        }
        catch {
            $outputRoot = $fallbackReleaseRoot
        }
    }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --uac-admin `
        --name $exeName `
        --distpath $distRoot `
        --workpath $buildRoot `
        --specpath $projectRoot `
        main.py

    New-Item -ItemType Directory -Force $outputRoot | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $outputRoot "payload") | Out-Null

    Copy-Item (Join-Path $distRoot "$exeName.exe") (Join-Path $outputRoot "$exeName.exe")
    Copy-Item (Join-Path $projectRoot "README.md") (Join-Path $outputRoot "README.md")
    Copy-Item (Join-Path $projectRoot "installer-config.template.json") (Join-Path $outputRoot "installer-config.template.json")
    Copy-Item (Join-Path $projectRoot "payload\README.txt") (Join-Path $outputRoot "payload\README.txt")

    Write-Host "构建完成：$outputRoot"
}
finally {
    Pop-Location
}
