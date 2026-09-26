[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$SourceRef = "main",
    [ValidateSet("UP", "CL")]
    [string]$Mode = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $InstallDir = Join-Path $localAppData "m3u-web-picker"
}
$repo = "zschmook/m3u-web-picker"
$archiveUrl = "https://codeload.github.com/$repo/zip/refs/heads/$SourceRef"
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("m3u-web-picker-install-" + [guid]::NewGuid().ToString("N"))
$archivePath = Join-Path $workDir "source.zip"
$extractDir = Join-Path $workDir "source"
$stagingDir = "$InstallDir.staging"
$previousDir = "$InstallDir.previous"

function Remove-InstallerPath([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Copy-PreservedPath([string]$Name, [string]$SourceRoot, [string]$DestinationRoot) {
    $source = Join-Path $SourceRoot $Name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $DestinationRoot $Name) -Recurse -Force
    }
}

function Set-DotEnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path) } else { @() }
    $replacement = "$Name=$Value"
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Name))=") {
            $found = $true
            $replacement
        }
        else {
            $line
        }
    }
    if (-not $found) { $updated += $replacement }
    [System.IO.File]::WriteAllLines(
        [System.IO.Path]::GetFullPath($Path),
        [string[]]$updated,
        [System.Text.UTF8Encoding]::new($false)
    )
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop, then try again."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running or is not ready."
}

$productionStatus = docker ps `
    --filter "label=com.docker.compose.project=m3u-picker" `
    --filter "label=com.docker.compose.service=m3u-picker" `
    --filter "publish=9999" `
    --format "{{.Names}} | {{.Status}} | {{.Ports}}"
if ($productionStatus) {
    Write-Host "Running production M3U Web Picker detected on port 9999:"
    Write-Host "  $productionStatus"
    Write-Host ""
}
else {
    Write-Host "No running production M3U Web Picker was detected on port 9999."
    Write-Host ""
}

$existingInstall = Test-Path -LiteralPath $InstallDir
if (-not $existingInstall) {
    Write-Host "No existing production installation was found at $InstallDir."
    Write-Host "Starting a clean installation."
    Write-Host ""
    $Mode = "CL"
}
else {
    while ($Mode -notin @("UP", "CL")) {
        Write-Host "Choose how to install:"
        Write-Host "  UPGRADE (UP) - update the application and keep its saved setup"
        Write-Host "  CLEAN   (CL) - erase the production app data and run setup again"
        $Mode = (Read-Host "Enter UP or CL").Trim().ToUpperInvariant()
    }
}

if ($Mode -eq "CL" -and $existingInstall) {
    Write-Warning "CLEAN deletes all M3U Web Picker production app data."
    Write-Host "This includes saved channels and settings, provider configuration, DVR schedules,"
    Write-Host "and saved sports automation selections and rules."
    Write-Host "Existing recording files in the separate DVR folder are not deleted."
    $confirmation = (Read-Host "Type CLEAN to continue").Trim().ToUpperInvariant()
    if ($confirmation -ne "CLEAN") {
        throw "Clean installation cancelled; nothing was changed."
    }
}

try {
    New-Item -ItemType Directory -Path $workDir, $extractDir -Force | Out-Null
    Write-Host "Downloading M3U Web Picker ($SourceRef)..."
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath -UseBasicParsing
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractDir -Force

    $sourceRoot = Get-ChildItem -LiteralPath $extractDir -Directory | Select-Object -First 1
    if ($null -eq $sourceRoot -or
        -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "docker-compose.yml")) -or
        -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "scripts\docker-windows.ps1"))) {
        throw "The downloaded ZIP does not contain the expected M3U Web Picker files."
    }

    Remove-InstallerPath $stagingDir
    Move-Item -LiteralPath $sourceRoot.FullName -Destination $stagingDir

    if (Test-Path -LiteralPath $InstallDir) {
        Copy-PreservedPath ".env" $InstallDir $stagingDir
        Copy-PreservedPath "runtime" $InstallDir $stagingDir
        Remove-InstallerPath $previousDir
        Move-Item -LiteralPath $InstallDir -Destination $previousDir
    }

    $envPath = Join-Path $stagingDir ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        Copy-Item -LiteralPath (Join-Path $stagingDir ".env.example") -Destination $envPath
    }
    Set-DotEnvValue -Path $envPath -Name "M3U_HOST_PORT" -Value "9999"
    Set-DotEnvValue -Path $envPath -Name "M3U_EXTERNAL_PORT" -Value "9999"
    $configuredDvr = (Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^M3U_DVR_DIR=\S' } | Select-Object -First 1)
    if (-not $configuredDvr) {
        $videosPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyVideos)
        if ([string]::IsNullOrWhiteSpace($videosPath)) {
            $videosPath = Join-Path $env:USERPROFILE "Videos"
        }
        $dvrPath = Join-Path $videosPath "M3U-Web-Picker-DVR"
        New-Item -ItemType Directory -LiteralPath $dvrPath -Force | Out-Null
        Set-DotEnvValue -Path $envPath -Name "M3U_DVR_DIR" -Value $dvrPath.Replace("\", "/")
    }

    try {
        Move-Item -LiteralPath $stagingDir -Destination $InstallDir
    }
    catch {
        if ((Test-Path -LiteralPath $previousDir) -and -not (Test-Path -LiteralPath $InstallDir)) {
            Move-Item -LiteralPath $previousDir -Destination $InstallDir
        }
        throw
    }

    $setupScript = Join-Path $InstallDir "scripts\docker-windows.ps1"
    if ($Mode -eq "CL") {
        & $setupScript -CleanVolumes
    }
    else {
        & $setupScript
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Docker setup failed with exit code $LASTEXITCODE."
    }

    Remove-InstallerPath $previousDir
    Write-Host ""
    Write-Host "M3U Web Picker is ready: http://localhost:9999"
}
finally {
    Remove-InstallerPath $workDir
}
