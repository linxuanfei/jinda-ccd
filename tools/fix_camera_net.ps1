<#
一键修相机网络（GigE 相机接不上时跑这个）
--------------------------------------------------
做的事（都会打印出来）：
  1) 把相机那块网卡（USB 转千兆，AX88179）的网络类型改 Private
     —— 默认是 Public，防火墙会把相机的发现包挡掉，这是"接上了但枚举不到"的最常见原因
  2) 加防火墙放行：来自 169.254.0.0/16、192.168.0.0/24、10.0.0.0/24 的入站包
  3) 静态 IP：192.168.0.100/24 和 10.0.0.100/24（保留 169.254 自动地址）
     —— 相机多半在这几个段里；都有也不冲突，Windows 会按路由选
  4) 找相机：对三段发广播 ping，再列 ARP，看看到底是哪个 IP
  5) 顺手调网卡：巨帧=4KB（这块 AX88179 最大只支持 4KB，不支持 9014）、关节能、关中断调节
  6) 最后自动跑一次枚举（32 位 Python），把结果打出来

用法：右键 → 使用 PowerShell 运行（会自动弹 UAC 要管理员权限）
或：  powershell -ExecutionPolicy Bypass -File fix_camera_net.ps1
#>

[CmdletBinding()]
param(
    [string]$Adapter = "",
    [switch]$NoIpChange
)

$ErrorActionPreference = "Continue"

