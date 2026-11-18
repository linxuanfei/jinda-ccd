# 与 grok 版本（jinda-ccd @ 3d76825）的差异对照

> 对方包：`jinda-ccd-3d7682596707d49a50d10224b20645a7ba49751d.zip`（77 文件，4.0 MB，2026-11-16 17:50 打包）
> 我方包：`visionapp-src-2026-11-16.zip`（111 文件，10.6 MB）
> 结论：**同一棵树长出来的两个分支**（21 个文件字节级相同），他们主要补了"真实相机 + 调机语义"，我们主要补了"能交付 + 能上产线"。

---

## 1. 血缘：21 个文件完全一致

`config/app_settings.json`、`config/line.json`、`recipes/sim_demo.json`、`recipes/2pin_imported.json`、
`setup.md`、`requirements.txt`、`tools/hpro_import.py`、`tools/hpro_inspect.py`、`tools/launch_gui.py`、
`tools/selftest_calibration.py`、`tools/selftest_outputs.py`、`tools/selftest_runner.py`、
`visionapp/__init__.py`、`alarm.py`、`hpro.py`、`outputs.py`、`settings.py`、`stations.py`、`ui/__init__.py`、
`docs/项目计划.md`、`docs/compose/spec/ui-theme.md`

→ 他们拿的是我方早期快照（在我做"设计值±容差"之后、"跟踪/线 ROI/多选/打包"之前的版本）。

## 2. 他们多出来的（值得吸收）

| # | 内容 | 价值 |
| --- | --- | --- |
| 1 | **`visionapp/mindvision.py`（506 行）**：迈德威视 **官方 C 接口**（`MVCAMSDK_X64.dll`）适配 | ★★★ 这是最大增量：`CameraEnumerateDevice/CameraInit/CameraPlay/CameraGetImageBuffer/CameraImageProcess`，双后端（官方 `mvsdk.py` 或裸 ctypes），曝光**微秒**、触发 0 连续/1 软触发/2 硬件触发、`CameraSetAnalogGain`、`CameraSetFrameRate`、ISP 输出格式 |
| 2 | **锁定铭牌型号**：`MV-GE500M-TPO` / SN `058071510393` / GigE | ★★★ 相机型号终于确定（**网口 500 万**），不是我们之前猜的 USB |
| 3 | **`visionapp/tuning.py`（173 行）**：极性取点、阈值拦截、双边容差、对齐、水平线的**共享纯函数** | ★★ 界面/算子/跟踪共用一套几何，避免两处算错 |
| 4 | **6 种极性命名**：黑色底部 / 白色底部 / 白色中下 / 黑色中下 / 白色中上 / **白色左侧** | ★★ 比我们 5 区位多了"左侧"，且直接叫现场话术 |
| 5 | **规则预览文本**：`间距 设计 1 ± 0.08 → [0.920, 1.080]` | ★★ 现场对表方便 |
| 6 | **结果表列**：对象 / 基准 / 设计值 / 实测 / 面积 / NG次数 / 判定 | ★★ 对齐原厂界面 |
| 7 | **三级权限**：生产（免登录）/ 管理员 / 超级管理员（两份 SHA-256 摘要，默认空＝登录失败） | ★ 与现场"改框要登录"一致 |
| 8 | 文档 `docs/迈德威视相机.md`（安装/API/触发/单位）、`docs/调机迁移.md`（迁移决策+验收口径） | ★ 相机这块写得很细，可直接用 |
| 9 | `tools/selftest_setup.py`（273 行）：双边容差/分轴标定/跟踪平移/对齐/阈值拦截/权限 | ★ 思路可借 |

## 3. 我们多出来的（他们没有）

