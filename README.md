# VisionApp（一期骨架）

开源视觉检测系统。当前阶段：**模拟相机跑通 + 检测流水线骨架**，不依赖任何相机即可开发。

## 文档

| 文档 | 内容 |
| --- | --- |
| [README.md](README.md)（本文件） | 怎么跑、功能清单 |
| [setup.md](setup.md) | 开发环境搭建（macOS / 无显示器） |
| [ARCH.md](ARCH.md) | 架构决策（ADR-001~015），推翻旧决策要新增 ADR |
| [PROCESS.md](PROCESS.md) | 开发、联调、验收、发布流程 |
| [docs/项目计划.md](docs/项目计划.md) | 范围、里程碑、风险 |
| [docs/功能差距.md](docs/功能差距.md) | 与遗留 In-Vision 的逐项差距（D-01~D-36）+ 排期 |
| [docs/现场实操需求.md](docs/现场实操需求.md) | **现场实际操作流程 + R-01~R-22 必须项**（含现状对照、实施顺序、技术方案草稿） |
| [docs/现场信息采集清单.md](docs/现场信息采集清单.md) | **第一次去现场要采集什么**（相机/IO/协议/样张/待确认问题/待补截图/带回清单） |
| [docs/相机接入-MindVision.md](docs/相机接入-MindVision.md) | 迈德威视相机接入：需要哪些 SDK 文件、验证步骤、打包与风险 |
| [docs/老主机部署与驱动清单.md](docs/老主机部署与驱动清单.md) | **老检测机部署**：SDK 与驱动的区别、按系统版本定方案（Win7/32 位怎么办）、驱动版本怎么选、验证 3 步 |
| [docs/视频对照-实测参数.md](docs/视频对照-实测参数.md) | **现场视频反读出来的真实参数**（阈值 111/255、面积 25~999999、设计值 10000 实测 7626、.HPro 参数段实证） |
| [docs/原厂功能说明-InVision8.2.md](docs/原厂功能说明-InVision8.2.md) | **原厂界面 16 张截图逐屏整理**（菜单/检测设置/规则设置/结果/超级管理员），功能对齐与验收依据 |
| [docs/软件功能说明/](docs/软件功能说明/) | 上述截图原件（现场提供） |
| [docs/compose/spec/ui-theme.md](docs/compose/spec/ui-theme.md) | 界面主题设计令牌 |

## 环境

| 项 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11.9 | `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe` |
| OpenCV | 5.0.0 | `pip install opencv-python` |
| numpy | 2.4.6 | |
| PySide6 | 6.12 | 界面 |
| 镜像 | 清华 TUNA | `-i https://pypi.tuna.tsinghua.edu.cn/simple` |

> 本机 PATH 里没有 Python，命令请用完整路径，或先执行：
> `set PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%`

## 跑起来

```powershell
cd E:\lin\mv\visionapp

# 1) 命令行：模拟相机 20 帧，自动校验结果
python tools\demo_sim_camera.py --frames 20

# 2) 命令行：带抖动（模拟真实波动），看判定是否稳定
python tools\demo_sim_camera.py --frames 50 --jitter 2

# 3) 命令行：回放真实样图（原程序的 0121.bmp）
python tools\demo_sim_camera.py --source folder --path E:\lin\mv\Bin --frames 3 --out out\real

# 4) 图形界面（实时预览 + 结果表）
python tools\demo_gui.py
```

输出：`out\demo\frame_*.png`（带标注的截图）、`out\demo\result.json`（结构化结果）。

## 目录结构

```
visionapp/
├─ visionapp/
│  ├─ core.py       Frame / Rect / Point / SourceInfo
│  ├─ sources.py    FrameSource 抽象 + Folder/Synthetic/Rtsp/Uvc 实现
│  ├─ ops.py        检测算子（find_blobs / distance_pp / match_template / area_of_blob / angle_ppp）
│  └─ recipe.py     Recipe（配方 JSON）+ Pipeline（执行 + 画标注）
├─ recipes/
│  └─ sim_demo.json 演示配方
├─ tools/
│  ├─ demo_sim_camera.py  命令行演示（含与真值的误差统计）
│  └─ demo_gui.py         PySide6 界面演示
└─ out/            运行输出
```

