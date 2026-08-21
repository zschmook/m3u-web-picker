[CmdletBinding()]
param(
    [switch]$CleanVolumes,
    [switch]$ConfigureOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

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

Set-DotEnvValue -Path $envPath -Name "M3U_LAN_HOST" -Value $lanHost
Write-Host "Advertising M3U Web Picker at http://${lanHost}:9999"

if ($ConfigureOnly) {
    return
}

Push-Location $repoRoot
try {
    if ($CleanVolumes) {
        docker compose -f docker-compose.yml down -v
    }
    else {
        docker compose -f docker-compose.yml down
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    docker compose -f docker-compose.yml up -d --build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    docker compose -f docker-compose.yml ps
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
