# Auto-elevate to Administrator privileges if not already elevated
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $ScriptDir "config.json"
$BattlegroupBat = Join-Path $ScriptDir "battlegroup.bat"

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  Dune: Awakening - Network Route Synchronizer     " -ForegroundColor Cyan
Write-Host "===================================================`n" -ForegroundColor Cyan

# 1. Parse configuration or prepare default values
$VmUser = "dune"
$VmIp = ""
$PcIp = ""
$SwitchAlias = "vEthernet (DuneAwakeningServerSwitch)"

if (Test-Path -Path $ConfigFile) {
    try {
        $ConfigRaw = (Get-Content -Raw -Path $ConfigFile).Trim()
        if (-not [string]::IsNullOrWhiteSpace($ConfigRaw)) {
            $Config = $ConfigRaw | ConvertFrom-Json
            if (-not [string]::IsNullOrWhiteSpace($Config.vm_user)) { $VmUser = $Config.vm_user }
            if (-not [string]::IsNullOrWhiteSpace($Config.vm_ip)) { $VmIp = $Config.vm_ip }
            if (-not [string]::IsNullOrWhiteSpace($Config.pc_ip)) { $PcIp = $Config.pc_ip }
            if (-not [string]::IsNullOrWhiteSpace($Config.switch_name)) { $SwitchAlias = $Config.switch_name }
        }
    } catch {
        Write-Host "[-] Warning: Failed to parse 'config.json'. Using runtime discovery." -ForegroundColor Yellow
    }
}

# 2. Check VM State via Hyper-V Subsystem
Write-Host "[*] Checking Hyper-V Virtual Machine status..." -ForegroundColor Cyan
$VmInstance = Get-VM | Where-Object { $_.Name -like "*Dune*" -or $_.NetworkAdapters.SwitchName -like "*Dune*" } | Select-Object -First 1

if ($null -ne $VmInstance -and $VmInstance.State -ne "Running") {
    Write-Host "`n[!] Virtual Machine '$($VmInstance.Name)' is NOT running (Current State: $($VmInstance.State))." -ForegroundColor Yellow
    Write-Host "`nRequired Actions in Battlegroup CLI:" -ForegroundColor Cyan
    Write-Host "  1. Start the VM using option: 'b. start-vm'" -ForegroundColor White
    Write-Host "  2. If your Public IP changed, update it using: '8. change-battlegroup-ip'" -ForegroundColor White
    Write-Host "  3. Start the game server using: '2. start'" -ForegroundColor White
    Write-Host "  * Note: If players cannot see the server, verify version updates using: '5. update'`n" -ForegroundColor DarkGray

    $LaunchNow = Read-Host "[?] Launch battlegroup.bat now to manage VM? (Y/N)"
    if ($LaunchNow -match '^[Yy]$') {
        if (Test-Path -Path $BattlegroupBat) {
            Start-Process -FilePath $BattlegroupBat
        } else {
            Write-Host "[-] 'battlegroup.bat' not found in root directory." -ForegroundColor Red
        }
    }
    exit 0
}

# 3. Resolve Host PC IP automatically
if ([string]::IsNullOrWhiteSpace($PcIp)) {
    Write-Host "[*] Resolving Host PC IP from '$SwitchAlias'..." -ForegroundColor Cyan
    $PcIp = (Get-NetIPAddress -InterfaceAlias $SwitchAlias -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress -First 1)
}

