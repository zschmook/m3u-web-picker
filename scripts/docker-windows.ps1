[CmdletBinding()]
param(
    [switch]$CleanVolumes,
    [switch]$ConfigureOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
$envExamplePath = Join-Path $repoRoot ".env.example"
$envPreexisting = Test-Path -LiteralPath $envPath

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop, then try again."
}

docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is installed but its engine is not running."
}

docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose is unavailable. Update Docker Desktop, then try again."
}

function Get-LanIPv4 {
    $configuration = try {
        Get-NetIPConfiguration -ErrorAction Stop |
            Where-Object {
                $_.IPv4DefaultGateway -and
                $_.NetAdapter.Status -eq "Up" -and
                $_.InterfaceAlias -notmatch "vEthernet|Docker|WSL|Loopback"
            } |
            Sort-Object -Property @{Expression = { $_.NetIPv4Interface.RouteMetric }} |
            Select-Object -First 1
    }
    catch {
        $null
    }

    $address = $configuration.IPv4Address.IPAddress | Select-Object -First 1
    if ($address -and $address -notmatch "^(127\.|169\.254\.)") {
        return $address
    }

    $socket = [System.Net.Sockets.Socket]::new(
        [System.Net.Sockets.AddressFamily]::InterNetwork,
        [System.Net.Sockets.SocketType]::Dgram,
        [System.Net.Sockets.ProtocolType]::Udp
    )
    try {
        $socket.Connect("8.8.8.8", 80)
        return ([System.Net.IPEndPoint]$socket.LocalEndPoint).Address.IPAddressToString
    }
    finally {
        $socket.Dispose()
    }
}

function Set-DotEnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = if (Test-Path -LiteralPath $Path) {
        @(Get-Content -LiteralPath $Path)
    }
    else {
        @()
    }
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
    if (-not $found) {
        $updated += $replacement
    }
    $utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllLines(
        [System.IO.Path]::GetFullPath($Path),
        [string[]]$updated,
        $utf8WithoutBom
    )
}

function Get-DotEnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $prefix = "$Name="
    $line = Get-Content -LiteralPath $Path |
        Where-Object { $_.StartsWith($prefix, [System.StringComparison]::Ordinal) } |
        Select-Object -First 1
    if ($null -eq $line) {
        return $null
    }
    return $line.Substring($prefix.Length).Trim()
}

$lanHost = Get-LanIPv4
if (-not $lanHost) {
    throw "Could not detect this computer's LAN IPv4 address."
}

if (-not (Test-Path -LiteralPath $envPath)) {
    if (Test-Path -LiteralPath $envExamplePath) {
        Copy-Item -LiteralPath $envExamplePath -Destination $envPath
        Write-Host "Created .env from .env.example"
    }
    else {
        New-Item -ItemType File -Path $envPath | Out-Null
    }
}

Set-DotEnvValue -Path $envPath -Name "M3U_LAN_HOST" -Value $lanHost
Write-Host "Advertising M3U Web Picker at http://${lanHost}:9999"

$dvrPath = Get-DotEnvValue -Path $envPath -Name "M3U_DVR_DIR"
if (-not $envPreexisting -or [string]::IsNullOrWhiteSpace($dvrPath)) {
    $dvrPath = "C:/DVR"
    Set-DotEnvValue -Path $envPath -Name "M3U_DVR_DIR" -Value $dvrPath
}
if ($dvrPath -eq "C:/DVR") {
    New-Item -ItemType Directory -Path "C:\DVR" -Force | Out-Null
    Write-Host "Using C:/DVR for persistent DVR recordings."
}

if ($ConfigureOnly) {
    return
}

Push-Location $repoRoot
try {
    $composeArgs = @("-f", "docker-compose.yml")
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $composeArgs += @("-f", "docker-compose.gpu.yml")
        Write-Host "NVIDIA GPU detected; requesting Docker GPU passthrough."
    }

    if ($CleanVolumes) {
        docker compose @composeArgs down -v
    }
    else {
        docker compose @composeArgs down
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    docker compose @composeArgs up -d --build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    docker compose @composeArgs ps
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
