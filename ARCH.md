# ARCH.md — VisionApp 架构决策记录

> 本文件记录新项目（visionapp）的架构决策（ADR 风格）。
> 遗留参考系统：`~/localcode/oldvision/2pin`（In-Vision 8.2，汉特士，无源码，只有部署包）。
> 决策一旦写下默认生效；推翻某条时**新增一条 ADR** 并注明废弃旧条，不直接改写历史。

---

## 背景与目标

- **是什么**：产线视觉检测软件，复刻遗留 In-Vision 的核心检测能力，多机位、可离线运行。
- **为什么重写**：遗留系统无维护、闭源、依赖 HASP 加密狗与一套过时的 Windows 工具链；新项目用开源栈，macOS 开发 + Windows 生产。
- **一致性验收口径**：同一批黄金样张，新旧系统逐项数值与 OK/NG 判定一致（允许标定换算后等效）。

功能差距清单（缺失项）单独维护在 `docs/功能差距.md`；本文件只定"怎么做"，不定"还差什么"。

---

## ADR-001 技术栈：Python 3.11 + PySide6 + OpenCV + numpy

- **状态**：已采纳
- **决策**：
  - 语言 Python 3.11；界面 PySide6 (Qt 6, LGPL)；算法 OpenCV (BSD-3) + numpy (BSD)；串口 pyserial。
  - 打包 PyInstaller onedir；测试 pytest（现阶段以 `tools/selftest_*.py` 为门禁）。
  - **禁用** AGPL 组件（如 Ultralytics YOLO）；可选 AI 走 onnxruntime（MIT）。
- **理由**：视觉生态最好、与 MaixCam 同语言、跨 Win/mac；许可可商用。
- **后果**：工业相机 SDK 基本只有 Windows → mac 只做开发/算法调试，生产固定 Windows。

## ADR-002 分层与目录：单包 `visionapp/`，按职责分模块

- **状态**：已采纳
- **决策**：保持现有扁平模块，不拆多包：

  ```
  visionapp/
  ├─ core.py         Frame / Rect / Point / SourceInfo（纯数据）
  ├─ sources.py      FrameSource 抽象 + synthetic/folder/rtsp/uvc
  ├─ ops.py          检测算子注册表（@register）
  ├─ recipe.py       Recipe(JSON) + Pipeline 执行与标注
  ├─ calibration.py  两点标定（像素↔毫米）
  ├─ runner.py       单机位运行引擎（线程、模式、快照）
  ├─ stations.py     机位/产线配置与多机位管理
  ├─ recorder.py     CSV + OK/NG 图落盘
  ├─ outputs.py      输出通道（file/serial/tcp）+ 协议模板
  ├─ alarm.py        NG 报警（wav、最小间隔）
  ├─ security.py     操作员/工程师（SHA-256）
  ├─ settings.py     应用设置读写
  ├─ hpro.py         .HPro 文本解析
  ├─ hpro_import.py  .HPro → Recipe 导入（映射表驱动）
  └─ ui/             PySide6 界面
  ```
- **理由**：当前规模（~5k 行）不需要子包；`docs/项目计划.md` 里的 `ops/`、`sources/` 多文件拆分留到算子明显膨胀时再做。
- **后果**：新增算子改 `ops.py` + `ui/main_window.py` 的 `OP_SCHEMA`；新增相机改 `sources.py`。

## ADR-003 帧源抽象：`FrameSource` 四方法，检测层零感知相机型号

- **状态**：已采纳
- **决策**：
  - 接口固定为 `open() / read(timeout_ms) -> Frame / close() / info -> SourceInfo`。
  - `Frame` 携带 `bgr`、`ts_ns`、`index`、`meta`（曝光/增益/触发源等）。
  - 新相机一律实现子类并在 `open_source()` 注册；**不**在 recipe/ops 里写任何相机分支。
- **理由**：一期已验证 synthetic/folder/uvc/rtsp 四路；工业相机只是又一个子类。
- **后果**：工业相机适配（pylon / MVS / Galaxy）只动 `sources.py`（见功能差距 D-01）。相机参数下发（曝光/增益/AOI）通过 `SourceInfo`/`meta` 或 source 专用方法暴露，不进配方。

## ADR-004 配方模型：JSON 单文件 + 判定挂在检测项上

- **状态**：已采纳
- **决策**：
  - 配方 = `recipes/<名字>.json`，含检测项列表、每项 `roi/params/judge`、`calibration`、版本字段。
  - 判定两级：项级 `judge`（range/upper/lower）→ 项 OK/NG；整件判定由 runner 汇总（默认 all-OK）。
  - 保存自动留 `.bak`；界面"另存为/克隆"支持多型号。
- **理由**：人可读、可 diff、可离线编辑；与 `.HPro` 导入器解耦。
- **后果**：**不**做数据库配方仓库（一期明确不做 MES/DB）。配方升级需带 `version` 字段做迁移。

