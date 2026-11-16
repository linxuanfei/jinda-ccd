"""相机/帧源选择 与 记录设置 对话框。"""

from __future__ import annotations

import os

from ..qtcompat import Qt
from ..qtcompat import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout,
)

from ..camera_cfg import BRAND_MINDVISION, MODEL_MV_GE500M_TPO
from ..mindvision import SERIAL
from ..recorder import RecorderConfig
from ..sources import source_from_config
from . import theme

SOURCE_KINDS = [
    ("synthetic", "模拟相机（合成画面，开发用）"),
    ("folder", "图片回放（目录里的图片）"),
    ("uvc", "USB 摄像头 / UVC（MaixCam UVC 模式）"),
    ("rtsp", "网络相机 RTSP（MaixCam 推流）"),
    ("mindvision", "迈德威视 MV-GE500M-TPO（GigE，官方 MVSDK）"),
]


class SourceDialog(QDialog):
    """选择帧源并连接。返回 config: {kind, path, index, url}。"""

    def __init__(self, config: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择相机 / 帧源")
        self.resize(560, 300)
        cfg = dict(config or {})

        v = theme.tune_layout(QVBoxLayout(self))
        form = QFormLayout()

        self.cb_kind = QComboBox()
        for kind, label in SOURCE_KINDS:
            self.cb_kind.addItem(label, kind)
        idx = max(0, self.cb_kind.findData(cfg.get("kind", "synthetic")))
        self.cb_kind.setCurrentIndex(idx)
        form.addRow("类型", self.cb_kind)

        # folder
        self.ed_path = QLineEdit(cfg.get("path", ""))
        btn_path = QPushButton("...")
        btn_path.setFixedWidth(32)
        btn_path.clicked.connect(self._pick_dir)
        row_path = QHBoxLayout()
        row_path.addWidget(self.ed_path, 1)
        row_path.addWidget(btn_path)
        form.addRow("图片目录", row_path)

        # uvc
        self.sp_index = QSpinBox()
        self.sp_index.setRange(0, 9)
        self.sp_index.setValue(int(cfg.get("index", 0) or 0))
        form.addRow("摄像头序号", self.sp_index)

        # rtsp
        self.ed_url = QLineEdit(cfg.get("url", "rtsp://10.239.136.1:8554/live"))
        form.addRow("RTSP 地址", self.ed_url)

        self.ed_model = QLineEdit(cfg.get("model") or MODEL_MV_GE500M_TPO)
        self.ed_serial = QLineEdit(cfg.get("serial") or SERIAL)
        self.ed_sdk = QLineEdit(cfg.get("sdk_path") or "")
        form.addRow("迈德威视型号", self.ed_model)
        form.addRow("序列号", self.ed_serial)
        form.addRow("SDK 路径", self.ed_sdk)

        lbl_hint = QLabel(
            "迈德威视走官方 MVSDK。库不在或打不开会报错，不会用合成图或 UVC 代替。"
            "分辨率和曝光范围待手册确认。"
        )
        theme.set_class(lbl_hint, "hint")
        form.addRow(lbl_hint)
        v.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("连接")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.result_cfg: dict = {}
        self.cb_kind.currentIndexChanged.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        kind = self.cb_kind.currentData()
        self.ed_path.setEnabled(kind == "folder")
        self.sp_index.setEnabled(kind == "uvc")
        self.ed_url.setEnabled(kind == "rtsp")
        gige = kind == "mindvision"
        self.ed_model.setEnabled(gige)
        self.ed_serial.setEnabled(gige)
        self.ed_sdk.setEnabled(gige)

    def _pick_dir(self) -> None:
        start = self.ed_path.text() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "选择图片目录", start)
        if d:
            self.ed_path.setText(d)

    def _accept(self) -> None:
        kind = self.cb_kind.currentData()
        cfg = {"kind": kind, "path": self.ed_path.text().strip(),
               "index": int(self.sp_index.value()), "url": self.ed_url.text().strip()}
        if kind == "mindvision":
            cfg.update({
                "brand": BRAND_MINDVISION,
                "model": self.ed_model.text().strip() or MODEL_MV_GE500M_TPO,
                "serial": self.ed_serial.text().strip() or SERIAL,
                "sdk_path": self.ed_sdk.text().strip(),
                "trigger": "hardware",
            })
        try:
            src = build_source(cfg)
            src.open()
            info = src.info
            src.close()
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, "连接失败", f"{exc}")
            return
        self.result_cfg = cfg
        QMessageBox.information(self, "连接成功",
                                f"{info.name}  {info.width}x{info.height}  fps={info.fps}")
        self.accept()


