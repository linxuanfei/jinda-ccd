# 相机 SDK 放这里（迈德威视 MindVision）

现场相机：**MV-GE500M-TPO**（GigE 网口，2592×1944 = 500 万，铭牌序列号 `058071510393`）。

---

## 1. 关键结论（2026-11-17 从现场 Bin 目录确认）

现场原厂软件（`2pin.exe` / `In-Vision8.2.exe`）**全部是 32 位（x86）**，
它带的相机 SDK 是 **`MVCAMSDK.dll`（x86，9 MB）**，并且通过 HALCON 的
`hAcqMindVision8.dll` 加载它 —— 也就是我们适配的**经典 C 接口**（`CameraInit` 一族）。

> 现场机器上**没有** `MVCAMSDK_X64.dll`。
> 所以：**64 位程序加载不了现场这份 DLL，用 32 位包（`build_exe_x86.cmd`）才能直接用**。

本目录现在放的就是从现场 Bin 里取出来的那份：

| 文件 | 说明 |
| --- | --- |
| `MVCAMSDK.dll` | 现场原版 x86 C 接口 SDK（已随 32 位包打进去） |
| `MVImageProcess.dll`、`MVGenICamConfig.dll` | 同目录依赖（图像处理 / GenICam 配置） |
| `Config.ini` | 现场相机参数（`[CMOS500C]`：`Exp=258`、`GainAll=32`、`WIDTH=2592`、`HEIGHT=1944`、`CapMode=1`） |

---

## 2. 三个包怎么选

| 包 | 架构 | 能否用现场那份 `MVCAMSDK.dll` | 适用 |
| --- | --- | --- | --- |
| `dist_x86/VisionApp-x86` | **x86** | ✅ 能（直接加载） | **老主机首选**；Win7 SP1 x86/x64、Win10/11 x64 都能跑 |
| `dist_win7/VisionApp-Win7` | x64 | ❌ 不行（需要 `MVCAMSDK_X64.dll`） | Win7 x64，且能从厂商拿到 x64 SDK 时 |
| `dist/VisionApp` | x64 | ❌ 不行（同上） | Win10/11 x64 新机器 |

若要走 64 位：问迈德威视要 **x64 版 SDK**，把 `MVCAMSDK_X64.dll` 放到本目录即可（程序会优先找它）。

---

## 3. SDK 查找顺序（程序自动找）

1. 环境变量 `MINDVISION_SDK`（库文件 / `mvsdk.py` / SDK 目录）
2. **本目录**（`<exe 或项目根>/sdk/mindvision/`）
3. `<exe 或项目根>/_deps/mindvision/`
4. 系统 PATH / 安装目录

支持三种后端，找到哪套用哪套：

| 后端 | 文件 | 接口 |
| --- | --- | --- |
| **c-api**（现场用这套） | `MVCAMSDK.dll` / `MVCAMSDK_X64.dll` | `CameraSdkInit` / `CameraInit` / `CameraGetImageBuffer` … |
| `mvsdk.py` | 官方 Python 绑定 | 同上（Python 版） |
| `genicam` | `MvCameraControl_class.py` + `MvCameraControl.dll` | `MV_CC_*` |

---

## 4. 验证

```powershell
# 在 32 位包目录里或项目根跑（用 32 位 Python 才能加载 x86 SDK）
C:\py38x86\python.exe tools\list_cameras.py
C:\py38x86\python.exe tools\list_cameras.py --open 0
```

| 现象 | 含义 |
| --- | --- |
| `未找到 MVSDK…` | 本目录里没有 DLL，或路径不对 |
| `没有枚举到迈德威视相机…` | **SDK 已加载成功**，但没找到相机（检查上电 / 网线 / 同网段 / 被原厂软件占用） |
| 打印型号+序列号 | 正常，可以开始检测 |

> 没有相机时枚举返回空列表是正常的（SDK 返回 `-16 No camera found!`），程序已把它当"0 台"处理，不会崩。