| # | 内容 | 影响 |
| --- | --- | --- |
| 1 | **Win7 兼容（`qtcompat.py` + PySide2/Qt5）** + Python 3.8 工具链 | ★★★ 老机器直接能跑 |
| 2 | **两套 PyInstaller 打包**（spec + build 脚本 + frozen 日志 + `paths.py` 便携路径） | ★★★ 现场要的是 exe，不是源码 |
| 3 | **`imgio.py` 中文路径修复**（imencode/tofile） | ★★★ 他们仍直接 `cv2.imwrite`，中文用户名机器上 **NG 图存不下来** |
| 4 | **线形 ROI**：任意角度 + Shift 吸附 + 端点拖拽 + 选中红 + 右键拖 + 存配方 | ★★ 他们只有"水平基准线"（写死水平向右，不能改角度/端点） |
| 5 | **多选**（Ctrl/Shift/框选）+ **7 个批量对齐** + **撤销对齐** + **连画** | ★★ 他们只有 `align_rois`（以第一个框为基准，无框选、无撤销） |
| 6 | **跟踪质量**：模板匹配 + 相似度阈值 + **失败判 NG** + `apply_to` 指定跟随对象 | ★★★ 他们"抓不到就用旧坐标继续测"，产品跑偏会**静默测错** |
| 7 | **抓取位置 = 5 区位**（top/upper/center/lower/bottom）+ `show_pick` 十字 + **红色屏蔽区蒙版** | ★ 他们有极性但屏蔽区只算了像素数，**没画在画面上** |
| 8 | **判定原生支持 `design ± tol`**（含 `abs`、对称 `tol`） | ★ 他们是在界面/导入层折算成 `range`，算子只认 range |
| 9 | 视频反读的真实参数文档（阈值 111/255、设计值 1 ± 0.08、结果表样例） | ★ 有一致性依据 |
| 10 | 部署文档 + `check_env.ps1`（老机器体检） | ★ 现场踩坑少 |

## 4. 他们代码里的具体问题（我实测/读码确认）

| # | 问题 | 证据 |
| --- | --- | --- |
| 1 | **分轴标定后 `Calibration.valid` 仍是 False** | 实测：`set_axis(100, 1.0, 'x')` → `mm_per_px_x=0.01` 但 `valid=False`；`valid` 只看 `mm_per_px`。凡是用 `valid` 判断"是否已标定"的地方都会误判 |
| 2 | **中文路径读写图失效** | `recorder.py:142` `cv2.imwrite(path, img)`、`ops.py:301`/`sources.py:82,94` `cv2.imread` 直调；他们自己机器用户名就是中文 |
| 3 | **跟踪失败不判 NG** | `tracking.apply_tracking` 返回一句话后继续用**上一次**的 ROI 位置测量 |
| 4 | **运行时状态写进配方** | `spec["taught_xy"]` / `spec["base_rois"]` 塞进 `recipe.track` 字典，存档后换产品复用会带着旧基准 |
| 5 | **极性「黑色底部」写死 +10px** | `tuning.sample_point` 里 `y + h + 10.0`，换分辨率/镜头就偏 |
| 6 | **等间距按左边缘均分** | 框宽不同时，视觉间距不等（我们按中心均分） |
| 7 | **无打包配置** | 没有 spec/build 脚本，交付是源码，现场要装 Python+PySide6 |
| 8 | **对齐以第一个框为基准** | 与"上下左右各自取组内极值"语义不同，框大小不一时结果不一样 |

> 他们 6 个自测在我们环境里**全部 PASS**（`selftest_setup/runner/p1/mindvision/multistation/ui`），说明代码是可运行、自洽的；上面第 1、2 条是他们的测试没覆盖到的缺陷。

## 5. 建议的合并方案（以我方为主干）

**保留我方**：打包（Win7/Qt5 + Qt6 两套）、中文路径、线形 ROI、多选+对齐、跟踪（模板匹配+失败判 NG）、抓取位置+屏蔽区、部署文档。

**吸收他们**（按优先级）：

1. **`mindvision.py` 的 MVSDK C 接口** → 与我们的 `sources_mindvision.py` 合并：
   - 目标库改为 `MVCAMSDK_X64.dll`（官方 C 接口），保留我们"SDK 目录自动查找 + 友好报错"的壳；
   - 相机锁定 **型号 `MV-GE500M-TPO` + SN `058071510393` + GigE**（找不到就报错，不冒充）；
   - 参数：曝光 **µs**（界面毫秒×1000）、增益原样下发、帧率 Hz、触发 0/1/2（默认 2 硬件触发）。
2. **6 极性改名**（加"白色左侧"）+ 把取点逻辑抽到共享模块（等价于我们的 `pick_zone`，但用现场话术）。
3. **规则预览文本**（`设计 1 ± 0.08 → [0.920, 1.080]`）。
4. **结果表列**（对象/基准/设计值/实测/面积/NG次数/判定）。
5. **三级权限**（生产/管理员/超级管理员，两份摘要）。

