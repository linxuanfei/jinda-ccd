---
feature: ui-theme
status: delivered
updated: 2026-11-15
source: feat/ui-theme
---

# UI 主题与交互现代化（暗色科幻 HUD）

## Report

**What was built** — 自研 QSS 设计令牌层（`visionapp/ui/theme.py`：17 色令牌 + `build_qss()`/`apply()`/`set_state()`/`set_class()`/`tune_layout()`）；全界面清除内联样式（main_window/station_tile/5 个对话框/image_view/demo_gui）；判定与整线状态药丸（ok/ng/warn/idle 四态）、角色/标定语义色；工具条 3 组分区（配方/配置/运行）+ 开始/停止主次造型 + F5/F6/F7 快捷键；机位卡状态 LED + 选中 accent 描边 + 统一 28px 头部；对话框统一 12/8 刻度与错误色；数值等宽排版（统计面板/机位头/结果表/lbl_calib/lbl_merged）。自测与业务属性名（lbl_verdict 等）零变更。

**Verification** — selftest 7 项全 `RESULT: PASS`（offscreen）；像素探针：机位卡选中描边 (34,211,238)、通用状态色 ok=(47,214,123)/ng=(255,93,93)、1920px 工具条 sizeHint=1701 全动作直接可见、1366px 溢出按钮可见——全部 PASS。独立评审两轮：初审 REQUEST_CHANGES（6 critical：描边渲染/工具条溢出/对话框刻度/错误色/通用状态规则/等宽覆盖面）→ 整改（53c7012）→ 复审 APPROVE。

## [S1] Problem

产线视觉系统要尽可能把有用信息全显示在一屏，这个密度不能降；但当前 UI 是照着 20 年前的
In-Vision 交互概念堆出来的，且无产品设计：

- **无设计系统**：样式全是散落的内联 `setStyleSheet` 硬编码色（`#212121`、`#666`、`#c62828`
  十几处），颜色、字号、间距各自为政，观感不统一；
- **信息层级弱**：工具条 20+ 个同权按钮一字排开；判定（OK/NG）只是小号彩色文字，远观不可读；
  统计数值与标签无排版层级，数字字体非等宽会跳动；
- **控件状态缺失**：按钮无 hover/focus/disabled 视觉语言，机位卡状态不一眼可辨。

约束：前端技术保持 **PySide6** 不换库；一屏高信息密度不丢。目标是现代感甚至科幻感，
且产线强光环境下仍可读、不闪烁（光敏/疲劳安全）。

## [S2] Design

### S2.1 样式路线调研与决策

| 路线 | 许可 | 统一速度 | 科幻定制上限 | 依赖 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **自研 QSS 设计令牌层** | 自有 | 中 | **高**（完全可控） | 无 | **采纳** |
| QDarkStyleSheet 打底 | MIT | 快 | 中（底库风格掣肘） | +qdarkstyle | 备选 |
| qt-material | MIT | 快 | 低（Material 风，不科幻） | +qt-material | 不采纳 |
| QFluentWidgets | **GPLv3 / 商用需买授权** | 快 | 中（Fluent 风） | +qfluentwidgets | **不采纳**（与 ADR-001 禁 GPL/AGPL 冲突） |

已核实（2026-11-15）：QDarkStyleSheet=MIT（图像资源 CC-BY 4.0）；
PyQt-Fluent-Widgets 明示"GPLv3 for non-commercial；commercial use purchase a commercial license"。

**决策（已与用户确认）**：暗色科幻 HUD + 自研 QSS 令牌层，零新依赖。
理由：科幻观感只能靠自定义调色板与造型达成；令牌化后浅色主题可后续低成本补上；
无许可风险（延续 ADR-001）。

### S2.2 设计令牌（Token）

色板（深空暗色，3 个色族：中性底 / 青蓝主色 / 状态色）：

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `bg.base` | `#0B0E14` | 窗口底色 |
| `bg.panel` | `#121A24` | 面板/分组 |
| `bg.rise` | `#18222E` | 输入框/悬浮面 |
| `border.subtle` | `#22303C` | 1px 分隔线/描边 |
| `border.focus` | `#22D3EE` | 焦点/选中描边 |
| `text.primary` | `#E6EDF5` | 主文字 |
| `text.secondary` | `#93A4B8` | 次级/标签 |
| `text.muted` | `#5E7186` | 弱化 |
| `accent.cyan` | `#22D3EE` | 主强调（运行/选中/当前机位） |
| `accent.cyan.dim` | `#0E3A47` | 强调底色 |
| `state.ok` | `#2FD67B` | OK |
| `state.ng` | `#FF5D5D` | NG |
| `state.warn` | `#FFB454` | WT/警告/工程师态 |
| `state.idle` | `#5E7186` | 待机 |

