# Auto-elevate to Administrator privileges if not already elevated
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $ScriptDir "config.json"

# Verify configuration file existence
if (-not (Test-Path $ConfigFile)) {
    Write-Host "[-] Configuration file 'config.json' not found in $ScriptDir." -ForegroundColor Red
    Pause
    exit 1
}

try {
    $Config = Get-Content -Raw -Path $ConfigFile | ConvertFrom-Json
} catch {
    Write-Host "[-] Failed to parse 'config.json'. Verify JSON syntax." -ForegroundColor Red
    Pause
    exit 1
}

# Resolve Switch / Interface Alias
$SwitchAlias = if ([string]::IsNullOrWhiteSpace($Config.switch_name)) { "vEthernet (DuneAwakeningServerSwitch)" } else { $Config.switch_name }
$VmUser = if ([string]::IsNullOrWhiteSpace($Config.vm_user)) { "dune" } else { $Config.vm_user }

# Resolve PC IP (Host adapter bound to the virtual switch)
$PcIp = $Config.pc_ip
if ([string]::IsNullOrWhiteSpace($PcIp)) {
    Write-Host "[*] Resolving Host PC IP automatically from '$SwitchAlias'..." -ForegroundColor Cyan
    $PcIp = (Get-NetIPAddress -InterfaceAlias $SwitchAlias -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
}

if ([string]::IsNullOrWhiteSpace($PcIp)) {
    Write-Host "[-] Unable to resolve Host PC IP. Please configure 'pc_ip' in config.json." -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "[+] Host PC IP: $PcIp" -ForegroundColor Green

# Resolve VM IP via Hyper-V subsystem
$VmIp = $Config.vm_ip
if ([string]::IsNullOrWhiteSpace($VmIp)) {
    Write-Host "[*] Resolving VM IP automatically via Hyper-V subsystem..." -ForegroundColor Cyan
    try {
        $VmAdapters = Get-VMNetworkAdapter -All -ErrorAction SilentlyContinue | Where-Object { $_.SwitchName -like "*Dune*" -or $_.VMName -like "*Dune*" }
        $CandidateIps = $VmAdapters.IPAddresses | Where-Object { $_ -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$' -and $_ -ne $PcIp -and $_ -notlike "169.254.*" }
        $VmIp = $CandidateIps | Select-Object -First 1
    } catch {
        Write-Host "[-] Hyper-V query failed. Ensure Hyper-V PowerShell module is enabled." -ForegroundColor Yellow
    }
}

if ([string]::IsNullOrWhiteSpace($VmIp)) {
    Write-Host "[-] Unable to resolve VM IP. Please specify 'vm_ip' in config.json." -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "[+] Virtual Machine IP: $VmIp" -ForegroundColor Green

# Fetch external public IP address
Write-Host "[*] Fetching current public IP..." -ForegroundColor Cyan
try {
    $CurrentPublicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 5).Trim()
} catch {
    Write-Host "[-] Error fetching public IP. Please verify your internet connection." -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "[+] Current Public IP: $CurrentPublicIp" -ForegroundColor Green

# 1. Manage Windows Routing Table
Write-Host "[*] Cleaning up stale routes..." -ForegroundColor Cyan
$ExistingRoutes = Get-NetRoute -ErrorAction SilentlyContinue | Where-Object { 
    $_.NextHop -eq $VmIp -and $_.DestinationPrefix -notlike "192.168.*"
}

foreach ($Route in $ExistingRoutes) {
    Write-Host "[-] Removing outdated route: $($Route.DestinationPrefix)" -ForegroundColor Yellow
    Remove-NetRoute -DestinationPrefix $Route.DestinationPrefix -Confirm:$false | Out-Null
}

Write-Host "[+] Adding new route: $CurrentPublicIp/32 -> $VmIp" -ForegroundColor Cyan
New-NetRoute -DestinationPrefix "$CurrentPublicIp/32" -InterfaceAlias $SwitchAlias -NextHop $VmIp -PolicyStore ActiveStore -ErrorAction SilentlyContinue | Out-Null

# 2. Update targeted iptables on Linux VM (Port range 7777:7810 UDP for game instances, preserving RabbitMQ TCP 31982)
Write-Host "[*] Updating iptables on VM ($VmIp)..." -ForegroundColor Cyan

$CleanOldRules = "sudo iptables -t nat -S PREROUTING | grep 'DNAT.*$VmIp' | sed 's/^-A/sudo iptables -t nat -D/' | sh; sudo iptables -t nat -S POSTROUTING | grep 'MASQUERADE.*$PcIp' | sed 's/^-A/sudo iptables -t nat -D/' | sh"
$AddNewRules = "sudo iptables -t nat -I PREROUTING 1 -p udp -d $CurrentPublicIp --dport 7777:7810 -j DNAT --to-destination $VmIp && sudo iptables -t nat -A POSTROUTING -s $PcIp -j MASQUERADE"

$RemoteCommand = "$CleanOldRules; $AddNewRules"

try {
    $SshOutput = ssh -o StrictHostKeyChecking=no "$VmUser@$VmIp" $RemoteCommand 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[+] VM iptables rules successfully applied." -ForegroundColor Green
    } else {
        Write-Host "[-] Warning during iptables configuration: $SshOutput" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[-] Failed to connect via SSH to VM." -ForegroundColor Red
    Pause
    exit 1
}

Write-Host "`n[SUCCESS] Network routes synchronized successfully!" -ForegroundColor Green