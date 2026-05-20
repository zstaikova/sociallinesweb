# Run this script once as Administrator to install cloudflared as a Windows service.
# After this, the tunnel starts automatically on boot.

$ErrorActionPreference = "Stop"

$configSrc  = "D:\socialline\cloudflared.yml"
$credsSrc   = "C:\Users\z\.cloudflared\90345bfb-dc29-4892-9a92-3a071fef5b61.json"
$svcDir     = "C:\Windows\System32\config\systemprofile\.cloudflared"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script as Administrator (right-click PowerShell > Run as administrator)."
    exit 1
}

Write-Host "Creating service config directory..."
New-Item -ItemType Directory -Force $svcDir | Out-Null

Write-Host "Copying config and credentials..."
Copy-Item $configSrc  "$svcDir\config.yml" -Force
Copy-Item $credsSrc   "$svcDir\"           -Force

# Remove any existing service before reinstalling
$existing = Get-Service "Cloudflared" -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing service..."
    cloudflared service uninstall
    Start-Sleep -Seconds 2
}

Write-Host "Installing Cloudflare Tunnel as a Windows service..."
cloudflared service install

if ($LASTEXITCODE -ne 0) {
    Write-Error "cloudflared service install failed. Is cloudflared in PATH?"
    exit 1
}

Write-Host "Starting service..."
Start-Service "Cloudflared"
Start-Sleep -Seconds 3

$svc = Get-Service "Cloudflared"
Write-Host "Service status: $($svc.Status)"

if ($svc.Status -eq "Running") {
    Write-Host "Done. Cloudflare Tunnel is running and will auto-start on boot."
} else {
    Write-Warning "Service installed but not running. Check: Get-EventLog -LogName Application -Source cloudflared -Newest 10"
}