def build_source(cfg: dict):
    """按配置创建帧源（不打开）。"""
    return source_from_config(cfg)


class RecordSettingsDialog(QDialog):
    """结果记录设置。"""

    def __init__(self, cfg: RecorderConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("记录设置")
        self.resize(520, 240)
        v = theme.tune_layout(QVBoxLayout(self))
        form = QFormLayout()

        self.ed_root = QLineEdit(cfg.root)
        btn = QPushButton("...")
        btn.setFixedWidth(32)
        btn.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.ed_root, 1)
        row.addWidget(btn)
        form.addRow("结果目录", row)

        self.ck_csv = QCheckBox("写 CSV")
        self.ck_csv.setChecked(cfg.csv_enabled)
        form.addRow("", self.ck_csv)

        self.ck_ng = QCheckBox("保存 NG 图像")
        self.ck_ng.setChecked(cfg.save_ng_images)
        form.addRow("", self.ck_ng)

        self.ck_ok = QCheckBox("保存 OK 图像（占空间大）")
        self.ck_ok.setChecked(cfg.save_ok_images)
        form.addRow("", self.ck_ok)

        v.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择结果目录", self.ed_root.text())
        if d:
            self.ed_root.setText(d)

    def result_cfg(self) -> RecorderConfig:
        return RecorderConfig(
            root=self.ed_root.text().strip() or "results",
            csv_enabled=self.ck_csv.isChecked(),
            save_ng_images=self.ck_ng.isChecked(),
            save_ok_images=self.ck_ok.isChecked(),
        )