## 帧源

| 类型 | 用途 | 状态 |
| --- | --- | --- |
| `synthetic` | 合成画面，带真值，**当前开发主用** | ✅ 可用 |
| `folder` | 回放图片目录（黄金样张/原程序截图） | ✅ 可用 |
| `rtsp` | 网络相机（MaixCam Pro 开 RTSP 推流） | ⏳ 相机需联网 |
| `uvc` | 本地 USB 摄像头 | ✅ 可用 |
| `mindvision` | 工业相机 MindVision（迈德威视 MV-GE500M-TPO / GigE） | 🔧 代码就绪（支持 MVCAMSDK_X64.dll / mvsdk.py / GenICam 三套 SDK），待 SDK 文件+真机（[说明](docs/相机接入-MindVision.md)） |

新增工业相机：在 `sources.py` 里实现 `FrameSource` 子类，并在 `open_source()` 注册即可，
检测算法与配方完全不用改。

## 已实现的算子

| 算子 | 参数要点 | 输出 |
| --- | --- | --- |
| `find_blobs` | **threshold_low/threshold_high 区间阈值**（1~254 限幅）、threshold（单阈值兼容）、threshold_mode / blur / morph_kernel / min_area / max_area / min_width / min_height / min_circularity、**pick_zone（抓取位置区位）/ show_pick / show_mask（红色屏蔽区）** | 斑块列表（中心、面积、外框、抓取点） |
| `distance_pp` | src / blob_a / blob_b / mm_per_px | 两点距离（px 或 mm） |
| `match_template` | template / min_score | 最佳位置 + 相似度分数 |
| `area_of_blob` | src / index / mm2_per_px2 | 面积 |
| `angle_ppp` | points（3 点） | 夹角 |

## 产线运行（P0）

界面工具条从左到右：`新建/打开/保存/另存为/导入.HPro` → `配方下拉 + 管理` → `相机` → `记录设置`
→ `模式(连续/单次) + 开始/停止/单帧/清空计数`。

右侧"运行统计"面板实时显示：**总判定 / OK / NG / 良率 / 总数 / FPS / 单帧耗时**。

| 功能 | 说明 |
| --- | --- |
| 连续运行 | 循环取图检测，直到点"停止" |
| 单次运行 | 跑一帧就停（对应产线单次触发） |
| 配方快速切换 | 工具条下拉框，运行中也能切（切完立刻用新配方） |
| 配方管理 | 对话框里新建/复制/重命名/删除/加载 |
| 相机 | 支持 合成画面 / 图片目录 / UVC 摄像头 / RTSP 网络相机 |
| 结果记录 | CSV（每天一个文件）+ NG 图像自动存盘 |

结果目录结构：

```
results/
  <配方名>/
    2026-11-14.csv
    NG/2026-11-14/223321_frame000012.png
```

CSV 表头：`时间, 帧号, 判定, 耗时ms, <各检测项 id...>`

自测：

```powershell
python tools\selftest_runner.py    # 连续/单次/计数/CSV/NG存图/运行中切配方
```

## 标定（像素 ↔ 毫米）

工具条 **"标定(两点)"** → 在图像上点两个点（两点之间的实际距离已知）→ 输入实际毫米数 → 自动算出 `mm/px`。

- 标定结果**跟着配方保存**（`calibration` 字段），换配方就换标定；
- 标定后，`distance_pp` / `area_of_blob` 里 `mm_per_px`、`mm2_per_px2` 留 **0** 就自动用标定值；
- 右侧统计面板显示当前标定；画面会用粉色虚线画出标定线；
- "清除标定" 一键回到像素单位。

```powershell
python tools\selftest_calibration.py    # 计算 / 序列化 / 毫米换算 / 取消标定回像素
python tools\selftest_ui_calib.py       # 界面流程：标定→刷新→跑毫米→存盘重载→清除
```

> 后续可扩展：棋盘格标定（消除镜头畸变、支持非线性）、多相机各自标定。

