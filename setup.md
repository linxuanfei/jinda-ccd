# VisionApp 开发环境搭建（macOS）

> 工业视觉检测软件（替代原厂 In-Vision 的新实现）。
> Python 3.11 + OpenCV + PySide6 桌面应用，**生产机用 Windows，mac 用于开发与算法调试**。

---

## 0. 这是什么

一套**多机位（默认 6 路）工业视觉检测软件**，功能：

| 模块 | 说明 |
| --- | --- |
| 多机位同屏 | 3×2 网格显示 6 路画面，每路独立相机/配方/统计 |
| 检测算子 | 斑块检测、点距、面积、模板匹配、三点夹角（可扩展） |
| 配方 | JSON 格式，界面可编辑 ROI/参数/判定；支持导入原厂 `.HPro` 模板 |
| 标定 | 图像上两点标定，像素 ↔ 毫米（随配方保存） |
| 结果 | CSV 逐帧记录 + NG 图像自动存盘（按机位/配方/日期分目录） |
| 输出 | 串口（RS232/USB）、TCP、文件；协议模板可配 |
| 报警 | NG 响铃，可换 wav，最小间隔防吵 |
| 权限 | 操作员（只运行）/ 工程师（可改配置），密码只存 SHA-256 |
| 产线配置 | 界面化增删机位、指定相机与配方、设合并判定规则 |

---

## 1. 环境要求

- **macOS 12+**（Apple Silicon 或 Intel 均可）
- **Python 3.11**（最稳，3.12 一般也能跑）
- **Xcode Command Line Tools**
- 磁盘约 2 GB

```bash
xcode-select --install     # 如果还没装
```

---

## 2. 安装步骤

### 2.1 安装 Python 3.11

```bash
# 方式 A：Homebrew（推荐）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.11

# 方式 B：官网安装包
# https://www.python.org/downloads/release/python-3119/
# 安装 macOS 64-bit universal2 installer
```

### 2.2 创建虚拟环境并安装依赖

```bash
cd visionapp

python3.11 -m venv .venv
source .venv/bin/activate

python -m pip install -U pip
pip install -r requirements.txt
```

网络慢可用国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

依赖（`requirements.txt`）：

```
numpy>=1.26
opencv-python>=4.9
PySide6>=6.6
pyserial>=3.5
pytest>=8.0
```

> 打包这份代码的机器（Windows）实测版本：`numpy 2.4.6 / opencv-python 5.0.0.93 / PySide6 6.12.0 / pyserial 3.5`

### 2.3 验证安装（不需要相机）

```bash
# 运行引擎（连续/单次、计数、CSV、NG 存图）
python tools/selftest_runner.py

# 6 机位并发 + 合并判定（无显示器也行）
QT_QPA_PLATFORM=offscreen python tools/selftest_multistation.py

# 标定换算
python tools/selftest_calibration.py
```

全部出现 `RESULT: PASS` 即环境就绪。

---

## 3. 启动开发

| 场景 | 命令 |
| --- | --- |
| **6 机位主界面**（默认） | `python tools/visionapp_gui.py` |
| 一键脚本（自动建 venv/装依赖） | `chmod +x run.sh && ./run.sh` |
| 单机位调试 | `python tools/visionapp_gui.py --single --source synthetic` |
| 回放图片目录 | `python tools/visionapp_gui.py --single --source folder --path ~/Pictures/test` |
| USB/UVC 摄像头 | `python tools/visionapp_gui.py --single --source uvc --index 0` |
| 网络相机 RTSP | `python tools/visionapp_gui.py --single --source rtsp --url rtsp://<ip>:8554/live` |
| 无界面跑流水线 | `python tools/demo_sim_camera.py --frames 50 --jitter 2` |
| 解析原厂模板 | `python tools/hpro_inspect.py --file samples/2pin.HPro` |
| 导入原厂模板为配方 | `python tools/hpro_import.py --hpro samples/2pin.HPro` |

无显示器/SSH 环境：

```bash
export QT_QPA_PLATFORM=offscreen
```

---

## 4. 目录结构

```
visionapp/
├─ run.sh                    # macOS 启动脚本
├─ requirements.txt
├─ config/
│  ├─ line.json              # 产线/机位配置（界面"产线配置"保存到这里）
│  ├─ app_settings.json      # 记录/输出/报警/权限
│  └─ hpro_types.json        # 原厂 .HPro 类型 → 本系统算子 的映射表
├─ recipes/                  # 配方（每个型号/机位一套）
├─ samples/
│  └─ 2pin.HPro              # 原厂模板样例（用来试 hpro_inspect / hpro_import）
├─ docs/
│  └─ 项目计划.md            # 项目背景、架构与路线图
├─ visionapp/
│  ├─ core.py                # Frame / Rect / Point
│  ├─ sources.py             # 帧源（synthetic / folder / uvc / rtsp）
│  ├─ ops.py                 # 检测算子（注册表）
│  ├─ recipe.py              # 配方 + 流水线
│  ├─ calibration.py         # 两点标定
│  ├─ runner.py              # 运行引擎（线程、计数、快照）
│  ├─ recorder.py            # CSV + NG 图落盘
│  ├─ outputs.py             # 串口 / TCP / 文件输出
│  ├─ alarm.py               # NG 报警
│  ├─ security.py            # 权限
│  ├─ stations.py            # 机位 / 产线 / 多机位管理
│  ├─ settings.py            # 应用设置
│  ├─ hpro.py / hpro_import.py   # 原厂模板解析与导入
│  └─ ui/                    # PySide6 界面
├─ tools/                    # 启动入口 + 自测脚本
└─ results/                  # 运行产物（不提交）
```

