<#
老检测机环境体检脚本（只读，不改系统）
--------------------------------------------------
用法（老机器上，普通用户即可）：
  1) 把本文件拷到老机器任意目录
  2) 右键 → “使用 PowerShell 运行”
     或命令行： powershell -ExecutionPolicy Bypass -File check_env.ps1
  3) 把生成的 env_report.txt 发回

它会报告：系统版本/位数、CPU/内存/磁盘、VC++ 运行库、USB3 控制器、
已装相机软件与驱动、MvCameraControl.dll 位置、网卡/巨帧设置。
#>

$ErrorActionPreference = "SilentlyContinue"

# --- 兼容老系统（Win7 + PowerShell 2.0）：CIM 没有就退回 WMI ---
$HasCim = $null -ne (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)
function Get-CimOrWmi([string]$class, $filter) {
  if ($HasCim) {
    if ($filter) { return Get-CimInstance $class -Filter $filter }
    return Get-CimInstance $class
  }
  if ($filter) { return Get-WmiObject $class -Filter $filter }
  return Get-WmiObject $class
}
$out = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "env_report.txt"
$lines = New-Object System.Collections.Generic.List[string]

function Add-Line([string]$text = "") { $lines.Add($text) }
function Add-Section([string]$title) { Add-Line ""; Add-Line ("=" * 60); Add-Line "  $title"; Add-Line ("=" * 60) }

