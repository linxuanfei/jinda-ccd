<#
抓包看相机到底在说什么（pktmon，抓 8 秒写成文本）
--------------------------------------------------
用途：现在相机电源灯亮、网口灯闪，但我们收到的单播包是 0。
要确认三件事：
  1) 我们发出去的 GigE 发现包（UDP 3956）有没有落到相机那块网卡上
  2) 相机到底有没有回包（回包源 MAC 是不是相机的）
  3) 空闲时每秒一个的广播包（~298 字节）是谁发的

做法：
  pktmon 抓 8 秒（含一次 SDK 枚举），转成文本，结果是：
    out\pktmon_cam.txt   （明细，可能很大）
    out\pktmon_cam_stats.txt（按会话统计的摘要）

用法：双击 `抓包看相机.cmd`（弹 UAC 点是）
#>

[CmdletBinding()]
param([int]$Seconds = 8)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $PSCommandPath
$proj = Split-Path -Parent $here
$outDir = Join-Path $proj "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$log = Join-Path $outDir "pktmon_run.txt"
$etl = Join-Path $env:TEMP "camera.pktmon.etl"
$txt = Join-Path $outDir "pktmon_cam.txt"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    ) -Verb RunAs
    return
}

function W([string]$t = "") { Write-Host $t; Add-Content -Path $log -Value $t -Encoding UTF8 }
Set-Content -Path $log -Value ("pktmon 抓包 " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding UTF8

$nic = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -match "AX88|RTL81|Ethernet|以太网" } | Select-Object -First 1
if (-not $nic) { W "没有已连接的以太网卡"; exit 1 }
$ifIndex = $nic.ifIndex
W ("网卡: {0}  ifIndex={1}  {2}" -f $nic.Name, $ifIndex, $nic.InterfaceDescription)
W ("网卡 MAC: " + $nic.MacAddress)

pktmon stop | Out-Null
pktmon filter remove | Out-Null
Remove-Item -LiteralPath $etl -Force -ErrorAction SilentlyContinue

W ""
W "== 开始抓包（$Seconds 秒），期间会跑一次 SDK 枚举 =="
pktmon start --capture --comp nics --pkt-size 256 --file-name $etl | Out-Null

Start-Sleep -Seconds 1
$py = "C:\py38x86\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $proj
try {
    $enumOut = & $py "tools\list_cameras.py" 2>&1 | Out-String
    W $enumOut
} finally { Pop-Location }
Start-Sleep -Seconds ($Seconds - 2)
pktmon stop | Out-Null
W "抓包结束"

if (-not (Test-Path $etl)) { W "没有生成 ETL，pktmon 可能没启动成功"; Read-Host "按回车关闭"; exit 1 }

W ""
W "== 转文本 =="
pktmon etl2txt $etl --out $txt --brief | Out-Null
if (Test-Path $txt) {
    $sizeMB = [math]::Round((Get-Item $txt).Length / 1MB, 2)
    W ("  明细: {0}（{1} MB）" -f $txt, $sizeMB)
} else {
    W "  转换失败"
}

# 摘要：源 MAC 统计 + 端口统计
if (Test-Path $txt) {
    $lines = Get-Content -LiteralPath $txt -ErrorAction SilentlyContinue
    W ""
    W ("== 抓到的行数: {0} ==" -f $lines.Count)

    W ""
    W "== 出现过的 UDP 端口（看有没有 3956 = GigE 发现）=="
    $ports = @{}
    foreach ($ln in $lines) {
        foreach ($m in [regex]::Matches($ln, "UDP:(\d+) -> (\d+)")) {
            $k = "UDP " + $m.Groups[1].Value + " -> " + $m.Groups[2].Value
            if ($ports.ContainsKey($k)) { $ports[$k]++ } else { $ports[$k] = 1 }
        }
    }
    if ($ports.Count -eq 0) { W "  （没解析到 UDP 端口，明细请直接看文件）" }
    else { $ports.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 15 | ForEach-Object { W ("  {0}  x{1}" -f $_.Key, $_.Value) } }

    W ""
    W "== 源 MAC 统计（找非本机、非网关的 MAC）=="
    $macs = @{}
    foreach ($ln in $lines) {
        foreach ($m in [regex]::Matches($ln, "([0-9A-F]{2}(?:[-:][0-9A-F]{2}){5})")) {
            $k = $m.Groups[1].Value
            if ($macs.ContainsKey($k)) { $macs[$k]++ } else { $macs[$k] = 1 }
        }
    }
    $macs.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 12 | ForEach-Object { W ("  {0}  x{1}" -f $_.Key, $_.Value) }

    W ""
    W "== 含 169.254 / 192.168 / 10. 的 IP 对（前 20 条）=="
    $iplines = $lines | Where-Object { $_ -match "\d+\.\d+\.\d+\.\d+" } | Select-Object -First 20
    foreach ($ln in $iplines) { W ("  " + $ln.Trim()) }
}

W ""
W ("完成。明细: $txt")
Read-Host "按回车关闭"
