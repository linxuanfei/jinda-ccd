"""运行引擎：单次 / 连续运行、计数统计、结果分发。

设计要点：
- Runner 不依赖 Qt，可在命令行/测试里直接用；
- 相机与流水线都支持运行中切换（换配方不必重启）；
- 内部线程只负责取图+检测+记录，界面用 `snapshot()` 轮询，避免跨线程 UI 问题。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .core import Frame
from .alarm import Alarm
from .outputs import OutputManager
from .recorder import Recorder
from .recipe import Pipeline, PipelineResult
from .sources import FrameSource

MODE_SINGLE = "single"
MODE_CONTINUOUS = "continuous"
MODE_LABELS = {MODE_SINGLE: "单次", MODE_CONTINUOUS: "连续"}


@dataclass
class RunSnapshot:
    """给界面读的一份快照（线程安全地拷贝出来）。"""
    running: bool = False
    mode: str = MODE_CONTINUOUS
    total: int = 0
    ok: int = 0
    ng: int = 0
    start_ts: float = 0.0
    fps: float = 0.0
    last_elapsed_ms: float = 0.0
    mean_brightness: float = 0.0       # 最近一帧平均亮度（现场调光用）
    frame_seq: int = 0                 # 已取到的帧序号（界面据此决定要不要重绘）
    waiting_frames: int = 0            # 连续没取到图的次数（硬件触发没信号时>0）
    last_result: dict[str, Any] | None = None
    last_annotated: np.ndarray | None = None
    last_frame: np.ndarray | None = None       # 最近一帧原图（截取定位模板用）
    recheck: bool = False          # 当前是否复检模式
    rc_total: int = 0              # 复检件数
    rc_ok: int = 0
    rc_ng: int = 0
    last_error: str = ""

    @property
    def yield_rate(self) -> float:
        """良率**只按首检**算：复检件不进分子分母（否则复检会把良率变好看）。"""
        base = self.ok + self.ng
        return (self.ok / base) if base else 0.0

    @property
    def recheck_rate(self) -> float:
        """复检合格率：复检件里判 OK 的比例（这才是"返修救回来多少"）。"""
        return (self.rc_ok / self.rc_total) if self.rc_total else 0.0
class Runner:
    """取图 → 检测 → 计数 → 记录。"""

    def __init__(
        self,
        source: FrameSource | None,
        pipeline_provider: Callable[[], Pipeline | None],
        recorder: Recorder | None = None,
        outputs: OutputManager | None = None,
        alarm: Alarm | None = None,
    ):
        self._source = source
        self._pipeline_provider = pipeline_provider
        self._recorder = recorder
        self._outputs = outputs
        self._alarm = alarm

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        self._running = False
        self._mode = MODE_CONTINUOUS
        self._total = 0
        self._ok = 0
        self._ng = 0
        self._recheck = False
        self._rc_total = 0
        self._rc_ok = 0
        self._rc_ng = 0
        self._start_ts = 0.0
        self._last_elapsed = 0.0
        self._last_mean = 0.0
        self._seq = 0
        self._waiting = 0
        self._auto_detect = True          # False = 只预览不检测（单帧仍会测一次）
        self._last_result: dict[str, Any] | None = None
        self._last_annotated: np.ndarray | None = None
        self._last_raw: np.ndarray | None = None
        self._last_error = ""
        self._frame_times: list[float] = []

    # ------------------------------------------------------------------ 配置
    def set_source(self, source: FrameSource | None) -> None:
        with self._lock:
            self._source = source

    def set_recorder(self, recorder: Recorder | None) -> None:
        with self._lock:
            self._recorder = recorder

    def set_outputs(self, outputs: OutputManager | None) -> None:
        with self._lock:
            self._outputs = outputs

    def set_alarm(self, alarm: Alarm | None) -> None:
        with self._lock:
            self._alarm = alarm

    def set_auto_detect(self, on: bool) -> None:
        """自动检测：关掉后连续取图只做预览，不跑检测、不计数（调机看画面用）。"""
        with self._lock:
            self._auto_detect = bool(on)

    @property
    def auto_detect(self) -> bool:
        with self._lock:
            return self._auto_detect

    def set_recheck(self, on: bool) -> None:
        """切换首检 / 复检模式：复检帧只计复检品，不进良率。"""
        with self._lock:
            self._recheck = bool(on)

    @property
    def recheck(self) -> bool:
        with self._lock:
            return self._recheck

    # ------------------------------------------------------------------ 状态
    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            return RunSnapshot(
                running=self._running,
                mode=self._mode,
                total=self._total,
                ok=self._ok,
                ng=self._ng,
                start_ts=self._start_ts,
                fps=self._calc_fps_locked(),
                last_elapsed_ms=self._last_elapsed,
                mean_brightness=self._last_mean,
                frame_seq=self._seq,
                waiting_frames=self._waiting,
                last_result=self._last_result,
                last_annotated=self._last_annotated,
                last_frame=self._last_raw,
                recheck=self._recheck,
                rc_total=self._rc_total,
                rc_ok=self._rc_ok,
                rc_ng=self._rc_ng,
                last_error=self._last_error,
            )

    def reset_stats(self) -> None:
        with self._lock:
            self._total = self._ok = self._ng = 0
            self._rc_total = self._rc_ok = self._rc_ng = 0
            self._start_ts = 0.0
            self._frame_times.clear()

    def _calc_fps_locked(self) -> float:
        if len(self._frame_times) < 2:
            return 0.0
        span = self._frame_times[-1] - self._frame_times[0]
        if span <= 0:
            return 0.0
        return (len(self._frame_times) - 1) / span

    # ------------------------------------------------------------------ 运行
    def start(self, mode: str = MODE_CONTINUOUS) -> bool:
        """启动后台运行线程；已在运行时返回 False。"""
        with self._lock:
            if self._running:
                return False
            self._mode = mode
            self._running = True
            self._stop_evt.clear()
            if self._start_ts == 0.0:
                self._start_ts = time.time()
        self._thread = threading.Thread(target=self._loop, name="visionapp-runner", daemon=True)
        self._thread.start()
        return True

    def stop(self, wait: bool = True, timeout: float = 3.0) -> None:
        self._stop_evt.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=timeout)
        with self._lock:
            self._running = False

    def step(self, force_detect: bool = True, count: bool = True) -> bool:
        """同步处理一帧（界面【检测一次】按钮、测试用）。count=False 只出结果不计数。"""
        return self._process_once(force_detect=force_detect, count=count)

    # ------------------------------------------------------------------ 内部
    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            ok = self._process_once()
            if self._mode == MODE_SINGLE:
                break
            if not ok:
                if self._stop_evt.is_set():
                    break
                time.sleep(0.05)
        with self._lock:
            self._running = False

    def _process_once(self, force_detect: bool = False, count: bool = True) -> bool:
        with self._lock:
            source = self._source
            recorder = self._recorder
            outputs = self._outputs
            alarm = self._alarm
        pipeline = self._pipeline_provider()
        if source is None or pipeline is None:
            with self._lock:
                self._last_error = "没有帧源或流水线"
            return False

        frame = source.read(timeout_ms=1000)
        if frame is None:
            with self._lock:
                self._waiting = min(self._waiting + 1, 100000)
            return False

        with self._lock:
            self._waiting = 0
            auto = self._auto_detect
        if not auto and not force_detect:
            # 只预览：把这一帧丢给界面，不跑检测、不计数
            with self._lock:
                self._last_annotated = frame.bgr
                self._seq += 1
            self._last_raw = frame.bgr
            self._last_mean = float(frame.bgr.mean()) if frame.bgr is not None else 0.0
            return True
        recheck = self._recheck
        t0 = time.perf_counter()
        try:
            result, annotated = pipeline.run(frame, annotate=True)
        except Exception as exc:                       # noqa: BLE001
            with self._lock:
                self._last_error = f"检测异常: {exc}"
            return False
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # 记录（CSV + NG 存图）
        save_error = ""
        if recorder is not None:
            try:
                recorder.record(result, frame, annotated, elapsed_ms, recheck=recheck)
            except Exception as exc:                   # noqa: BLE001
                save_error = f"记录失败: {exc}"

        # 对外输出（串口/网络/文件）
        if outputs is not None:
            try:
                outputs.send(result)
                if outputs.last_error:
                    save_error = save_error or outputs.last_error
            except Exception as exc:                   # noqa: BLE001
                save_error = save_error or f"输出异常: {exc}"

        # NG 报警
        if alarm is not None:
            try:
                alarm.trigger(result.ok)
            except Exception:                          # noqa: BLE001
                pass

        with self._lock:
            if count:
                self._total += 1
                if recheck:
                    self._rc_total += 1
                    if result.ok:
                        self._rc_ok += 1
                    else:
                        self._rc_ng += 1
                elif result.ok:
                    self._ok += 1
                else:
                    self._ng += 1
            self._last_mean = float(frame.bgr.mean()) if frame.bgr is not None else 0.0
            self._last_elapsed = elapsed_ms
            self._last_result = result.as_dict()
            self._last_annotated = annotated
            self._frame_times.append(time.time())
            if len(self._frame_times) > 300:
                self._frame_times = self._frame_times[-300:]
            self._last_error = save_error
        return True