排版：

- UI 字体栈：`"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`；基准 12–13px。
- 数值/计数一律等宽：`"Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace`（防跳动）。
- 字号层级：判定 24px/700 → 指标值 20px/600（等宽）→ 标签 11px/400（secondary 色）。
- 间距刻度 4 / 8 / 12 / 16；圆角 4px（按钮/输入）、6px（面板/药丸）。
- 深度语言：不用阴影，用 1px `border.subtle` + 面板底色层级 + 顶部高光渐变（QSS `qlineargradient`）。

### S2.3 `visionapp/ui/theme.py` 契约

```python
# 令牌读取
COLORS: dict[str, str]        # S2.2 全量键，点分命名
def color(name: str) -> str   # 取色；未知键抛 KeyError
def ui_font(px: int, weight: ...) -> QFont
def mono_font(px: int, ...) -> QFont

# 样式表
def build_qss() -> str        # 由令牌生成全局 QSS（单次生成，纯函数）
def apply(app: QApplication)  # app.setStyleSheet(build_qss()) + 全局字体

# 语义状态（动态属性 + 重刷样式）
def set_state(widget, state)  # state ∈ {"ok","ng","warn","idle"} → setProperty("state", ...)
                              # 并 unpolish/polish；供 QLabel 状态药丸使用
```

全局 QSS 覆盖面（`build_qss()` 内声明）：`QMainWindow/QWidget#root`、`QToolBar` 及按钮、
`QPushButton`（普通/primary/destructive 三档，用动态属性 `variant`）、`QComboBox`、`QGroupBox`、
`QListWidget/QTreeView/QTableView`（含选中/交替行）、`QLineEdit/QSpinBox/QDoubleSpinBox`、
`QCheckBox/QRadioButton`、`QScrollBar`、`QSplitter` handle、`QStatusBar`、`QMenu/QToolTip`、
`QDialog` 基础、`QLabel[state="..."]` 状态药丸。

**UI 代码约定（本次起执行）**：业务代码禁止写入十六进制色值/字号/内联 `setStyleSheet`；
动态状态一律走 `theme.set_state` / `setStyleSheet(theme.qss_snippet(...))` 由 theme 模块生成。
（历史遗留内联样式在本次全部清除。）

### S2.4 语义控件改造契约

| 区域 | 改造 | 不变 |
| --- | --- | --- |
| 工具条 | 分 3 组：**配方**（新建/打开/保存/另存/导入.HPro）→ **配置**（机位/合并/管理/相机/记录/输出/产线/权限/标定）→ **运行**（模式/开始/停止/单帧/清空）；组间分隔线可辨识；「开始」= primary 青色实心，「停止」= destructive 描边红；开始/停止/单帧 tooltip 带快捷键（F5/F6/F7）。按钮紧凑（padding 4×7px、12px 字号），1920 宽内全部直接可见；更窄屏溢出按钮须可见可点 | 动作的 slot 接线、`_edit_actions` 权限禁用机制 |
| 判定横幅 | `lbl_verdict` / `lbl_merged` 改为状态药丸（背景 `state.*` 对应 dim 底 + 同色描边/文字，24px）；四态：待机(idle)/OK/NG/WT(warn) | 文案、更新时机（`_poll`） |
| 统计面板 | 指标值等宽 20px；标签 11px secondary；`lbl_calib` 用 accent.cyan；分组框统一间距 | 字段与布局行列 |
| 机位卡 | `station_tile`：头部高度统一 28px；左上状态 LED 圆点（8px，`state.*`）；当前机位卡片 accent 描边 2px；选中/未选底色区分 | 点击切换机位行为、网格布局 |
| 对话框 | `dialogs.py / line_dialog.py / recipe_manager.py / security_dialog.py`：统一 contentMargins 12 / spacing 8、按钮盒右对齐、错误文字 `state.ng`、提示文字 secondary；全部去内联样式 | 字段与逻辑 |
| 数值排版 | 计数/耗时/FPS/毫米值一律等宽（`mono`/`statValue`）：统计面板、机位头、结果表值列/判定列、`lbl_calib`、`lbl_merged` 内计数；单位以标签承载（统计面板单位即 statKey caption） | 取值来源 |

