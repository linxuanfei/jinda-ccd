<#
相机发现排查（临时放开防火墙 + 全网段广播）—— 结果写 out\camera_diag.txt
------------------------------------------------------------------
上一轮扫描：6 个网段都没有 ARP 邻居，SDK 也枚举不到。
但 GigE Vision 的发现机制是「广播发现、相机用自己的 IP 回包」，
如果相机在别的网段（比如 192.168.5.x），它的回包会被我们的防火墙规则挡掉。

这一轮做：
  1) 临时关闭「专用网络」防火墙（只在这台机上，事后可开回来）
  2) 发 255.255.255.255 有限广播 ping + 列 ARP
  3) 再用 SDK 枚举（这次相机的回包不会被打断）
  4) 把结果全写文件

用法：双击 `排查相机发现.cmd`（弹 UAC 点是）
#>

[CmdletBinding()]
param([switch]$KeepFirewallOff)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $PSCommandPath
$proj = Split-Path -Parent $here
$outDir = Join-Path $proj "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$log = Join-Path $outDir "camera_diag.txt"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    ) -Verb RunAs
    return
}

function W([string]$t = "") { Write-Host $t; Add-Content -Path $log -Value $t -Encoding UTF8 }
Set-Content -Path $log -Value ("相机发现排查 " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding UTF8

$nic = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -match "AX88|RTL81|Ethernet|以太网" } | Select-Object -First 1
if (-not $nic) { W "没有已连接的以太网卡"; exit 1 }
$name = $nic.Name
W ("网卡: {0}  {1}  {2}" -f $name, $nic.InterfaceDescription, $nic.LinkSpeed)

$before = Get-NetAdapterStatistics -Name $name
W ("收包(前): 单播={0} 广播={1} 字节={2}" -f $before.ReceivedUnicastPackets, $before.ReceivedBroadcastPackets, $before.ReceivedBytes)

W ""
W "== 1) 临时关闭专用网络防火墙 =="
Set-NetFirewallProfile -Profile Private -Enabled False
W ("  专用网络防火墙: " + ((Get-NetFirewallProfile -Profile Private).Enabled))

W ""
W "== 2) 有限广播 ping + ARP =="
ping -n 3 -w 400 255.255.255.255 | Out-Null
Start-Sleep -Milliseconds 500
$idx = (Get-NetIPInterface -InterfaceAlias $name -AddressFamily IPv4 | Select-Object -First 1).InterfaceIndex
$neigh = Get-NetNeighbor -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.LinkLayerAddress -and $_.LinkLayerAddress -notmatch "^00-00-00-00-00-00$|^FF-FF-FF-FF-FF-FF$|^01-00-5E" }
if ($neigh) {
    $neigh | ForEach-Object { W ("  {0,-16} {1}  {2}" -f $_.IPAddress, $_.LinkLayerAddress, $_.State) }
} else {
    W "  （仍然没有邻居）"
}

# 收发增量：相机若在发心跳/广播，这里会涨
Start-Sleep -Seconds 3
$after = Get-NetAdapterStatistics -Name $name
W ("收包(后): 单播={0} 广播={1} 字节={2}" -f $after.ReceivedUnicastPackets, $after.ReceivedBroadcastPackets, $after.ReceivedBytes)
W ("3 秒增量: 单播={0} 广播={1} 字节={2}" -f `
    ($after.ReceivedUnicastPackets - $before.ReceivedUnicastPackets),
    ($after.ReceivedBroadcastPackets - $before.ReceivedBroadcastPackets),
    ($after.ReceivedBytes - $before.ReceivedBytes))

W ""
W "== 3) SDK 枚举 =="
$py = "C:\py38x86\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $proj
try {
    W (& $py "tools\list_cameras.py" 2>&1 | Out-String)
} finally { Pop-Location }

if (-not $KeepFirewallOff) {
    W ""
    W "== 4) 恢复防火墙 =="
    Set-NetFirewallProfile -Profile Private -Enabled True
    W ("  专用网络防火墙: " + ((Get-NetFirewallProfile -Profile Private).Enabled))
}
W ""
W "完成：$log"
Read-Host "按回车关闭"
