# Auto-elevate to Administrator privileges if not already elevated
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $ScriptDir "config.json"

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "   Dune: Awakening - Host Environment Initializer   " -ForegroundColor Cyan
Write-Host "===================================================`n" -ForegroundColor Cyan

# 1. Parse configuration or prepare default values
$VmUser = "dune"
$VmIp = ""
$SwitchAlias = "vEthernet (DuneAwakeningServerSwitch)"

if (Test-Path $ConfigFile) {
    try {
        $Config = Get-Content -Raw -Path$ConfigFile | ConvertFrom-Json
        if (-not [string]::IsNullOrWhiteSpace($Config.vm_user)) { $VmUser =$Config.vm_user }
        if (-not [string]::IsNullOrWhiteSpace($Config.vm_ip)) { $VmIp =$Config.vm_ip }
        if (-not [string]::IsNullOrWhiteSpace($Config.switch_name)) { $SwitchAlias =$Config.switch_name }
    } catch {
        Write-Host "[-] Warning: Failed to parse 'config.json'. Using fallback defaults." -ForegroundColor Yellow
    }
}

# 2. Resolve VM IP via Hyper-V subsystem if not provided
if ([string]::IsNullOrWhiteSpace($VmIp)) {
    Write-Host "[*] Detecting VM IP via Hyper-V subsystem..." -ForegroundColor Cyan
    try {
        $VmAdapters = Get-VMNetworkAdapter -All -ErrorAction SilentlyContinue | Where-Object { $_.SwitchName -like "*Dune*" -or $_.VMName -like "*Dune*" }
        $VmIp = ($VmAdapters.IPAddresses \vert{} Where-Object {$_ -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$' -and$_ -notlike "169.254.*" } | Select-Object -First 1)
    } catch {
        Write-Host "[-] Hyper-V lookup failed." -ForegroundColor Yellow
    }
}

if ([string]::IsNullOrWhiteSpace($VmIp)) {$VmIp = Read-Host "[?] Enter Virtual Machine IP address manually"
}

if ([string]::IsNullOrWhiteSpace($VmIp)) {
    Write-Host "[-] Critical: Virtual Machine IP address is required." -ForegroundColor Red
    Pause
    exit 1
}

Write-Host "[+] Target VM: $VmUser@$VmIp" -ForegroundColor Green

# 3. Check or generate local Ed25519 SSH Key
$SshDir = Join-Path$env:USERPROFILE ".ssh"
$PublicKeyPath = Join-Path$SshDir "id_ed25519.pub"
$PrivateKeyPath = Join-Path$SshDir "id_ed25519"

if (-not (Test-Path $PublicKeyPath)) {
    Write-Host "[*] No Ed25519 key found. Generating new SSH key pair..." -ForegroundColor Cyan
    if (-not (Test-Path $SshDir)) { New-Item -ItemType Directory -Path$SshDir -Force | Out-Null }
    & ssh-keygen -t ed25519 -N '""' -f "$PrivateKeyPath" | Out-Null
    Write-Host "[+] SSH key pair successfully generated." -ForegroundColor Green
} else {
    Write-Host "[+] Existing Ed25519 SSH public key found." -ForegroundColor Green
}

$PublicKeyContent = (Get-Content -Raw -Path$PublicKeyPath).Trim()

# 4. Provision VM via SSH (Idempotent deployment)
Write-Host "`n[*] Configuring VM environment..." -ForegroundColor Cyan
Write-Host "[!] Note: You may be prompted for the '$VmUser' user password ONCE to authorize setup.`n" -ForegroundColor Yellow

$RemoteProvisionScript = @"
set -e
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

if ! grep -qxF '$PublicKeyContent' ~/.ssh/authorized_keys; then
    echo '$PublicKeyContent' >> ~/.ssh/authorized_keys
fi

echo "$VmUser ALL=(ALL) NOPASSWD: /sbin/iptables, /sbin/sysctl" | sudo tee /etc/sudoers.d/dune-iptables > /dev/null
sudo chmod 0440 /etc/sudoers.d/dune-iptables

echo "net.ipv4.ip_forward = 1" | sudo tee /etc/sysctl.d/99-dune.conf > /dev/null
sudo sysctl -p /etc/sysctl.d/99-dune.conf > /dev/null
"@

# Normalize line breaks for Alpine Linux shell execution
$NormalizedScript =$RemoteProvisionScript -replace "`r`n", "`n"

try {
    $NormalizedScript | ssh -o StrictHostKeyChecking=accept-new "$VmUser@$VmIp" "sh"
    Write-Host "`n[+] VM provisioning completed successfully!" -ForegroundColor Green
} catch {
    Write-Host "`n[-] Failed to provision VM via SSH." -ForegroundColor Red
    Pause
    exit 1
}

# 5. Verify passwordless execution
Write-Host "[*] Verifying passwordless sudo execution on VM..." -ForegroundColor Cyan
$Verification = ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$VmUser@$VmIp" "sudo iptables -V" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[+] Passwordless execution verified: $Verification" -ForegroundColor Green
    Write-Host "`n[SUCCESS] Setup complete! You can now use Run-DuneRoute.bat seamlessly." -ForegroundColor Green
} else {
    Write-Host "[-] Verification failed. Manual check required: $Verification" -ForegroundColor Red
}

Pause