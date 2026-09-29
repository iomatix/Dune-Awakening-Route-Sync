# Auto-elevate to Administrator privileges if not already elevated
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

# Network Configuration
$VmIp = "192.168.1.57"
$PcIp = "192.168.1.33"
$VmUser = "dune"

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
New-NetRoute -DestinationPrefix "$CurrentPublicIp/32" -InterfaceAlias "vEthernet (DuneAwakeningServerSwitch)" -NextHop $VmIp -PolicyStore ActiveStore -ErrorAction SilentlyContinue | Out-Null

# 2. Update iptables on Linux VM
Write-Host "[*] Updating iptables on VM ($VmIp)..." -ForegroundColor Cyan

# Remove old custom rules safely
$CleanOldRules = "sudo iptables -t nat -S PREROUTING | grep 'DNAT.*$VmIp' | sed 's/^-A/sudo iptables -t nat -D/' | sh; sudo iptables -t nat -S POSTROUTING | grep 'MASQUERADE.*$PcIp' | sed 's/^-A/sudo iptables -t nat -D/' | sh"

# Insert targeted UDP DNAT at position 1 (preserving RabbitMQ TCP on 31982) and append POSTROUTING MASQUERADE
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