class OutputAlarmDialog(QDialog):
    """串口/TCP 输出 与 NG 报警 设置。"""

    CHANNELS = [("file", "文件（联调用）"), ("serial", "串口"), ("tcp", "TCP")]

    def __init__(self, output_cfg: dict, alarm_cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("输出与报警设置")
        self.resize(640, 520)
        out = dict(output_cfg or {})
        alarm = dict(alarm_cfg or {})
        v = theme.tune_layout(QVBoxLayout(self))

        # ---------------- 输出 ----------------
        box = QGroupBox("结果输出（串口 / TCP / 文件）")
        f = QFormLayout(box)

        self.ck_out = QCheckBox("启用输出")
        self.ck_out.setChecked(bool(out.get("enabled", False)))
        f.addRow("", self.ck_out)

        self.cb_kind = QComboBox()
        for kind, label in self.CHANNELS:
            self.cb_kind.addItem(label, kind)
        self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(out.get("channel", {}).get("kind", "file"))))
        f.addRow("通道", self.cb_kind)

        self.ed_port = QLineEdit(out.get("channel", {}).get("port", "COM3"))
        self.sp_baud = QSpinBox()
        self.sp_baud.setRange(1200, 921600)
        self.sp_baud.setValue(int(out.get("channel", {}).get("baud", 115200)))
        self.ed_host = QLineEdit(out.get("channel", {}).get("host", "127.0.0.1"))
        self.sp_tcp = QSpinBox()
        self.sp_tcp.setRange(1, 65535)
        self.sp_tcp.setValue(int(out.get("channel", {}).get("tcp_port", 5000)))
        self.ed_path = QLineEdit(out.get("channel", {}).get("path", "results/output.log"))

        row_s = QHBoxLayout()
        row_s.addWidget(QLabel("串口"))
        row_s.addWidget(self.ed_port, 1)
        row_s.addWidget(QLabel("波特率"))
        row_s.addWidget(self.sp_baud)
        f.addRow("串口参数", row_s)

        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("主机"))
        row_t.addWidget(self.ed_host, 1)
        row_t.addWidget(QLabel("端口"))
        row_t.addWidget(self.sp_tcp)
        f.addRow("TCP", row_t)

        f.addRow("文件", self.ed_path)

        self.ed_tpl = QLineEdit(out.get("template", "{time},{verdict},{frame}\\r\\n"))
        self.ed_tpl.setToolTip("可用占位符：{time} {frame} {verdict} {result} {elapsed} 以及各检测项 id")
        f.addRow("协议模板", self.ed_tpl)

        row_flags = QHBoxLayout()
        self.ck_ok = QCheckBox("OK 也发送")
        self.ck_ok.setChecked(bool(out.get("on_ok", True)))
        self.ck_ng = QCheckBox("NG 发送")
        self.ck_ng.setChecked(bool(out.get("on_ng", True)))
        btn_test = QPushButton("测试发送")
        btn_test.clicked.connect(self._test_send)
        row_flags.addWidget(self.ck_ok)
        row_flags.addWidget(self.ck_ng)
        row_flags.addWidget(btn_test)
        f.addRow("", row_flags)

        self.lbl_test = QLabel("")
        theme.set_state(self.lbl_test, "idle")
        f.addRow("", self.lbl_test)
        v.addWidget(box)

        # ---------------- 报警 ----------------
        box2 = QGroupBox("NG 报警")
        f2 = QFormLayout(box2)
        self.ck_alarm = QCheckBox("启用 NG 报警")
        self.ck_alarm.setChecked(bool(alarm.get("enabled", True)))
        f2.addRow("", self.ck_alarm)
        self.ck_beep = QCheckBox("响铃")
        self.ck_beep.setChecked(bool(alarm.get("beep_on_ng", True)))
        f2.addRow("", self.ck_beep)
        row_sp = QHBoxLayout()
        self.ed_sound = QLineEdit(alarm.get("sound_path", ""))
        btn_s = QPushButton("...")
        btn_s.setFixedWidth(32)
        btn_s.clicked.connect(self._pick_sound)
        row_sp.addWidget(self.ed_sound, 1)
        row_sp.addWidget(btn_s)
        f2.addRow("声音文件", row_sp)
        self.sp_interval = QDoubleSpinBox()
        self.sp_interval.setRange(0.0, 60.0)
        self.sp_interval.setDecimals(1)
        self.sp_interval.setValue(float(alarm.get("min_interval_s", 1.0)))
        f2.addRow("最小间隔(秒)", self.sp_interval)
        v.addWidget(box2)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ------------------------------------------------------------------ 内部
    def _pick_sound(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "选择报警声音", self.ed_sound.text(), "WAV (*.wav)")
        if p:
            self.ed_sound.setText(p)

    def _channel_cfg(self) -> dict:
        return {
            "kind": self.cb_kind.currentData(),
            "port": self.ed_port.text().strip(),
            "baud": int(self.sp_baud.value()),
            "host": self.ed_host.text().strip(),
            "tcp_port": int(self.sp_tcp.value()),
            "path": self.ed_path.text().strip(),
        }

    def _test_send(self) -> None:
        from ..outputs import OutputManager, format_payload  # noqa: PLC0415
        from ..recipe import PipelineResult                  # noqa: PLC0415
        from ..ops import ItemResult                         # noqa: PLC0415

        cfg = self.result_output_cfg()
        cfg.enabled = True
        mgr = OutputManager(cfg)
        try:
            mgr.open()
            demo = PipelineResult(frame_index=0,
                                  results={"test": ItemResult("test", "test", value=1.0)},
                                  ok=True, elapsed_ms=1.0)
            mgr.send(demo)
            self.lbl_test.setText(f"已发送（{len(format_payload(cfg, demo))} 字节）")
            theme.set_state(self.lbl_test, "ok")
        except Exception as exc:                       # noqa: BLE001
            self.lbl_test.setText(f"发送失败：{exc}")
            theme.set_state(self.lbl_test, "ng")
        finally:
            mgr.close()

    # ------------------------------------------------------------------ 结果
    def result_output_cfg(self) -> dict:
        return {
            "enabled": self.ck_out.isChecked(),
            "channel": self._channel_cfg(),
            "template": self.ed_tpl.text(),
            "encoding": "gbk",
            "on_ok": self.ck_ok.isChecked(),
            "on_ng": self.ck_ng.isChecked(),
            "ok_text": "OK",
            "ng_text": "NG",
        }

    def result_alarm_cfg(self) -> dict:
        return {
            "enabled": self.ck_alarm.isChecked(),
            "beep_on_ng": self.ck_beep.isChecked(),
            "sound_path": self.ed_sound.text().strip(),
            "min_interval_s": float(self.sp_interval.value()),
        }