Add-Line "VisionApp 老机器环境报告"
Add-Line ("生成时间: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))

# ------------------------------------------------------------------ 系统
Add-Section "1. 操作系统"
$os = Get-CimOrWmi "Win32_OperatingSystem" $null
Add-Line ("系统:     " + $os.Caption)
Add-Line ("版本:     " + $os.Version + "  (Build " + $os.BuildNumber + ")")
Add-Line ("位数:     " + $os.OSArchitecture)
Add-Line ("安装时间: " + $os.InstallDate)
Add-Line ("已运行:   " + $os.LastBootUpTime)

# 兼容性判定
$build = [int]$os.BuildNumber
Add-Line ""
if ($build -ge 22000) { Add-Line ">>> 结论: Windows 11 —— 我们现有 exe 可用（需 64 位）" }
elseif ($build -ge 10240) { Add-Line ">>> 结论: Windows 10 —— 我们现有 exe 可用（需 64 位）" }
elseif ($build -ge 9600) { Add-Line ">>> 结论: Windows 8.1 —— Qt6 官方不支持，需先试跑，可能要做 Qt5 兼容版" }
elseif ($build -ge 7600) { Add-Line ">>> 结论: Windows 7 —— 现有 exe【不能运行】，需升级系统或做 Qt5 兼容版" }
else { Add-Line ">>> 结论: 更老的系统 —— 现有 exe 不能运行，建议升级系统" }
if ($os.OSArchitecture -match "32") { Add-Line ">>> 注意: 32 位系统！我们的是 x64 程序，跑不了，需换 64 位系统" }

# ------------------------------------------------------------------ 硬件
Add-Section "2. 硬件"
foreach ($cpu in (Get-CimOrWmi "Win32_Processor" $null)) {
  Add-Line ("CPU:  " + $cpu.Name + "  核数=" + $cpu.NumberOfCores + "  线程=" + $cpu.NumberOfLogicalProcessors)
}
$mem = (Get-CimOrWmi "Win32_ComputerSystem" $null).TotalPhysicalMemory / 1GB
Add-Line ("内存: {0:N1} GB" -f $mem)
foreach ($d in (Get-CimOrWmi "Win32_LogicalDisk" "DriveType=3")) {
  Add-Line ("磁盘 {0}  可用 {1:N1} GB / 共 {2:N1} GB" -f $d.DeviceID, ($d.FreeSpace/1GB), ($d.Size/1GB))
}

# ------------------------------------------------------------------ 运行库
Add-Section "3. Visual C++ 运行库（相机驱动/SDK 安装器需要）"
$keys = @(
  "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
  "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$vc = Get-ItemProperty $keys | Where-Object { $_.DisplayName -match "Visual C\+\+" } |
      Select-Object -ExpandProperty DisplayName -Unique | Sort-Object
if ($vc) { $vc | ForEach-Object { Add-Line ("  " + $_) } } else { Add-Line "  (未检测到 VC++ 运行库)" }
if ($build -lt 10240) {
  $ucrt = Test-Path "C:\Windows\System32\ucrtbase.dll"
  Add-Line ("  ucrtbase.dll 存在: " + $ucrt + "   （Win7 需要 KB2999226 补丁）")
}

# ------------------------------------------------------------------ USB
Add-Section "4. USB 控制器（USB3 相机必需）"
$usb = Get-CimOrWmi "Win32_USBController" $null | Select-Object -ExpandProperty Name
$usb | ForEach-Object { Add-Line ("  " + $_) }
$has3 = ($usb | Where-Object { $_ -match "3\.0|xHCI|USB 3" }) -ne $null
Add-Line ("  含 USB3 控制器: " + $has3)
if (-not $has3) { Add-Line "  >>> 注意: 没检测到 USB3，USB3 工业相机插 USB2 会掉线/降速" }

# ------------------------------------------------------------------ 相机软件/驱动
Add-Section "5. 已安装的相机软件 / 驱动"
$cams = Get-ItemProperty $keys |
        Where-Object { $_.DisplayName -match "MVS|MindVision|迈德威视|Basler|pylon|Daheng|Galaxy|Hikrobot|海康|大恒|In-Vision|视觉|CCD" } |
        Select-Object DisplayName, DisplayVersion -Unique
if ($cams) { $cams | ForEach-Object { Add-Line ("  " + $_.DisplayName + "  " + $_.DisplayVersion) } }
else { Add-Line "  (未检测到相机相关软件)" }

Add-Line ""
Add-Line "  设备管理器里的图像设备:"
$img = Get-CimOrWmi "Win32_PnPEntity" $null | Where-Object { $_.Name -match "Cam|相机|USB3|MV-|Basler|Hik|Daheng" }
if ($img) { $img | ForEach-Object { Add-Line ("    " + $_.Name + "   [" + $_.Status + "]") } }
else { Add-Line "    (未发现相机设备；老机器上可能是原厂专有驱动，见下条)" }

# ------------------------------------------------------------------ SDK 文件
Add-Section "6. MvCameraControl.dll / SDK 位置"
$found = @()
foreach ($root in @("C:\", "D:\", "C:\Program Files", "C:\Program Files (x86)")) {
  if (Test-Path $root) {
    $found += Get-ChildItem -Path $root -Filter "MvCameraControl.dll" -Recurse -Depth 4 -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty FullName
  }
}
$found = $found | Select-Object -Unique
if ($found) { $found | ForEach-Object { Add-Line ("  " + $_) } } else { Add-Line "  (没找到 MvCameraControl.dll，需从原厂安装目录确认)" }

# ------------------------------------------------------------------ 网络（GigE）
Add-Section "7. 网卡与巨帧（千兆网相机用）"
if (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue) {
  Get-NetAdapter | ForEach-Object {
    Add-Line ("  " + $_.Name + "  " + $_.InterfaceDescription + "  速度=" + $_.LinkSpeed + "  状态=" + $_.Status)
  }
  $jumbo = Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match "Jumbo|巨帧" }
  if ($jumbo) { $jumbo | ForEach-Object { Add-Line ("    巨帧: " + $_.Name + " = " + $_.DisplayValue) } }
} else {
  # Win7 等老系统
  Get-CimOrWmi "Win32_NetworkAdapter" "NetEnabled=True" | ForEach-Object {
    Add-Line ("  " + $_.Name + "  " + $_.AdapterType + "  速度=" + $_.Speed)
  }
}

# ------------------------------------------------------------------ 输出
Add-Section "完"
Add-Line "把本文件（env_report.txt）发回即可。"
$lines | Set-Content -Path $out -Encoding UTF8
Write-Host ""
Write-Host "报告已生成: $out" -ForegroundColor Green
Write-Host "请把这个文件发回。" -ForegroundColor Green
Write-Host ""
$lines | ForEach-Object { Write-Host $_ }