## 多机位（已实现，默认 6 路）

产线配置在 **`config/line.json`**（6 机位演示用）；**现场是 2 机位**，用 `config/line_field.json`（迈德威视 ×2 + `recipes/2pin_imported.json`）：

```jsonc
{
  "name": "默认产线（6 机位）",
  "merge_rule": "all_ok",              // all_ok | any_ok | per_station
  "stations": [
    { "id": 1, "name": "机位1", "enabled": true,
      "source": { "kind": "synthetic" },          // 或 uvc / rtsp / folder
      "recipe": "recipes/sim_demo.json" }
    // … 共 6 个
  ]
}
```

| 能力 | 说明 |
| --- | --- |
| 同屏显示 | 3×2 网格，6 个画面同屏；点击任一画面切换为"当前机位" |
| 独立运行 | 每个机位独立线程取图+检测，互不阻塞 |
| 独立配方 | 每机位可挂不同配方（多型号混合产线） |
| 独立统计 | 每块画面头部显示 判定/OK/NG/良率/耗时，右侧显示当前机位详情 |
| 合并判定 | 工具条"合并"下拉：全部 OK 才 OK / 任一 OK 即 OK / 各机位独立 |
| 独立记录 | 结果写到 `results/<机位名>/<配方>/<日期>.csv` + NG 图 |
| 整线状态 | 状态栏右侧大字显示"整线：OK/NG + 合计计数" |
| 报警 | 按整线判定触发（NG 才响，最小间隔防吵） |

启动：默认就是 6 机位模式（桌面快捷方式 `VisionApp`）。

```powershell
python tools\visionapp_gui.py                        # 6 机位（config/line.json）
python tools\visionapp_gui.py --line config\line.json --run 1
python tools\visionapp_gui.py --single --source uvc --index 0    # 单机位调试
```

自测：

```powershell
python tools\selftest_multistation.py   # 6 路并发/合并规则/界面网格
```

## 产线配置界面（不用改 JSON）

工具条 **"产线配置"** 打开配置窗口：

- 左侧机位列表：**添加机位 / 删除机位 / 上移 / 下移**；
- 右侧每机位设置：名称、启用、**相机类型**（模拟/图片回放/UVC/RTSP）、相机参数、**配方**；
- **本机位输出/报警**：勾选"使用全局"或点"配置本机位…"单独设置；
- 顶部：产线名称、合并判定规则；
- **保存并应用** 后立即重建所有机位并写回 `config/line.json`（用户全程不碰 JSON）。

## 权限（操作员 / 工程师）

工具条 **"权限"**：

| 角色 | 能做什么 |
| --- | --- |
| 操作员 | 开始/停止、单帧、切换机位、打开配方、看结果——**编辑面板整体禁用** |
| 工程师 | 全部：改检测项/参数/判定、标定、产出线配置、输出/记录设置 |

- 设置密码后（`config/app_settings.json` 只存 SHA-256 摘要，不存明文），启动即为**操作员**，点"权限"输入密码进入工程师；
- 工程师模式下可"清除密码"取消权限控制。

## 机位独立输出 / 报警

每个机位可单独配置输出通道与报警（默认继承全局）：

- 机位 A 走 `COM3` 发协议、机位 B 走 TCP、机位 C 用全局——互不影响；
- 报警同样按机位独立（该机位出 NG 才响），另有"整线报警"开关；
- 配置入口：**产线配置 → 选中机位 → 配置本机位…**

## 输出与报警（P1）

工具条 **"输出/报警"** 里配置：

| 项 | 说明 |
| --- | --- |
| 通道 | **串口**（RS232/USB 转串口）/ **TCP** / **文件**（没有硬件时联调用） |
| 协议模板 | `{time},{verdict},{frame},{pitch}\r\n`，可用占位符：`{time} {date} {frame} {verdict} {result} {elapsed}` 和任意检测项 id |
| 发送开关 | OK 是否发送、NG 是否发送 |
| 测试发送 | 不接产线也能验证通讯是否通 |
| NG 报警 | 响铃开关、可选 wav、最小间隔（防吵） |

示例模板：

