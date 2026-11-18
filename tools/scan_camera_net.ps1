<#
相机网络扫描（结果写文件，跑完我直接读文件）
--------------------------------------------------
为什么需要：
  相机可能不在当前网段（比如它是 169.254.x 自动地址，或者 192.168.2.x/172.16.x），
  只有把网卡临时加到那些网段，才能用广播 ping + ARP 把它的 IP 找出来。

做三件事（都会写进 out\camera_scan.txt）：
  1) 给相机网卡临时加几个常见网段的 IP（保留已有的 192.168.0.100 / 10.0.0.100）
  2) 对每个网段广播 ping，再导 ARP 表 —— 有相机就会出现在 ARP 里
  3) 用 SDK 再枚举一次

用法：双击 `扫描相机网络.cmd`（会自动弹 UAC）
#>

[CmdletBinding()]
param([string]$Adapter = "")

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $PSCommandPath
$proj = Split-Path -Parent $here
$outDir = Join-Path $proj "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$log = Join-Path $outDir "camera_scan.txt"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    ) -Verb RunAs
    return
}

function W([string]$t = "") {
    Write-Host $t
    Add-Content -Path $log -Value $t -Encoding UTF8
}

Set-Content -Path $log -Value ("相机网络扫描 " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding UTF8

$nics = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -notmatch "Wi-Fi|Wireless|Bluetooth|WLAN" }
if ($Adapter) { $nics = $nics | Where-Object { $_.Name -eq $Adapter } }
$nic = ($nics | Where-Object { $_.InterfaceDescription -match "AX88|RTL81|Ethernet|以太网" } | Select-Object -First 1)
if (-not $nic) { $nic = $nics | Select-Object -First 1 }
if (-not $nic) { W "没有已连接的以太网卡"; exit 1 }
$name = $nic.Name
W ("网卡: {0}  {1}  {2}" -f $name, $nic.InterfaceDescription, $nic.LinkSpeed)

# 1) 临时加网段
$extra = @(
    @{ Address = "169.254.10.10"; Prefix = 16 },   # 相机若是"自动地址"模式就在这段
    @{ Address = "192.168.2.100"; Prefix = 24 },
    @{ Address = "172.16.0.100"; Prefix = 24 },
    @{ Address = "10.1.1.100"; Prefix = 24 }
)
W ""
W "== 加了这些 IP =="
foreach ($e in $extra) {
    $has = Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -eq $e.Address }
    if (-not $has) {
        try {
            New-NetIPAddress -InterfaceAlias $name -IPAddress $e.Address -PrefixLength $e.Prefix -ErrorAction Stop | Out-Null
            W ("  + {0}/{1}" -f $e.Address, $e.Prefix)
        } catch { W ("  x {0}: {1}" -f $e.Address, $_.Exception.Message) }
    } else { W ("  = {0}（已有）" -f $e.Address) }
}
Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength | ForEach-Object { W ("  当前: {0}/{1}" -f $_.IPAddress, $_.PrefixLength) }

# 2) 广播 ping + ARP
W ""
W "== 广播 ping =="
foreach ($b in @("169.254.255.255", "192.168.0.255", "192.168.2.255", "172.16.0.255", "10.0.0.255", "10.1.1.255")) {
    W ("  ping {0}" -f $b)
    ping -n 2 -w 300 $b | Out-Null
}
Start-Sleep -Milliseconds 600
$idx = (Get-NetIPInterface -InterfaceAlias $name -AddressFamily IPv4 | Select-Object -First 1).InterfaceIndex
W ""
W "== ARP 邻居（只看 IPv4 单播）=="
$found = $false
Get-NetNeighbor -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.LinkLayerAddress -and $_.LinkLayerAddress -notmatch "^00-00-00-00-00-00$|^FF-FF-FF-FF-FF-FF$|^01-00-5E" } |
    ForEach-Object {
        W ("  {0,-16} {1}  {2}" -f $_.IPAddress, $_.LinkLayerAddress, $_.State)
        if ($_.IPAddress -notmatch "^(169\.254\.|192\.168\.|10\.|172\.16\.)" -or $true) { $found = $true }
    }
if (-not $found) { W "  （没有非多播邻居 —— 说明相机可能根本没在发东西，或不在这些网段）" }

# 3) SDK 枚举
W ""
W "== SDK 枚举（32 位 Python）=="
$py = "C:\py38x86\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $proj
try {
    $res = & $py "tools\list_cameras.py" 2>&1 | Out-String
    W $res
} catch {
    W ("枚举异常: " + $_.Exception.Message)
} finally {
    Pop-Location
}

W ""
W "扫描完成。结果文件：$log"
Read-Host "按回车关闭"
