"""P1 收尾自测：机位独立输出/报警、权限、产线配置对话框。"""

from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visionapp.qtcompat import QApplication            # noqa: E402

import visionapp.alarm as alarm_mod                    # noqa: E402
from visionapp.security import SecurityConfig, hash_password   # noqa: E402
from visionapp.settings import AppSettings             # noqa: E402
from visionapp.stations import LineConfig, StationConfig, StationManager  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out", "selftest_p1")


def main() -> int:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    fails: list[str] = []
    settings = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))

    def getter(key, default=None):
        return {"recorder_cfg": settings.recorder_cfg,
                "output_cfg": settings.output_cfg,
                "alarm_cfg": settings.alarm_cfg}.get(key, default)

    # ---------------- 1) 机位独立输出 ----------------
    beeps: list[int] = []
    real_beep = alarm_mod._default_beep
    alarm_mod._default_beep = lambda p: beeps.append(1)
    try:
        line = LineConfig(name="测试线", merge_rule="all_ok", stations=[
            StationConfig(id=1, name="A", output={"enabled": True, "template": "{verdict},{frame}\\r\\n",
                                                  "channel": {"kind": "file", "path": os.path.join(OUT, "A.log")}},
                          alarm={"enabled": True, "beep_on_ng": True, "min_interval_s": 0.0},
                          recipe="recipes/sim_demo.json"),
            StationConfig(id=2, name="B", output={"enabled": True, "template": "{verdict}\\r\\n",
                                                  "channel": {"kind": "file", "path": os.path.join(OUT, "B.log")}},
                          recipe="recipes/sim_demo.json"),
            StationConfig(id=3, name="C", output=None, recipe="recipes/sim_demo.json"),
        ])
        mgr = StationManager(line, BASE, getter)
        mgr.open_all()
        for st in mgr.stations:
            st.open_recorder()
            st.open_outputs()
            st.refresh_alarm()
            st.runner.step()
            st.runner.step()

        a_log = os.path.join(OUT, "A.log")
        b_log = os.path.join(OUT, "B.log")
        a_txt = open(a_log, encoding="utf-8").read() if os.path.exists(a_log) else ""
        b_txt = open(b_log, encoding="utf-8").read() if os.path.exists(b_log) else ""
        print(f"[1] 机位A输出={a_txt!r}")
        print(f"    机位B输出={b_txt!r}")
        if "OK" not in a_txt or len(a_txt.strip().splitlines()) != 2:
            fails.append(f"机位A 独占输出不正确: {a_txt!r}")
        if "OK" not in b_txt or len(b_txt.strip().splitlines()) != 2:
            fails.append(f"机位B 独占输出不正确: {b_txt!r}")

        # 报警只应来自 A（B/C 用全局，全局默认关闭）；OK 帧本就不该响
        print(f"[2] 报警次数={len(beeps)}（OK 帧不该报警）")
        if len(beeps) != 0:
            fails.append(f"OK 帧不应报警: {len(beeps)}")

        # 人为让 A 出 NG，再验证报警
        mgr.stations[0].recipe.items[1].judge = {"kind": "range", "min": 100, "max": 200}
        before = len(beeps)
        mgr.stations[0].runner.step()
        print(f"[3] A 出 NG 后报警: {len(beeps) - before} 次")
        if len(beeps) - before != 1:
            fails.append("NG 时未触发机位报警")
        mgr.close_all()
    finally:
        alarm_mod._default_beep = real_beep

    # ---------------- 4) 权限 ----------------
    sec = SecurityConfig()
    sec.set_password("8888")
    print(f"[4] 权限: 密码正确={sec.check('8888')} 错误={sec.check('1234')} 需要登录={sec.require_login}")
    if not sec.check("8888") or sec.check("1234") or not sec.require_login:
        fails.append("密码校验不正确")
    if sec.engineer_password_hash == "8888":
        fails.append("密码必须存摘要而不是明文")
    if sec.engineer_password_hash != hash_password("8888"):
        fails.append("摘要不匹配")
    sec.clear_password()
    if sec.require_login or sec.has_password:
        fails.append("清除密码失败")

    # ---------------- 5) 界面权限门禁 ----------------
    from visionapp.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication(sys.argv)
    s2 = AppSettings.load(os.path.join(BASE, "config", "app_settings.json"))
    s2.set("security", {"require_login": True, "engineer_password_hash": hash_password("8888")})
    line2 = LineConfig.load(os.path.join(BASE, "config", "line.json"))
    win = MainWindow(line=line2, settings=s2)
    win.timer.stop()
    print(f"[5] 启动身份={win.role} 编辑面板可用={win.editor_panel.isEnabled()}")
    if win.role != "operator" or win.editor_panel.isEnabled():
        fails.append("未登录时应为操作员且编辑面板禁用")
    win.role = "engineer"
    win._apply_role()
    if not win.editor_panel.isEnabled():
        fails.append("工程师模式编辑面板应可用")

    # ---------------- 6) 产线配置对话框 ----------------
    from visionapp.ui.line_dialog import LineConfigDialog
    dlg = LineConfigDialog(line2, os.path.join(BASE, "recipes"),
                           s2.output_cfg, s2.alarm_cfg)
    n0 = len(dlg.line.stations)
    dlg._add_station()
    dlg.ed_name.setText("新机位X")
    dlg.ed_line_name.setText("改名产线")
    dlg._accept()
    print(f"[6] 产线配置: 机位数 {n0} -> {len(dlg.line.stations)}，名称={dlg.line.name}，"
          f"末机位={dlg.line.stations[-1].display_name()}")
    if len(dlg.line.stations) != n0 + 1:
        fails.append("添加机位失败")
    if dlg.line.name != "改名产线" or dlg.line.stations[-1].name != "新机位X":
        fails.append("对话框编辑未写回")

    tmp = os.path.join(tempfile.gettempdir(), "visionapp_line_test.json")
    dlg.line.save(tmp)
    back = LineConfig.load(tmp)
    if len(back.stations) != len(dlg.line.stations) or back.name != dlg.line.name:
        fails.append("产线配置保存/重载不一致")

    # ---------------- 7) 应用到界面 ----------------
    win.apply_line(back)
    print(f"[7] 应用产线: 画面块={len(win.tiles)} 机位下拉={win.cb_station.count()}")
    if len(win.tiles) != len(back.stations):
        fails.append("应用产线后网格数量不对")
    win.manager.close_all()
    win.close()
    dlg.deleteLater()
    win.deleteLater()
    app.processEvents()

    print()
    if fails:
        for f in fails:
            print("  [FAIL]", f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
