$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    throw "Go is required to build the installer executable."
}

$env:GOOS = "windows"
$env:GOARCH = "amd64"
$env:CGO_ENABLED = "0"

Push-Location $Root
try {
    go build -trimpath -ldflags "-s -w" -o (Join-Path $OutDir "M3U-Web-Picker-Setup.exe") .
} finally {
    Pop-Location
}

Write-Host "Built: $(Join-Path $OutDir 'M3U-Web-Picker-Setup.exe')"