class CalibrationDialog(QDialog):
    """直接填 X/Y，或用真实值对实测值反算（设定 1 / 实测 1.433）。"""

    def __init__(self, calibration, parent=None):
        super().__init__(parent)
        from ..calibration import Calibration

        self.setWindowTitle("相机标定（X 水平 / Y 垂直）")
        self.resize(520, 430)
        self.result_cal = None
        self.cal = Calibration.from_dict(calibration.to_dict() if calibration else None)

        v = theme.tune_layout(QVBoxLayout(self))
        lbl_now = QLabel("当前：" + self.cal.describe())
        theme.set_class(lbl_now, "hint")
        v.addWidget(lbl_now)

        box1 = QGroupBox("① 直接填比例（mm / 像素）")
        f1 = QFormLayout(box1)
        self.sp_x = QDoubleSpinBox()
        self.sp_y = QDoubleSpinBox()
        for sp in (self.sp_x, self.sp_y):
            sp.setDecimals(6)
            sp.setRange(0.0, 1000.0)
            sp.setSingleStep(0.001)
        self.sp_x.setValue(self.cal.sx)
        self.sp_y.setValue(self.cal.sy)
        f1.addRow("X 水平（mm/px）", self.sp_x)
        f1.addRow("Y 垂直（mm/px）", self.sp_y)
        btn_xy = QPushButton("应用这两个比例")
        btn_xy.clicked.connect(self._apply_xy)
        f1.addRow(btn_xy)
        v.addWidget(box1)

        box2 = QGroupBox("② 用实测值反算")
        f2 = QFormLayout(box2)
        self.cb_axis = QComboBox()
        self.cb_axis.addItem("X 水平", "x")
        self.cb_axis.addItem("Y 垂直", "y")
        self.sp_known = QDoubleSpinBox()
        self.sp_known.setDecimals(4)
        self.sp_known.setRange(0.0, 1e6)
        self.sp_known.setValue(1.0)
        self.sp_measured = QDoubleSpinBox()
        self.sp_measured.setDecimals(4)
        self.sp_measured.setRange(0.0, 1e6)
        self.sp_measured.setValue(1.433)
        f2.addRow("方向", self.cb_axis)
        f2.addRow("真实/设定值（mm）", self.sp_known)
        f2.addRow("当前实测值（mm）", self.sp_measured)
        btn_fix = QPushButton("计算并应用")
        btn_fix.clicked.connect(self._apply_reverse)
        f2.addRow(btn_fix)
        v.addWidget(box2)

        self.lbl_preview = QLabel("")
        theme.set_class(self.lbl_preview, "hint")
        v.addWidget(self.lbl_preview)
        v.addStretch(1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("保存到配方")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _apply_xy(self) -> None:
        try:
            self.cal.set_xy(self.sp_x.value(), self.sp_y.value(), method="manual")
        except ValueError as exc:
            QMessageBox.warning(self, "标定", str(exc))
            return
        self.lbl_preview.setText(f"已设置 → {self.cal.describe()}")

    def _apply_reverse(self) -> None:
        try:
            k = self.cal.set_by_known_value(
                self.sp_known.value(), self.sp_measured.value(), axis=self.cb_axis.currentData())
        except ValueError as exc:
            QMessageBox.warning(self, "标定", str(exc))
            return
        self.sp_x.setValue(self.cal.sx)
        self.sp_y.setValue(self.cal.sy)
        self.lbl_preview.setText(f"比例 × {k:.4f} → {self.cal.describe()}")

    def _accept(self) -> None:
        if not self.cal.valid:
            QMessageBox.warning(self, "标定", "比例都是 0，等于没标定")
            return
        self.result_cal = self.cal
        self.accept()