**顺带修掉**他们第 1、2、3、4、5 条问题（合进来就一起改）。

## 6. 一句话结论

- **不是重复劳动**：他们补的是"**相机能不能出图、调机语义对不对**"，我们补的是"**能不能打成 exe 装上老机器、画框/跟踪/中文路径这些现场细节**"。
- **谁也不能直接用对方的**：他们的包在老 Win7 机器上装不了（无 Qt5/无 exe），我们的包没有他们那份 MVSDK 适配（相机还是空的）。
- **最佳路径**：拿他们 `mindvision.py` 的相机层 + 型号信息，合进我们这套可交付的主干，再一起打两套包（Win10/11 + Win7）。
---

## 7. 合并记录（2026-11-16 19:0x，已完成）

以我方为主干，把他们的增量并进来了：

| # | 合并内容 | 落点 | 验证 |
| --- | --- | --- | --- |
| 1 | **迈德威视 MVSDK 经典 C 接口**（`MVCAMSDK_X64.dll`，`CameraInit/CameraGetImageBuffer/CameraImageProcess`） | `visionapp/sources_mindvision.py` 新增 `CtypesMindVision` 后端 | `selftest_mindvision.py` 用假后端跑通打开/取图链路 |
| 2 | **官方 `mvsdk.py` 绑定** | 同文件 `MvsdkModule` 后端 | 同上（按 import 分支） |
| 3 | **GenICam 风格 SDK**（我方原有实现保留为第三后端） | `GenICamBackend` | 旧适配未回归破坏 |
| 4 | **型号/序列号锁定**：`MV-GE500M-TPO` / `058071510393` / GigE | `DEFAULT_MODEL` / `DEFAULT_SERIAL` + `pick_device()` | 序列号不符、型号不符、多台同型号 → 都明确报错 |
| 5 | **曝光单位微秒**（界面毫秒 ×1000）、**触发 0/1/2**（默认 2 硬件）、增益原样下发、帧率 Hz | `MindVisionSource._push()` / `apply_tune()` | 自测断言 30ms→30000us、默认触发=2 |
| 6 | **6 种极性话术**（黑色底部/白色底部/白色中下/黑色中下/白色中上/**白色左侧**） | 新增 `visionapp/tuning.py`（`resolve_pick_zone` / `sample_point`），`find_blobs` 改用共享取点 | 中文话术与英文区位都认 |
| 7 | **规则预览文本**：`设计 1 ± 0.08 → [0.920, 1.080]` | `tuning.rule_preview_text()` + 判定区 `lbl_rule` | 界面实时刷新 |
| 8 | **阈值 0/255 提示** | `tuning.threshold_warnings()`，显示在规则预览行 | 我们仍**限幅 1~254**，但会明确告知被夹了 |
| 9 | **相机对话框参数**：序列号、型号、曝光(ms)、增益、触发 | `ui/dialogs.py` 的 `SourceDialog` | 实测回填/回传正确 |

**没有合并**（保留我方实现，理由如下）：

| 项 | 我方 | 他们 | 采用 |
| --- | --- | --- | --- |
| 跟踪 | 模板匹配 + 相似度阈值 + **失败判 NG** + `apply_to` | 阈值斑点质心，失败保持旧坐标继续测 | **我方**（安全） |
| 线形 ROI | 任意角度 + Shift 吸附 + 端点拖拽 + 存配方 | 只有水平基准线（写死右向） | 我方 |
| 多选/对齐 | Ctrl/Shift/框选 + 7 动作 + 撤销 + 连画 | `align_rois`（以第一个框为基准，无撤销） | 我方 |
| 中文路径 | `imgio`（imencode/tofile） | 直调 `cv2.imwrite` | 我方 |
| 打包/Win7 | 两套 exe + Qt5 兼容 | 无 | 我方 |
| 结果表列（对象/基准/设计值/实测/面积/NG次数） | 检测项/值/判定/说明 | 更贴近原厂 | **待办**（需要结果结构加设计值/NG 次数） |
| 三级权限（生产/管理员/超级） | 操作员/工程师 | 分两份摘要 | **待办** |

**顺带修掉的他们的缺陷**：`Calibration.valid` 分轴标定后仍 False（我方 `sx/sy` + `valid` 都覆盖）、中文路径、跟踪失败不判 NG、跟踪状态写进配方、极性写死 +10px。