```
{time},{verdict},{frame},{pitch},{pin_area}\r\n      # 文本协议
{result}\r\n                                          # 1=OK 0=NG，最简单
```

自测：

```powershell
python tools\selftest_outputs.py    # 模板/文件/串口(假串口)/TCP/报警
```

## 启动方式（Windows 点击即用）

| 入口 | 说明 |
| --- | --- |
| 桌面 `VisionApp` 快捷方式 | 合成画面（开发调试） |
| 桌面 `VisionApp-样图回放` 快捷方式 | 回放 `E:\lin\mv\Bin` 里的原程序样图 |
| `启动VisionApp.cmd` / `启动VisionApp-样图回放.cmd` | 同上，双击即可 |

启动日志写在 `logs\gui.log`（pythonw 没有控制台，异常都记在这里）。

## 打包成 exe（检测机部署）

生产检测机上跑的是 exe，不是源码树。打包：

```powershell
build_exe.cmd                    # 等价于 pyinstaller packaging\VisionApp.spec --noconfirm --clean
```

产出 `dist\VisionApp\VisionApp.exe`（onedir，约 260 MB，免安装绿色目录）。把**整个 `dist\VisionApp` 文件夹**拷到检测机（建议 `D:\VisionApp`），双击 `VisionApp.exe` 即可。

| 项 | 说明 |
| --- | --- |
| 资源 | `config/`、`recipes/`、`samples/`、`docs/` 打进 exe 内部（`_internal`） |
| 首次启动 | 自动释放到 exe 旁边；之后配置、配方、结果、日志都写在 exe 所在目录 |
| 结果 | `results\<机位>\<配方>\<日期>.csv` + `NG\<日期>\*.png` |
| 日志 | `logs\gui.log`（exe 无控制台，异常/启动记录都在这里） |
| 升级 | 重新打包后整体替换；保留原 `config/` 与 `recipes/` 即可沿用配方 |
| 启动参数 | 与 python 版一致：`VisionApp.exe --frames 3 --run 1`（自检跑 3 帧自动退出）、`--shot D:\ui.png`（截图自检）、`--single --source uvc --index 0` |
| 系统要求 | **Windows 10 / 11 x64**（Python 3.12 + Qt 6.12）；Win7/8.1/32 位需另做兼容版，见 [老主机部署与驱动清单](docs/老主机部署与驱动清单.md) |
| 环境体检 | 到老机器上跑 `tools\check_env.ps1`（右键用 PowerShell 运行），把生成的 `env_report.txt` 发回 |

> ⚠️ **必须用 `build_exe.cmd` 打包，不要直接跑 `pyinstaller`**：脚本会把 `_deps\cv`、`_deps\qt` 放进
> `PYTHONPATH`，否则 PyInstaller 找不到 opencv/PySide6，打出来的 exe 一启动就报
> `ModuleNotFoundError: No module named 'cv2'`（这个坑已经踩过一次）。

### 两套包

| 包 | 适用系统 | 技术栈 | 构建命令 | 产物 |
| --- | --- | --- | --- | --- |
| **主包** | Windows 10 / 11 x64 | Python 3.12 + Qt6（PySide6） | `build_exe.cmd` | `dist\VisionApp\VisionApp.exe` |
| **Win7 兼容包** | Windows 7 SP1 x64 起，Win10/11 也能跑 | Python 3.8 + Qt5（PySide2） | `build_exe_win7.cmd` | `dist_win7\VisionApp-Win7\VisionApp-Win7.exe` |
| **32 位包（现场首选）** | Win7 SP1 x86/x64、Win10/11 x64 | Python 3.8 **x86** + Qt5 | `build_exe_x86.cmd` | `dist_x86\VisionApp-x86\VisionApp-x86.exe`（能直接用现场 x86 的 `MVCAMSDK.dll`） |

Win7 包需要先装 **KB2999226**（UCRT 补丁）。两套包功能一致，差异见 [老主机部署与驱动清单](docs/老主机部署与驱动清单.md)。

