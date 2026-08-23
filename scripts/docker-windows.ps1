[CmdletBinding()]
param(
    [switch]$CleanVolumes,
    [switch]$ConfigureOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
$envExamplePath = Join-Path $repoRoot ".env.example"

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
    Set-Content -LiteralPath $Path -Value $updated -Encoding utf8
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
