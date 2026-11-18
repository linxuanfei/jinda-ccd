# 迈德威视相机接入（合并版）

> 现场相机：**MV-GE500M-TPO**（迈德威视，**网口 GigE**，500 万像素，铭牌序列号 **058071510393**）
> 代码：`visionapp/sources_mindvision.py`（**三套 SDK 后端都支持**，自动选）
> 状态：**SDK 已拿到并验证可加载**（2026-11-17，现场 Bin 里的 `MVCAMSDK.dll` x86）；**等相机上电做真机取图**

---

## 1. SDK 不是驱动（先分清）

| | 驱动 Driver | SDK |
| --- | --- | --- |
| 作用 | 让 **Windows 认识相机**（设备管理器里能看到） | 让 **我们的程序**能取图、设参数 |
| 怎么装 | 跑厂商驱动安装程序 | **拷文件**到 `sdk/mindvision/`，不用安装 |
| 缺了会怎样 | 设备管理器黄色感叹号 | 我们的程序报"未找到 MVSDK" |

**GigE 相机一般不用装驱动**：网卡与相机同网段 + 巨帧 9014 + 关反向路径过滤即可。

---

## 2. 支持的三套 SDK（自动选，找到哪套用哪套）

| 优先级 | 接口 | 需要放的文件 | 说明 |
| --- | --- | --- | --- |
| ① | **经典 C 接口** | `MVCAMSDK.dll`（x86，**现场就是这份**）/ `MVCAMSDK_X64.dll`（x64 需另要）/ `libMVSDK.so` | **MV-GE 系列首选**；`CameraInit/CameraGetImageBuffer` |
| ② | 官方 Python 绑定 | `mvsdk.py`（SDK 例程里带的） | 有就用，函数同 C 接口 |
| ③ | GenICam 风格 | `MvCameraControl_class.py` + `MvCameraControl.dll` | 新版 SDK 才有 |

**放到哪**（按顺序找）：

1. 环境变量 `MINDVISION_SDK`（可指库文件 / `mvsdk.py` / SDK 目录）
2. `<exe 或项目根>/sdk/mindvision/`  ← **推荐**
3. `<exe 或项目根>/_deps/mindvision/`
4. 系统 PATH / 安装目录里的 `MVCAMSDK_X64.dll`

> ⚠️ **架构必须匹配**：现场 SDK 是 **x86**，要用 **32 位包**（`build_exe_x86.cmd` → `dist_x86/VisionApp-x86`）。
> 64 位包加载不了 x86 DLL，要 64 位就得问厂商要 `MVCAMSDK_X64.dll`。
> 详见 [sdk/mindvision/README.md](../sdk/mindvision/README.md)。
>
> 打包时 `packaging/*.spec` 会把 `sdk/mindvision/` 一起打进 exe，所以**放好再打包**，现场不用单独装 SDK（驱动仍需装一次）。

---

## 3. 相机参数与行为（对齐现场）

| 项 | 行为 |
| --- | --- |
| **按型号+序列号锁定** | 默认找 `MV-GE500M-TPO` / `058071510393`；对不上就报错停下，**不偷偷开旁边那台**，也不用合成图/UVC 冒充 |
| **关自动曝光** | 打开即 `CameraSetAeState(0)` |
| **触发模式** | `hardware`=2 硬件触发（**默认**，产线用）/ `continuous`=0 连续采集（调参用）/ `software`=1 软触发 |
| **曝光** | 界面填**毫秒**，下发时 ×1000 转**微秒**（`CameraSetExposureTime` 单位 us） |
| **增益** | `CameraSetAnalogGain` 原样下发（不套手册里没有的范围） |
| **帧率** | `CameraSetFrameRate`（整 Hz）；这份 SDK 没导出该函数时**只提示不下发**，不中断 |
| **像素格式** | 先请求 BGR8，失败退 Mono8，再不行才报错 |
| **取不到图** | 超时返回空帧（调用方按丢帧处理），不会抛异常刷屏 |
| **描述信息** | `describe()` → `MV-GE500M-TPO  SN=058071510393  后端=c-api  触发=硬件触发  曝光=25.5ms` |

界面上：**相机 → 类型选"工业相机 MindVision"** → 填序列号/型号/曝光/增益/触发 → 连接。

---

## 4. 现场验证顺序（装好驱动 + SDK 后 10 分钟）

| # | 动作 | 通过标准 |
| --- | --- | --- |
| 1 | 设备管理器 → 图像设备 | 看得到 `MV-GE500M-TPO`，无黄色感叹号 |
| 2 | 厂商 GigE 配置工具 | 能搜到相机、能预览 |
| 3 | `python tools\list_cameras.py` | 打印型号 + 序列号 + 端口（GigE） |
| 4 | `python tools\list_cameras.py --open 0` | 抓一帧存 `out\camera_test.png`，尺寸对 |
| 5 | 界面连接 → 开始 | 出图、FPS 稳定、状态栏显示后端与触发 |
| 6 | 触发切 hardware，给外部信号 | 每来一个信号出一帧 |
| 7 | 拔网线 | 明确报错，不静默卡死（重连待做） |

---

## 5. 已知限制

| # | 限制 | 处理 |
| --- | --- | --- |
| 1 | 没做**断线重连** | 现场重启软件；后续补 |
| 2 | 多相机同型号要靠**序列号**区分 | 配置里填 SN，不填且多台会直接报错 |
| 3 | 分辨率/曝光/增益范围**不写死** | 按 SDK 实际能力下发；手册到手后再补默认值 |
| 4 | 只有迈德威视一家 | 海康/大恒/巴斯勒要各自适配（架构已留口子：`sources.py` 加子类） |

---

## 6. 自测

```powershell
python tools\selftest_mindvision.py     # 假后端：选相机/参数下发/取图/像素转换，32 项
python tools\list_cameras.py            # 真机：列设备
python tools\list_cameras.py --open 0   # 真机：抓一帧
```

`selftest_mindvision.py` 用一个假后端验证整条链路：默认硬件触发(2)、曝光 30ms→30000us、序列号不符/型号不符/多台同型号都会**明确报错**。