## ADR-005 .HPro 兼容：映射表驱动导入，不运行时解析旧格式

- **状态**：已采纳
- **决策**：
  - 运行时只认 JSON 配方；`.HPro` 仅通过 `hpro_import` 一次性转换。
  - 类型号 → 算子/参数/判定的映射放在 `config/hpro_types.json`，**改表不改代码**。
  - 未映射类型按 `unmapped.action` 处理（skip / placeholder），导入报告必须列出全部 issue。
- **理由**：旧格式语义靠逆向猜测，必须可配置、可审阅；导入是一次性迁移而非运行时依赖。
- **后果**：类型语义确认前（功能差距 D-05），导入结果只能当草稿，必须过黄金样张比对。

## ADR-006 多机位：`config/line.json` 描述产线，每机位一线程

- **状态**：已采纳
- **决策**：
  - 产线配置在 `config/line.json`：产线名、`merge_rule`（all_ok/any_ok/per_station）、机位列表（id/name/enabled/source/recipe，可选独立 output/alarm）。
  - 每机位独立采集+检测线程，结果独立记录到 `results/<机位>/<配方>/<日期>.csv`。
  - 整线判定按 merge_rule 汇总；报警可按机位或整线触发。
  - 界面"产线配置"全程不碰 JSON。
- **理由**：对齐遗留的多相机多工站；6 路同屏已验证。
- **后果**：机位间不做共享内存/零拷贝；机位数上去后若 FPS 不足，再评估采集与检测分线程（现状合并在 Runner 内）。

## ADR-007 输出与报警：通道插件化，协议用模板字符串

- **状态**：已采纳
- **决策**：
  - `OutputChannel` 抽象 + `build_channel()` 注册；一期实现 file / serial / tcp（+Null）。
  - 协议内容 = 模板字符串 + 占位符（time/frame/verdict/检测项 id…），编码默认 gbk（可配 utf-8）。
  - OK/NG 分别开关；提供"测试发送"不接产线验通断。
  - 报警与输出解耦：`Alarm` 只管响铃/ wav / 最小间隔。
- **理由**：产线协议千差万别，模板比硬编码解析器稳；串口/TCP/文件覆盖联调到投产全阶段。
- **后果**：Modbus、硬件 IO 卡（PCI1761 类）、分段信号等见功能差距 D-08/D-09，作为新 `OutputChannel` 子类扩展，不改模板机制。

## ADR-008 标定：两点标定随配方保存，后续再加棋盘格

- **状态**：已采纳
- **决策**：
  - 一期只做两点标定（已知距离 → `mm/px`），结果写入配方 `calibration` 字段。
  - 算子里 `mm_per_px=0` 表示"用配方标定"。
  - 棋盘格/畸变校正/多相机各自标定 → 后续 ADR，不预先设计接口。
- **理由**：两点标定已覆盖当前产线换算需求；过早抽象畸变模型只会拖慢一期。
- **后果**：换相机/换镜头必须重标定；配方与标定绑定，迁移配方要连标定一起验收。

## ADR-009 权限：本地双角色，密码只存摘要

- **状态**：已采纳
- **决策**：
  - 角色仅 操作员 / 工程师；`config/app_settings.json` 只存 SHA-256，不存明文。
  - 启动默认操作员；工程师模式可改检测/配方/产线/输出；可清除密码取消管控。
- **理由**：对齐遗留"修改密码/超级管理员"的最低可用集；不做多用户账户系统。
- **后果**：**不做**遗留的 HASP 加密狗与机器码注册（ADR-011）。审计日志（AdminTime 式）见功能差距 D-14。

## ADR-010 线程与 UI：后台 Runner + 界面定时轮询

- **状态**：已采纳
- **决策**：
  - 检测全部在后台线程；UI 通过 `RunSnapshot` 定时 `_poll` 拿状态，**禁止**子线程碰 Qt 控件。
  - 界面命令（开始/停止/切配方/改 ROI）投递回主线程执行。
- **理由`RuntimeError: main thread is not in main loop` 是已知坑，写进 setup.md 常见问题。
- **后果**：低频操作（配置保存）可同步；高频路径只读快照。

## ADR-011 明确不做（一期）

| 项 | 决策 |
| --- | --- |
| HASP 加密狗 / 机器码注册 | 不做。开源交付，无授权守护。 |
| MES / 数据库对接 | 不做（项目计划一期范围外）。 |
| 硬件 IO 卡与 PLC | 一期不做；产线联调需要时按 ADR-007 扩展通道（差距 D-08/D-09）。 |
| 视频录像 | 一期不做（差距 D-11，按需排期）。 |
| OCR / 二次元测量 / FPC 专项 | 不在通用骨架；确有产线需求再单独立项。 |
| 运行时读 `.HPro` | 不做；只走导入器（ADR-005）。 |
| 多语言 i18n 框架 | 一期只做中文；英文界面等有需求再上（差距 D-17）。 |

