<#
找到相机了：它在我们没试过的网段 192.168.3.x
------------------------------------------------
抓包证据（pktmon，8 秒）：
    DC-A5-01-E3-DF-91 > FF-FF-FF-FF-FF-FF
    192.168.3.150:51622 > 255.255.255.255:51622  UDP，每秒 1 个，256 字节

→ 相机 IP = 192.168.3.150，MAC DC-A5-01-E3-DF-91，正在广播（说明它活着、通网）

这个脚本只做三件事：
  1) 给相机网卡加 192.168.3.100/24
  2) 防火墙放行 192.168.3.0/24
  3) ping 一下相机并导 ARP，确认通了

用法：双击 `加相机网段.cmd`（弹 UAC 点是）
#>

[CmdletBinding()]
param([string]$CameraIp = "192.168.3.150", [string]$HostIp = "192.168.3.100")

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $PSCommandPath
$proj = Split-Path -Parent $here
$outDir = Join-Path $proj "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$log = Join-Path $outDir "camera_subnet.txt"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    ) -Verb RunAs
    return
}

function W([string]$t = "") { Write-Host $t; Add-Content -Path $log -Value $t -Encoding UTF8 }
Set-Content -Path $log -Value ("加相机网段 " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding UTF8

$nic = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -match "AX88|RTL81|Ethernet|以太网" } | Select-Object -First 1
if (-not $nic) { W "没有已连接的以太网卡"; exit 1 }
$name = $nic.Name
W ("网卡: {0}  {1}" -f $name, $nic.InterfaceDescription)

W ""
W "== 1) 加 $HostIp/24 =="
$has = Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -eq $HostIp }
if (-not $has) {
    New-NetIPAddress -InterfaceAlias $name -IPAddress $HostIp -PrefixLength 24 -ErrorAction SilentlyContinue | Out-Null
    W "  已加"
} else { W "  已有" }

W ""
W "== 2) 防火墙放行 192.168.3.0/24 =="
$ruleName = "GigE Camera 192.168.3.0/24"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -RemoteAddress "192.168.3.0/24" -Profile Any -ErrorAction SilentlyContinue | Out-Null
W ("  规则: " + $ruleName + " 存在=" + [bool](Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue))

W ""
W "== 3) ping 相机 =="
$ping = ping -n 3 -w 800 $CameraIp
$ping | ForEach-Object { W ("  " + $_) }

W ""
W "== 4) ARP =="
$idx = (Get-NetIPInterface -InterfaceAlias $name -AddressFamily IPv4 | Select-Object -First 1).InterfaceIndex
Get-NetNeighbor -InterfaceIndex $idx -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -eq $CameraIp } |
    ForEach-Object { W ("  {0} -> {1} ({2})" -f $_.IPAddress, $_.LinkLayerAddress, $_.State) }

W ""
W "== 5) 当前该网卡 IP =="
Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 | ForEach-Object { W ("  {0}/{1}" -f $_.IPAddress, $_.PrefixLength) }

W ""
W ("完成：$log")
Read-Host "按回车关闭"