---

## 5. 主要扩展点

### 加一个检测算子

1. `visionapp/ops.py` 里写函数并用 `@register("算子名")` 装饰：

```python
@register("my_op")
def my_op(frame, item, ctx) -> ItemResult:
    p = item.params
    ...
    res = ItemResult(item_id=item.id, type=item.type, value=v, unit="px")
    res.ok = _apply_judge(item.judge, res.value)
    return res
```

2. `visionapp/ui/main_window.py` 的 `OP_SCHEMA["my_op"]` 加参数表单定义，界面自动生成控件。

### 加一个相机

`visionapp/sources.py` 实现 `FrameSource` 子类（`open/read/close/info`），并在 `open_source()` 注册。
工业相机（Basler pylon / 海康 MVS / 大恒 Galaxy）注意：**SDK 基本只有 Windows 版**，mac 上做不了，需在 Windows 侧对接。

### 加一个输出通道

`visionapp/outputs.py` 里加 `OutputChannel` 子类 + 在 `build_channel()` 注册，界面"输出/报警"即可选择。

---

## 6. 约定

- 编码：**UTF-8**，中文注释可以
- 路径：所有相对路径都以 `visionapp/` 为基准（`BASE_DIR`）
- 配方：`recipes/<名字>.json`，保存时会自动留 `.bak`
- 结果：`results/<机位名>/<配方名>/<日期>.csv` + `NG/<日期>/*.png`
- 测试：每个 `tools/selftest_*.py` 都要能独立跑通，改了核心逻辑请一并跑一遍

---

## 7. macOS 注意事项

| 项 | 说明 |
| --- | --- |
| 工业相机 | 海康 MVS / 大恒 Galaxy 只有 Windows 驱动；Basler pylon 有 macOS 版（Intel）。mac 开发用 `synthetic` / `folder` / UVC 摄像头 |
| UVC 摄像头 | `--source uvc --index 0`，mac 首次会弹"允许访问摄像头" |
| 串口 | mac 上是 `/dev/tty.usbserial-XXXX`，在"输出/报警 → 串口参数 → 端口"里填这个路径 |
| 串口编码 | 默认 `gbk`（兼容老 PLC），mac 上若对接 utf-8 设备，在 `config/app_settings.json` 的 `output.encoding` 改 `utf-8` |
| `.cmd` 文件 | `启动VisionApp.cmd` 等是 Windows 专用，mac 用 `run.sh` 或 python 命令 |
| 中文输出 | 终端建议 `export PYTHONIOENCODING=utf-8`，否则命令行打印可能乱码 |
| 高 DPI | PySide6 自动处理；如界面发虚，可在启动命令前加 `QT_ENABLE_HIGHDPI_SCALING=1` |

---

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| `qt.qpa.plugin: Could not load the Qt platform plugin "cocoa"` | 重装 PySide6：`pip install --force-reinstall PySide6`；确认 Python 与依赖位数一致（arm64 Python + arm64 wheel） |
| `ImportError: ... cv2` | `pip install --force-reinstall opencv-python` |
| 启动就报 encoding 错误 | `export PYTHONIOENCODING=utf-8` |
| `RuntimeError: main thread is not in main loop` | 不要在子线程里操作 Qt 控件（Runner 是后台线程，界面靠 `_poll` 轮询） |
| 测试脚本卡住 | 加 `QT_QPA_PLATFORM=offscreen`，且确保弹窗没被阻塞（自测脚本不调用 `exec()`） |

---

## 9. 后续任务（roadmap）

1. **工业相机适配器**：`sources.py` 增加 `PylonSource` / `MvsSource` / `GalaxySource`（Windows 生产机）
2. **算子扩充（M2）**：卡尺测距、直线/圆拟合、正位度、参差度、缺角、相似度比对
3. **`.HPro` 类型映射确认**：`config/hpro_types.json` 里的 type 号语义需现场样张确认
4. 多机位 IO 输出映射（按机位独立端口）已在数据结构预留（`StationConfig.output`）

---

## 10. 打包/提交说明

不要提交/打包：`out/`、`results/`、`logs/`、`__pycache__/`、`.venv/`、`*.bak`
（`.gitignore` 已列好）

---

## 附：Windows 侧速查（如需对照）

```powershell
# 生产机启动（.cmd 已带 UAC/路径处理）
启动VisionApp.cmd              # 6 机位
启动VisionApp-样图回放.cmd     # 单机位回放
```