if ([string]::IsNullOrWhiteSpace($PcIp)) {
    Write-Host "[-] Failed to resolve Host PC IP on switch '$SwitchAlias'." -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "[+] Host PC IP: $PcIp" -ForegroundColor Green

# 4. Resolve VM IP via Hyper-V subsystem
if ([string]::IsNullOrWhiteSpace($VmIp)) {
    Write-Host "[*] Resolving VM IP automatically via Hyper-V subsystem..." -ForegroundColor Cyan
    try {
        $VmAdapters = Get-VMNetworkAdapter -All -ErrorAction SilentlyContinue | Where-Object { $_.SwitchName -like "*Dune*" -or $_.VMName -like "*Dune*" }
        $VmIp = $VmAdapters.IPAddresses | Where-Object {$_ -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$' -and$_ -notlike "169.254.*" } | Select-Object -First 1
    } catch {
        Write-Host "[-] Hyper-V subsystem lookup failed." -ForegroundColor Yellow
    }
}

if ([string]::IsNullOrWhiteSpace($VmIp)) {
    Write-Host "[-] Failed to resolve VM IP. Ensure VM is online and integration services are ready." -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "[+] Virtual Machine IP: $VmIp" -ForegroundColor Green

# 5. Fetch Public IP
Write-Host "[*] Fetching current public IP..." -ForegroundColor Cyan
try {
    $PublicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 5).Trim()
    Write-Host "[+] Current Public IP: $PublicIp" -ForegroundColor Green
} catch {
    Write-Host "[-] Failed to retrieve public IP address." -ForegroundColor Red
    Pause
    exit 1
}

# 6. Synchronize Windows Routing Table
Write-Host "[*] Cleaning up stale routes..." -ForegroundColor Cyan
$StaleRoutes = Get-NetRoute -DestinationPrefix "$PublicIp/32" -ErrorAction SilentlyContinue
$IpChanged =$false

if ($StaleRoutes) {
    foreach ($Route in $StaleRoutes) {
        if ($Route.NextHop -ne$VmIp) {
            $IpChanged =$true
            Write-Host "[-] Removing outdated route: $($Route.DestinationPrefix) via $($Route.NextHop)" -ForegroundColor Yellow
            Remove-NetRoute -DestinationPrefix $Route.DestinationPrefix -NextHop $Route.NextHop -Confirm:$false
        }
    }
} else {
    $IpChanged =$true
}

$ExistingRoute = Get-NetRoute -DestinationPrefix "$PublicIp/32" -NextHop $VmIp -ErrorAction SilentlyContinue
if (-not $ExistingRoute) {
    Write-Host "[+] Adding route: $PublicIp/32 ->$VmIp" -ForegroundColor Green
    New-NetRoute -DestinationPrefix "$PublicIp/32" -InterfaceAlias $SwitchAlias -NextHop $VmIp | Out-Null
} else {
    Write-Host "[+] Route already up to date: $PublicIp/32 ->$VmIp" -ForegroundColor Green
}

# 7. Synchronize VM iptables rules
Write-Host "[*] Updating iptables on VM ($VmIp)..." -ForegroundColor Cyan

$CmdPreroutingDel = 'sudo iptables -t nat -S PREROUTING | grep 7777:7810 | sed "s/^-A/sudo iptables -t nat -D/" | while read -r line; do eval "$line"; done'
$CmdPostroutingDel = 'sudo iptables -t nat -S POSTROUTING | grep 7777:7810 | sed "s/^-A/sudo iptables -t nat -D/" | while read -r line; do eval "$line"; done'
$CmdPreroutingAdd = "sudo iptables -t nat -A PREROUTING -d $($PublicIp.Trim()) -p udp --match multiport --dports 7777:7810 -j DNAT --to-destination $($PcIp.Trim())"
$CmdPostroutingAdd = "sudo iptables -t nat -A POSTROUTING -p udp -d $($PcIp.Trim()) --match multiport --dports 7777:7810 -j SNAT --to-source $($VmIp.Trim())"

$FullRemoteCommand = "$CmdPreroutingDel; $CmdPostroutingDel; $CmdPreroutingAdd; $CmdPostroutingAdd"

$IptablesResult = ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$VmUser@$($VmIp.Trim())" $FullRemoteCommand 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[+] VM iptables rules successfully applied." -ForegroundColor Green
} else {
    Write-Host "[-] Failed to apply VM iptables rules: $IptablesResult" -ForegroundColor Red
}

Write-Host "`n[SUCCESS] Network routes synchronized successfully!" -ForegroundColor Green

# 8. Check for Public IP changes and notify
if ($IpChanged) {
    Write-Host "`n[!] ATTENTION: Public IP change detected!" -ForegroundColor Yellow
    Write-Host "    Make sure to run '8. change-battlegroup-ip' in battlegroup menu to set: $PublicIp" -ForegroundColor White
}

Write-Host "`n===================================================" -ForegroundColor Cyan
Write-Host "  Synchronization Complete." -ForegroundColor Cyan
Write-Host "  Tip: If server is missing from server browser, check '5. update'." -ForegroundColor DarkGray
Write-Host "===================================================`n" -ForegroundColor Cyan

# 9. Prompt to Launch battlegroup.bat
$LaunchBg = Read-Host "Launch battlegroup.bat now? (Y/N)"
if ($LaunchBg -match '^[Yy]$') {
    if (Test-Path -Path $BattlegroupBat) {
        Start-Process -FilePath $BattlegroupBat
    } else {
        Write-Host "[-] 'battlegroup.bat' not found in current directory." -ForegroundColor Red
        Pause
    }
}