快捷键（新增并在 tooltip 标注）：`F5` 开始、`F6` 停止、`F7` 单帧（与现有动作绑定，不改语义）。

**禁用闪烁/动画**：状态变化只换静态色阶，不闪不抖（产线光敏与疲劳安全）；"科幻感"
来自色板、等宽数据、细描边与高对比状态灯，不来自动效。

### S2.5 接线与入口

- `tools/visionapp_gui.py`、`tools/launch_gui.py`、`tools/demo_gui.py` 创建 `QApplication`
  后调用 `theme.apply(app)`（offscreen 自测同样生效，不弹窗）。
- 入口不改命令行参数与启动语义。

## [S3] Out of Scope

- 布局大重构（ribbon/dock/多窗口/信息重排）——留二期；
- 浅色主题 / 主题切换（令牌已为此留好，本次只落暗色）；
- 动效、粒子、自绘图表、音效；
- 图标素材库（一期用文字符号与 Qt 标准图标，不引入图标包）；
- i18n、Qt Quick/QML、任何第三方样式库。

## Tasks

- [x] T1: 新增 `visionapp/ui/theme.py`（令牌 + `build_qss()` + `apply` + `set_state`） — acceptance: 纯函数生成 QSS；`apply` 后 offscreen 下新建 QApplication 无异常；模块内无业务逻辑 (covers: S2.2, S2.3)
- [x] T2: 三个入口接线 `theme.apply(app)` — acceptance: `visionapp_gui.py`/`launch_gui.py`/`demo_gui.py` 均生效；`QT_QPA_PLATFORM=offscreen` 下 selftest 起动不报错 (covers: S2.5)
- [x] T3: `main_window.py` 去内联样式 + 判定/整线/角色/标定状态药丸 — acceptance: 文件内无十六进制色值与内联 `setStyleSheet`（`set_state`/theme 生成除外）；`lbl_verdict`/`lbl_merged` 随 OK/NG/待机 变色（covers: S2.4）
- [x] T4: 工具条分组 + 运行按钮档位 + 快捷键 tooltip（F5/F6/F7） — acceptance: 分组可辨识；开始/停止有 primary/destructive 造型；tooltip 含快捷键；动作顺序与槽接线不变 (covers: S2.4)
- [x] T5: `station_tile.py` 状态 LED + 选中态 — acceptance: 每卡头部统一高度与 LED；选中卡 accent 描边；切换机位行为不变 (covers: S2.4)
- [x] T6: 对话框统一（dialogs/line_dialog/recipe_manager/security_dialog） — acceptance: 四文件无内联色值；间距/按钮盒按 S2.4；错误文案 state.ng (covers: S2.4)
- [x] T7: 数值等宽排版 + 统计字号层级 — acceptance: 统计面板值为 mono 字体、标签 11px secondary；`lbl_calib` accent 色 (covers: S2.4)
- [x] T8: 全量 selftest 回归（offscreen） — acceptance: `selftest_runner/multistation/calibration/outputs/ui/ui_calib/p1` 全部 `RESULT: PASS` (covers: S2.3)

## Journey log

- **QWidget 子类的 QSS border 默认不绘制**（background 可以）：需 `WA_StyledBackground=True` 或改继承 QFrame。机位卡选中描边曾因此完全不渲染（评审 C1，像素实证）；自绘面板类控件通用陷阱。
- **QSS 按钮 padding 会显著放大 QToolBar sizeHint**：5×12px padding 令 22 个文本动作宽至 2007px，1920 屏下运行组被挤进不可见的溢出菜单（评审 C6）。动作多的工具条必须用紧凑 padding + 可见的 `qt_toolbar_ext_button` 样式。
- **`set_state` 只对有匹配 QSS 规则的控件生效**：无 objectName 的 QLabel 仅设动态属性是视觉 no-op，故补了通用 `QLabel[state]` 色规则（评审 C5，demo_gui 状态色回归教训）。
- 「单位后缀 secondary 小字」全量微排版（值+单位混排富文本）成本高、收益低，一期以 statKey 标签承载单位；如需再立项。
- 残留（非阻塞）：QSS `font-family` 的 `mono` 栈在 macOS 缺字体时退化为比例字体（无 StyleHint），`mono_font()` 助手才带 StyleHint.Monospace；生产机 Windows 有 Consolas 不受影响，后续可给栈补 Menlo 等回退。
