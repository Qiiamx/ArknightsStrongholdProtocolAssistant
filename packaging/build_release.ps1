param(
    [string]$Version = "0.1.0",
    [string]$Repo = "Qiiamx/ArknightsStrongholdProtocolAssistant",
    [string]$PythonExe = "",
    [switch]$SkipWincap,
    [switch]$SkipPyInstaller,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BuildVenv = if ($PythonExe) { $PythonExe } else { Join-Path $Root ".build-venv\Scripts\python.exe" }
$BuildDir = Join-Path $Root "build"
$DistRoot = if ($OutDir) { $OutDir } else { Join-Path $Root "dist" }
$AppDir = Join-Path $DistRoot "ASPA"
$WincapExe = Join-Path $BuildDir "wincap\wincap.exe"

if (-not (Test-Path $BuildVenv)) {
    $cmd = Get-Command $BuildVenv -ErrorAction SilentlyContinue
    if ($cmd) { $BuildVenv = $cmd.Source }
}
if (-not (Test-Path $BuildVenv)) {
    throw "Python not found: $BuildVenv. Create .build-venv or pass -PythonExe."
}

# 1. Self-contained, compressed wincap (no .NET runtime required on the user's PC).
if (-not $SkipWincap) {
    Write-Host "[1/5] Publishing wincap.exe (self-contained)..." -ForegroundColor Cyan
    dotnet publish (Join-Path $Root "capture\src\WinCap.Cli\WinCap.Cli.csproj") `
        -c Release -r win-x64 --self-contained true `
        -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
        -p:EnableCompressionInSingleFile=true `
        -o (Join-Path $BuildDir "wincap") | Out-Null
}
if (-not (Test-Path $WincapExe)) { throw "wincap.exe not found: $WincapExe" }

# 2. Version injected into the frozen app.
Write-Host "[2/5] Writing version $Version..." -ForegroundColor Cyan
$versionModule = Join-Path $Root "spa_quicklook\_build_version.py"
"__version__ = `"$Version`"" | Set-Content -Path $versionModule -Encoding utf8

# 3. PyInstaller one-dir.
if (-not $SkipPyInstaller) {
    Write-Host "[3/5] Building ASPA.exe..." -ForegroundColor Cyan
    $env:WINCAP_EXE = $WincapExe
    Push-Location $Root
    try {
        & $BuildVenv -m PyInstaller --noconfirm --clean (Join-Path $Root "packaging\ASPA.spec") | Out-Null
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path (Join-Path $AppDir "ASPA.exe"))) {
    throw "PyInstaller output missing: $AppDir\ASPA.exe"
}

# 4. Runtime files.
Write-Host "[4/5] Assembling runtime..." -ForegroundColor Cyan
Copy-Item $WincapExe (Join-Path $AppDir "wincap.exe") -Force
$update = @{
    repo = $Repo
    asset = "ASPA-win-x64.zip"
    proxy = ""
} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $AppDir "update.json"), $update, (New-Object System.Text.UTF8Encoding($false)))
$versionInfo = @{ version = $Version; repo = $Repo } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $AppDir "version.json"), $versionInfo, (New-Object System.Text.UTF8Encoding($false)))
foreach ($doc in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md")) {
    $src = Join-Path $Root $doc
    if (Test-Path $src) { Copy-Item $src (Join-Path $AppDir $doc) -Force }
}

# 5. Zip + checksum.
Write-Host "[5/5] Packaging zip..." -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = Join-Path $DistRoot "ASPA-win-x64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory($AppDir, $zip)
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"$hash  ASPA-win-x64.zip" | Set-Content -Path "$zip.sha256" -Encoding ascii

$sizeMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Done: $zip ($sizeMb MB)" -ForegroundColor Green
Write-Host "SHA256: $hash"