> 打包要用 **Python 3.11/3.12 x64**（Win7 包用 3.8 x64），且项目路径别太深（Windows 260 字符路径限制）。
> `_deps\` 只是本机验证用的依赖目录，正式打包建议在 venv 里 `pip install -r requirements.txt pyinstaller`。

## .HPro 导入器（映射表可配置）

```powershell
python tools\hpro_import.py --hpro "E:\lin\mv\2pin.HPro" --report out\2pin_import.md
```

- 映射表：`config\hpro_types.json` —— **改这里不用改代码**；
- 每个类型可配：`meaning`（含义）、`op`（目标算子）、`params`（默认参数）、
  `judge_from_rule`（是否取原模板判定）、`role: baseline`（作为基准项供 `$baseline` 引用）；
- 判定换算：`judge.modes` 里按模式号配置表达式，例如 `"5": {"expr": "value2"}`、
  `"0": {"expr": "low ~ low + tol"}`；
- 线形 ROI（高度为 0）会按 `roi.line_roi_thickness` 自动加厚；
- 未配置类型按 `unmapped.action` 处理（`skip` 跳过 / `placeholder` 建占位项）。

界面里也有对应按钮：工具条 **"导入 .HPro"**。

导入 `2pin.HPro` 的实测结果：11 项全部映射成功，报告见 `out\2pin_import_report.md`。

## 图形界面（正式版）

```powershell
python tools\visionapp_gui.py                                   # 合成画面
python tools\visionapp_gui.py --source folder --path E:\lin\mv\Bin
python tools\visionapp_gui.py --recipe recipes\sim_demo.json
```

界面能力：

- 左侧实时画面：**空白处拖拽 = 新建 ROI**；拖框内 = 移动；拖四角 = 缩放；Delete = 删除；
  滚轮缩放视图；
- 右侧检测项列表：新增 / 复制 / 删除 / 上移 / 下移；
- 参数表单按算子类型自动生成（int / float / 下拉 / 文件选择 / 文本）；
- 判定编辑（区间 / 上限 / 下限）；
- 工具条：新建配方、打开配方、保存、另存为、暂停/继续、单帧；
- 结果表：每项数值 + OK/NG + 说明，顶部大字号总判定与耗时。

无显示器自测：

```powershell
python tools\selftest_ui.py     # 加载→跑帧→加项→设ROI→存盘→重载 一致性校验
```

## .HPro 模板解析（原程序配方）

```powershell
python tools\hpro_inspect.py --file "E:\lin\mv\2pin.HPro" --json out\2pin.hpro.json
```

已解出的结构：

| 层级 | 内容 |
| --- | --- |
| 第 1 行 | 机位数（站数） |
| 站头 | `<站号> <检测项数>` |
| 检测项 | ①参数行（7 段，制表符分隔）②ROI 行 `x1 y1 x2 y2` ③附加行（判定规则 + 评分阈值 `0 200 0 0.85` + 标志位） |
| 站尾 | 输出/组别配置，如 `4 0 0.000 0.120 0.120 1 1` |

实测结果：

| 模板 | 站数 | 检测项 | 类型号 |
| --- | --- | --- | --- |
| `2pin.HPro` | 2 | 6 + 5 = 11 | 1, 2, 3, 11, 12, 13, 14 |
| `测试.HPro` | 2 | 1 + 1 | 1 |
| `11.HPro` | 1 | 1 | 1 |
| `22.HPro` | 1 | 1 | 1 |

判定规则行的形态（推测，待黄金样张验证）：

```
1 1 0 -1 -0.100 0.080 0.080 100 99999 0 0 0     # 基准项=1, 下限=-0.100, 公差0.080
-1 5 0 -1 300.000 1000.000 100.000 100 99999 0 0 0   # 范围 300.000 ~ 1000.000
```

## 下一步（M1 → M2）

1. 把 `.HPro` 的 type 号（1/2/3/11/12/13/14）映射到本系统的算子，做成"导入即用"；
2. 黄金样张回归：`tests/` + pytest，比对数值与 OK/NG；
3. 采样标定工具（棋盘格）→ 像素→毫米换算；
4. 工业相机接入（pylon / MVS / Galaxy）；
5. 多机位并发 + 合并判定。