## ADR-012 配置与路径约定

- **状态**：已采纳
- **决策**：
  - 一切相对路径以仓库根（`BASE_DIR`）为基准。
  - 配置三件套：`config/line.json`（产线）、`config/app_settings.json`（记录/输出/报警/权限）、`config/hpro_types.json`（导入映射）。
  - 运行产物：`results/`、`logs/`、`out/`、`*.bak` 一律不进版本库（`.gitignore` 已列）。
  - 文本编码 UTF-8；串口默认 gbk 兼容老 PLC。
- **理由**：mac 开发 + Windows 生产双环境，路径与编码是最常见的移植坑。

## ADR-013 测试策略：selftest 门禁 → pytest 黄金样张回归

- **状态**：已采纳（分两阶段）
- **决策**：
  - **阶段 1（现状）**：每个 `tools/selftest_*.py` 独立可跑、出现 `RESULT: PASS` 才算过；改核心逻辑必须全跑。
  - **阶段 2（补齐）**：建 `tests/` + pytest，黄金样张（原系统数值/判定）做回归；见 PROCESS.md 验收流程。
- **理由**：无显示器环境先要能自证；一致性验收必须可重复执行。
- **后果**：selftest 不替代黄金样张；二者都过才算可交付。

## ADR-014 文档分工

- **状态**：已采纳
- **决策**：
  - `ARCH.md`（本文件）：架构决策与 ADR。
  - `PROCESS.md`：开发、连调、验收、发布流程。
  - `docs/功能差距.md`：与 oldvision 的功能对比及缺失项清单（带优先级）。
  - `docs/原厂功能说明-InVision8.2.md`：原厂界面截图逐屏整理（功能事实来源；截图原件在 `docs/软件功能说明/`）。
  - `docs/项目计划.md`：范围、里程碑、风险（历史计划，里程碑以实际进度为准）。
  - `README.md` / `setup.md`：跑起来与环境搭建，不承载决策。
- **理由**：避免"计划文档当架构文档用"导致决策散落；**功能事实（截图整理）与功能差距（结论）分开维护**，截图可增量补、结论可随现场确认修订。

## ADR-015 UI 主题层：自研 QSS 设计令牌，禁止业务代码内联样式

- **状态**：已采纳（feat/ui-theme，spec 见 `docs/compose/spec/ui-theme.md`）
- **决策**：
  - 视觉方向：暗色科幻 HUD（深空底 + 青蓝强调 + OK/NG/WT 状态色），数值等宽排版；不闪烁不动效（产线光敏/疲劳安全）。
  - 实现：`visionapp/ui/theme.py` 集中设计令牌 + `build_qss()` 全局样式表 + `set_state/set_class/set_prop` 语义驱动；**业务代码禁止硬编码色值/字号/内联 `setStyleSheet`**。
  - 不引入第三方样式库：QDarkStyleSheet（MIT）仅作备选；QFluentWidgets 为 GPLv3/商用需授权，与 ADR-001 冲突，明确不用。
  - 一屏高信息密度不降级：工具条紧凑分组、1920 宽全动作可见，更窄屏溢出按钮可见可点。
  - 浅色主题/布局大重构留二期（令牌已预留扩展位）。
- **理由**：观感不统一的根源是散落的内联样式而非缺库；令牌层统一视觉、无许可风险、可扩展主题。
- **后果**：新增界面沿用 `theme` 模块取色/排版；Qt 陷阱（QWidget 子类需 `WA_StyledBackground` 才画 QSS 边框、QToolBar padding 主导 sizeHint）记录于 spec Journey log。

---

## 决策索引

| ADR | 标题 | 状态 |
| --- | --- | --- |
| 001 | 技术栈 Python/PySide6/OpenCV | 已采纳 |
| 002 | 单包分层目录 | 已采纳 |
| 003 | FrameSource 帧源抽象 | 已采纳 |
| 004 | JSON 配方模型 | 已采纳 |
| 005 | .HPro 映射表导入 | 已采纳 |
| 006 | line.json 多机位 | 已采纳 |
| 007 | 输出通道插件 + 协议模板 | 已采纳 |
| 008 | 两点标定随配方 | 已采纳 |
| 009 | 本地双角色权限 | 已采纳 |
| 010 | 后台线程 + UI 轮询 | 已采纳 |
| 011 | 一期明确不做清单 | 已采纳 |
| 012 | 配置与路径约定 | 已采纳 |
| 013 | selftest → pytest 两阶段测试 | 已采纳 |
| 014 | 文档分工 | 已采纳 |
| 015 | UI 主题层：自研 QSS 设计令牌 | 已采纳 |
