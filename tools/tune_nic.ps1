<#
GigE 相机网卡调优（迈德威视 MV-GE500M-TPO 用）
--------------------------------------------------
工业千兆网相机对网卡有几项硬要求，不设会"能连但丢帧"：
  1. 巨帧 9014（Jumbo Frame）
  2. 关闭 节能以太网 / 绿色以太网（EEE）
  3. 关闭 中断调节（Interrupt Moderation）—— 降低单帧延迟
  4. 静态 IP，与相机同网段（相机默认多为 192.168.0.x / 169.254.x）
  5. 关闭该网卡的防火墙（或放行相机网段）

用法（以管理员身份运行 PowerShell）：
  # 只查看当前设置
  powershell -ExecutionPolicy Bypass -File tune_nic.ps1

  # 按网卡名调优（名字用"网络连接"里看到的，如 "以太网 2"）
  powershell -ExecutionPolicy Bypass -File tune_nic.ps1 -Adapter "以太网 2" -Apply

  # 顺便把 IP 设成 192.168.0.100（相机同网段）
  powershell -ExecutionPolicy Bypass -File tune_nic.ps1 -Adapter "以太网 2" -Apply -Ip 192.168.0.100

Win7 说明：Win7 没有 Get-NetAdapter/Set-NetAdapterAdvancedProperty，
本脚本会自动退回 netsh + 注册表提示，并按"需要手工点的地方"列出来。
#>
param(
    [string]$Adapter = "",
    [switch]$Apply,
    [string]$Ip = "",
    [string]$Mask = "255.255.255.0"
)

$ErrorActionPreference = "SilentlyContinue"
$hasNetAdapter = $null -ne (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue)

function Line([string]$t = "") { Write-Host $t }
function Head([string]$t) { Line ""; Line ("=" * 62); Line "  $t"; Line ("=" * 62) }

Head "GigE 相机网卡检查"

if (-not $hasNetAdapter) {
    Line "本机是 Windows 7（没有 Get-NetAdapter）。请手工设置："
    Line "  1) 控制面板 → 网络连接 → 右键相机网卡 → 属性 → 配置 → 高级："
    Line "     - Jumbo Frame / 巨帧 = 9014（或 9KB）"
    Line "     - Interrupt Moderation = Disabled"
    Line "     - Energy Efficient Ethernet / 绿色以太网 = Disabled"
    Line "  2) 该网卡 IPv4 设静态：192.168.0.100 / 255.255.255.0，网关留空"
    Line "  3) 防火墙：关闭该网卡上的 Windows 防火墙"
    exit 0
}

$nics = Get-NetAdapter -Physical | Where-Object { $_.Status -ne "Disabled" }
$candidates = $nics | Where-Object { $_.InterfaceDescription -match "Ethernet|以太网|I21|I22|I350|RTL81|AX88|Gigabit|千兆" }

Line "物理网卡："
foreach ($n in $candidates) {
    Line ("  {0,-16} {1,-38} {2,-10} {3}" -f $n.Name, $n.InterfaceDescription, $n.LinkSpeed, $n.Status)
}
if (-not $Adapter) {
    Line ""
    Line "用 -Adapter ""网卡名"" -Apply 来调优；下面是每块网卡的巨帧/节能/中断调节现状："
    foreach ($n in $candidates) {
        $jumbo = Get-NetAdapterAdvancedProperty -Name $n.Name | Where-Object { $_.DisplayName -match "Jumbo|巨帧" }
        $eee = Get-NetAdapterAdvancedProperty -Name $n.Name | Where-Object { $_.DisplayName -match "Energy Efficient|绿色|EEE" }
        $im = Get-NetAdapterAdvancedProperty -Name $n.Name | Where-Object { $_.DisplayName -match "Interrupt Moderation|中断调节" }
        Line ("  [{0}] 巨帧={1}  节能={2}  中断调节={3}" -f $n.Name,
            ($(if ($jumbo) { $jumbo.DisplayValue } else { "无此项" })),
            ($(if ($eee) { $eee.DisplayValue } else { "无此项" })),
            ($(if ($im) { $im.DisplayValue } else { "无此项" })))
    }
    exit 0
}

$nic = Get-NetAdapter -Name $Adapter
if (-not $nic) { Line "找不到网卡：$Adapter"; exit 1 }

if ($Apply) {
    Head "开始调优：$Adapter"
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Line "需要管理员权限才能改网卡设置，请以管理员身份重开 PowerShell。"
        exit 1
    }
    $props = Get-NetAdapterAdvancedProperty -Name $Adapter
    $jumbo = $props | Where-Object { $_.DisplayName -match "Jumbo|巨帧" }
    if ($jumbo) {
        $val = ($jumbo.ValidDisplayValues | Where-Object { $_ -match "9014|9K|9 KB" } | Select-Object -First 1)
        if ($val) {
            Set-NetAdapterAdvancedProperty -Name $Adapter -RegistryKeyword $jumbo.RegistryKeyword -RegistryValue $val
            Line "巨帧 → $val"
        } else { Line ("巨帧可选值：" + ($jumbo.ValidDisplayValues -join ", ")) }
    } else { Line "该网卡没有巨帧选项（USB 网卡常见），建议换 Intel PCIe 网卡" }

    foreach ($kw in @("InterruptModeration", "EEE", "EnergyEfficientEthernet", "GreenEthernet")) {
        $p = $props | Where-Object { $_.RegistryKeyword -eq $kw }
        if ($p) {
            $off = ($p.ValidDisplayValues | Where-Object { $_ -match "Disabled|关闭|Off" } | Select-Object -First 1)
            if ($off) { Set-NetAdapterAdvancedProperty -Name $Adapter -RegistryKeyword $kw -RegistryValue $off; Line "$kw → $off" }
        }
    }

    if ($Ip) {
        $existing = Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue
        if ($existing) {
            foreach ($e in $existing) { Remove-NetIPAddress -InterfaceAlias $Adapter -IPAddress $e.IPAddress -Confirm:$false -ErrorAction SilentlyContinue }
        }
        New-NetIPAddress -InterfaceAlias $Adapter -IPAddress $Ip -PrefixLength 24 -ErrorAction SilentlyContinue | Out-Null
        Line "IP → $Ip/$Mask"
    }

    # 相机网段的防火墙放行
    try {
        New-NetFirewallRule -DisplayName "GigE Camera 192.168.0.0/24" -Direction Inbound -Action Allow `
            -RemoteAddress 192.168.0.0/24 -ErrorAction Stop | Out-Null
        Line "防火墙：已放行 192.168.0.0/24"
    } catch { Line "防火墙规则已存在或无需添加" }

    Line ""
    Line "完成后请：拔插网线，确认 LinkSpeed = 1 Gbps，再用 tools\list_cameras.py 枚举相机。"
} else {
    Line ""
    Line "只检查不改。加 -Apply 才会调优（需要管理员）。"
}
