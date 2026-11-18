<#
老机器"一次拷全"脚本（只读源机，只往目标目录写）
----------------------------------------------------
在老机器上跑，把我们要的东西尽量自动找齐、复制到一个文件夹（U 盘/共享盘），
不用人在 C 盘里翻。

用法（老机器，普通用户即可）：
  powershell -ExecutionPolicy Bypass -File collect_old_machine.ps1
  powershell -ExecutionPolicy Bypass -File collect_old_machine.ps1 -Dest "E:\迁移包"

默认目标：本脚本同目录下的 _老机器文件\（改 U 盘路径更方便）
输出：复制完成后同目录生成 清单.txt，说明每项找到没找到。
#>
param(
    [string]$Dest = ""
)

$ErrorActionPreference = "SilentlyContinue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Dest) { $Dest = Join-Path $here "_老机器文件" }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$log = New-Object System.Collections.Generic.List[string]
function Note([string]$text) { $log.Add($text); Write-Host $text }

Note "迁移包目标: $Dest"
Note ("时间: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Note ""

# ---------------------------------------------------------------- 1. 相机 SDK
Note "== 1. 相机 SDK =="
$sdkTarget = Join-Path $Dest "sdk"
New-Item -ItemType Directory -Force -Path $sdkTarget | Out-Null
$dllNames = @("MVCAMSDK_X64.dll", "MVCAMSDK.dll", "mvsdk.py", "MvCameraControl.dll",
              "MvCameraControl_class.py", "CameraParams_header.py", "CameraParamsConst.py",
              "MvErrorDefine_const.py", "PixelType_header.py")
$foundDll = 0
$searchRoots = @("C:\Program Files", "C:\Program Files (x86)", "C:\Windows\System32", "D:\", "E:\")
foreach ($root in $searchRoots) {
    if (-not (Test-Path $root)) { continue }
    foreach ($name in $dllNames) {
        foreach ($hit in (Get-ChildItem -Path $root -Filter $name -Recurse -Depth 3 -File)) {
            $sub = Join-Path $sdkTarget $hit.Name
            if (-not (Test-Path $sub)) {
                Copy-Item -LiteralPath $hit.FullName -Destination $sub -Force
                Note ("  复制 " + $hit.FullName)
                $foundDll++
            }
        }
    }
}
if ($foundDll -eq 0) { Note "  [没找到] MVCAMSDK_X64.dll / mvsdk.py / MvCameraControl.dll —— 至少要有其中一个" }

# ---------------------------------------------------------------- 2. 原厂安装目录
Note ""
Note "== 2. 原厂软件安装目录（含 HunterxSys.ini / .HPro / 资料）=="
$keys = @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
          "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
          "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*")
$apps = Get-ItemProperty $keys | Where-Object {
    $_.DisplayName -and $_.DisplayName -match "In-?Vision|Hunter|视觉|CCD|Vision"
} | Select-Object DisplayName, InstallLocation, DisplayVersion -Unique
$installDirs = @()
if ($apps) {
    foreach ($a in $apps) {
        Note ("  已装: " + $a.DisplayName + " " + $a.DisplayVersion + "  " + $a.InstallLocation)
        if ($a.InstallLocation -and (Test-Path $a.InstallLocation)) { $installDirs += $a.InstallLocation }
    }
} else {
    Note "  [注册表没找到] 请手工确认原厂软件装在哪个盘（常见 D:\ 下）"
}
# 兜底：按 exe 名字找
foreach ($exe in @("HunterxSys.exe", "InVision.exe", "Vision.exe", "CCD.exe")) {
    foreach ($root in @("D:\")) {
        if (-not (Test-Path $root)) { continue }
        foreach ($hit in (Get-ChildItem -Path $root -Filter $exe -Recurse -Depth 2 -File)) {
            $installDirs += $hit.DirectoryName
        }
    }
}
$installDirs = $installDirs | Select-Object -Unique
foreach ($dir in $installDirs) {
    if (-not (Test-Path $dir)) { continue }
    $leaf = Split-Path $dir -Leaf
    $to = Join-Path $Dest ("原厂安装_" + $leaf)
    New-Item -ItemType Directory -Force -Path $to | Out-Null
    Note ("  复制目录 " + $dir)
    # 只拷配置/配方/资料，不拷大二进制
    foreach ($pat in @("*.ini", "*.HPro", "*.hpro", "*.json", "*.xml", "*.cfg", "*.txt", "*.csv", "*.log")) {
        foreach ($f in (Get-ChildItem -Path $dir -Filter $pat -Recurse -Depth 3 -File)) {
            $rel = $f.FullName.Substring($dir.Length).TrimStart('\')
            $dstFile = Join-Path $to $rel
            New-Item -ItemType Directory -Force -Path (Split-Path $dstFile -Parent) | Out-Null
            Copy-Item -LiteralPath $f.FullName -Destination $dstFile -Force
        }
    }
}

# ---------------------------------------------------------------- 3. 配方 / 资料
Note ""
Note "== 3. 配方(.HPro) 与 资料 目录 =="
$hproTarget = Join-Path $Dest "HPro"
New-Item -ItemType Directory -Force -Path $hproTarget | Out-Null
$hproCount = 0
foreach ($root in @("C:\", "D:\", "E:\")) {
    if (-not (Test-Path $root)) { continue }
    foreach ($f in (Get-ChildItem -Path $root -Include *.HPro, *.hpro -Recurse -Depth 4 -File)) {
        $dstFile = Join-Path $hproTarget ($f.Name)
        if (-not (Test-Path $dstFile)) {
            Copy-Item -LiteralPath $f.FullName -Destination $dstFile -Force
            Note ("  复制 " + $f.FullName + "  (" + [math]::Round($f.Length/1KB) + " KB)")
            $hproCount++
        }
    }
}
if ($hproCount -eq 0) { Note "  [没找到] .HPro（可能在原厂软件的 配方/Product 目录里，见第 2 项）" }

$ziLiao = Join-Path $Dest "资料"
New-Item -ItemType Directory -Force -Path $ziLiao | Out-Null
foreach ($root in (@("D:\", "C:\Users\Public") + $installDirs | Select-Object -Unique)) {
    if (-not $root -or -not (Test-Path $root)) { continue }
    foreach ($d in (Get-ChildItem -Path $root -Directory -Recurse -Depth 3 | Where-Object { $_.Name -eq "资料" })) {
        Note ("  复制 资料 目录 " + $d.FullName)
        Copy-Item -LiteralPath $d.FullName -Destination (Join-Path $ziLiao $d.Name) -Recurse -Force
    }
}

# ---------------------------------------------------------------- 4. 串口/网口线索
Note ""
Note "== 4. 串口与协议线索（登记用，不复制设备）=="
foreach ($p in (Get-CimInstance Win32_SerialPort)) {
    Note ("  串口 " + $p.DeviceID + "  " + $p.Description + "  PNP=" + $p.PNPDeviceID)
}
foreach ($n in (Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True")) {
    Note ("  网卡 IP=" + ($n.IPAddress -join ",") + "  " + $n.Description)
}

# ---------------------------------------------------------------- 5. 环境报告
Note ""
Note "== 5. 环境报告 =="
$envScript = Join-Path $here "check_env.ps1"
if (Test-Path $envScript) {
    try {
        powershell -ExecutionPolicy Bypass -File $envScript -Quiet | Out-Null
        $report = Join-Path $here "env_report.txt"
        if (Test-Path $report) {
            Copy-Item -LiteralPath $report -Destination (Join-Path $Dest "env_report.txt") -Force
            Note "  env_report.txt 已一并复制"
        }
    } catch { Note "  check_env.ps1 执行失败，忽略" }
} else {
    Note "  （同目录没有 check_env.ps1，跳过；不影响）"
}

# ---------------------------------------------------------------- 清单
$logPath = Join-Path $Dest "清单.txt"
$log | Set-Content -Path $logPath -Encoding UTF8
Write-Host ""
Write-Host "完成。把整个文件夹拷回来即可：" -ForegroundColor Green
Write-Host "  $Dest" -ForegroundColor Green
Write-Host "其中 清单.txt 写了每项找到没找到。" -ForegroundColor Green