# ---------- 自提权 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "需要管理员权限，正在申请提权……" -ForegroundColor Yellow
    $argsList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Adapter) { $argsList += @("-Adapter", "`"$Adapter`"") }
    if ($NoIpChange) { $argsList += "-NoIpChange" }
    Start-Process -FilePath "powershell.exe" -ArgumentList $argsList -Verb RunAs
    return
}

function Head([string]$t) {
    Write-Host ""
    Write-Host ("=" * 64) -ForegroundColor Cyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ("=" * 64) -ForegroundColor Cyan
}

Head "1) 找相机网卡"
$nics = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -notmatch "Wi-Fi|Wireless|Bluetooth|WLAN" }
if ($Adapter) { $nics = $nics | Where-Object { $_.Name -eq $Adapter } }
if (-not $nics) {
    Write-Host "没找到已连接的以太网卡。请确认 USB 网卡插好、网线接相机。" -ForegroundColor Red
    Read-Host "按回车退出"
    return
}
foreach ($n in $nics) {
    Write-Host ("  {0}  {1}  {2}" -f $n.Name, $n.InterfaceDescription, $n.LinkSpeed)
}
$nic = ($nics | Where-Object { $_.InterfaceDescription -match "AX88|RTL81|Ethernet|以太网" } | Select-Object -First 1)
if (-not $nic) { $nic = $nics[0] }
$name = $nic.Name
Write-Host ("  使用网卡: {0}（{1}）" -f $name, $nic.InterfaceDescription) -ForegroundColor Green

Head "2) 网络类型改 Private（放行相机发现包）"
try {
    Set-NetConnectionProfile -InterfaceAlias $name -NetworkCategory Private -ErrorAction Stop
    Write-Host "  已改为 Private" -ForegroundColor Green
} catch {
    Write-Host ("  改网络类型失败：{0}（继续，靠防火墙规则兜底）" -f $_.Exception.Message) -ForegroundColor Yellow
}

Head "3) 防火墙放行相机网段"
foreach ($cidr in @("169.254.0.0/16", "192.168.0.0/24", "10.0.0.0/24")) {
    $ruleName = "GigE Camera $cidr"
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -RemoteAddress $cidr -Profile Any -ErrorAction Stop | Out-Null
        Write-Host ("  放行 {0}" -f $cidr) -ForegroundColor Green
    } catch {
        Write-Host ("  放行 {0} 失败：{1}" -f $cidr, $_.Exception.Message) -ForegroundColor Yellow
    }
}

Head "4) 静态 IP（192.168.0.100 / 10.0.0.100）"
if (-not $NoIpChange) {
    $want = @(
        @{ Address = "192.168.0.100"; Prefix = 24 },
        @{ Address = "10.0.0.100"; Prefix = 24 }
    )
    foreach ($w in $want) {
        $exists = Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                  Where-Object { $_.IPAddress -eq $w.Address }
        if (-not $exists) {
            try {
                New-NetIPAddress -InterfaceAlias $name -IPAddress $w.Address -PrefixLength $w.Prefix -ErrorAction Stop | Out-Null
                Write-Host ("  已加 {0}/{1}" -f $w.Address, $w.Prefix) -ForegroundColor Green
            } catch {
                Write-Host ("  加 {0} 失败：{1}" -f $w.Address, $_.Exception.Message) -ForegroundColor Yellow
            }
        } else {
            Write-Host ("  已有 {0}" -f $w.Address)
        }
    }
    # DHCP 开着时手加的 IP 会被清掉，这里关掉 DHCP
    try { Set-NetIPInterface -InterfaceAlias $name -Dhcp Disabled -ErrorAction Stop; Write-Host "  已关闭 DHCP" -ForegroundColor Green }
    catch { Write-Host "  关闭 DHCP 失败（不影响本次）" -ForegroundColor Yellow }
}
Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength | Format-Table -AutoSize | Out-String | Write-Host

Head "5) 找相机 IP（广播 ping + ARP）"
foreach ($b in @("169.254.255.255", "192.168.0.255", "10.0.0.255")) {
    Write-Host ("  ping -n 1 {0}" -f $b)
    ping -n 1 -w 400 $b | Out-Null
}
Start-Sleep -Milliseconds 400
Write-Host "  ARP 表（该网卡）："
$idx = (Get-NetIPInterface -InterfaceAlias $name -AddressFamily IPv4 | Select-Object -First 1).InterfaceIndex
Get-NetNeighbor -InterfaceIndex $idx -ErrorAction SilentlyContinue |
    Where-Object { $_.State -ne "Unreachable" -and $_.LinkLayerAddress -ne "" -and $_.LinkLayerAddress -ne "00-00-00-00-00-00" } |
    Select-Object IPAddress, LinkLayerAddress, State | Format-Table -AutoSize | Out-String | Write-Host

Head "6) 网卡调优（巨帧 4KB / 关节能 / 关中断调节）"
$props = Get-NetAdapterAdvancedProperty -Name $name -ErrorAction SilentlyContinue
$jumbo = $props | Where-Object { $_.RegistryKeyword -eq "JumboPacket" }
if ($jumbo) {
    $val = ($jumbo.ValidDisplayValues | Where-Object { $_ -match "4KB|4088" } | Select-Object -First 1)
    if ($val) {
        Set-NetAdapterAdvancedProperty -Name $name -RegistryKeyword "JumboPacket" -RegistryValue $val -ErrorAction SilentlyContinue
        Write-Host ("  巨帧 → {0}" -f $val) -ForegroundColor Green
    } else {
        Write-Host ("  巨帧可选值：{0}（没有 9014，这块卡最大 4KB）" -f ($jumbo.ValidDisplayValues -join ", "))
    }
}
foreach ($kw in @("EnergyEfficientEthernet", "EEELinkAdvertisement", "InterruptModeration")) {
    $p = $props | Where-Object { $_.RegistryKeyword -eq $kw }
    if ($p) {
        $off = ($p.ValidDisplayValues | Where-Object { $_ -match "Disabled|关闭|Off|^0$" } | Select-Object -First 1)
        if ($off) {
            Set-NetAdapterAdvancedProperty -Name $name -RegistryKeyword $kw -RegistryValue $off -ErrorAction SilentlyContinue
            Write-Host ("  {0} → {1}" -f $kw, $off) -ForegroundColor Green
        }
    }
}

Head "7) 枚举相机（32 位 Python）"
$proj = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$py = "C:\py38x86\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $proj
try {
    & $py "tools\list_cameras.py"
} finally {
    Pop-Location
}

Write-Host ""
Read-Host "按回车退出"
