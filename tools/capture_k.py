"""グラフ強化(phase4_5.md §K)と 2台連動のスクリーンショットを docs/design/ui_mock_v2/real/ に保存する。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う):

    uv run --frozen python tools/capture_k.py [--out docs/design/ui_mock_v2/real] [--only NAME ...]

撮るもの(CoreBridge + SimTransport 2 台、ID 127 と 1。実機 COM7 には接続しない):
    K_01_trigger_popover   … 「トリガ・解析」ポップオーバー(設定)
    K_02_trigger_captured  … トリガ成立 → 前後 2 s を残して停止(成立の位置に目印)
    K_03_cursors           … 一時停止中の Δ カーソル(Δt・Δ値・傾き)
    K_04_fft               … FFT 表示(正弦波 3 Hz のピーク)
    K_05_step_metrics      … ステップ応答の評価(立上り・オーバーシュート・整定)
    K_06_timeline          … 状態の帯(状態・モード・制限)
    K_07_link              … 2台連動(バイラテラル)の運転
    K_08_link_details      … 2台連動の詳細設定を開いたところ
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_FONT_DPI", "96")

SIZE = (1536, 912)


def spin(app: QApplication, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app: QApplication, cond, timeout: float = 10.0) -> bool:  # noqa: ANN001
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return bool(cond())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "design" / "ui_mock_v2" / "real"))
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--size", default=None, help="例: 1280x720")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    size = SIZE
    if args.size:
        w, _, h = args.size.partition("x")
        size = (int(w), int(h))

    app = QApplication(sys.argv)
    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.pages.operate.analysis import TriggerSpec
    from edulite05.app.ui.style import theme_manager
    from edulite05.core.control.params import (
        MitParams,
        SineParams,
        StepParams,
    )

    def want(name: str) -> bool:
        return args.only is None or any(o in name for o in args.only)

    def save(name: str, widget: QWidget) -> None:
        if not want(name):
            return
        img = widget.grab()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    def save_screen(name: str, widget: QWidget) -> None:
        """ポップオーバーなど別ウィンドウも写すため、画面からウィンドウの範囲を切り出す。"""
        if not want(name):
            return
        top = widget.window()
        g = top.frameGeometry()
        scr = top.screen() or app.primaryScreen()
        px = scr.grabWindow(0, g.x(), g.y(), g.width(), g.height())
        path = out / f"{name}.png"
        px.save(str(path))
        print(f"saved {path.name} ({px.width()}x{px.height()})")

    theme_manager().apply("dark")
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    win.resize(*size)
    win.show()
    bridge.start()

    def ui(mid: int) -> str:
        s = bridge.states().get(mid)
        return s.ui_state if s else "none"

    if not wait_for(app, lambda: len(bridge.motors()) == 2 and ui(1) == "disabled"):
        print("接続できませんでした", file=sys.stderr)
        return 1
    spin(app, 0.5)
    win.navigate("operate")
    page = win.page("operate")
    graph = page.monitor.graph
    mon = page.monitor

    def start_velocity_test(wave: str, params) -> None:  # noqa: ANN001
        bridge.enable([1], 2)
        wait_for(app, lambda: ui(1) == "enabled")
        bridge.start_controller(wave, {"motor": 1}, params)
        wait_for(app, lambda: ui(1) == "running")

    def stop_all() -> None:
        for name in ("step", "sine", "mit", "velocity"):
            bridge.stop_controller(name, {"motor": 1})
        wait_for(app, lambda: ui(1) == "enabled", timeout=5.0)
        bridge.disable([1])
        wait_for(app, lambda: ui(1) == "disabled", timeout=5.0)

    # ---------------- ステップ試験(トリガ・Δ・ステップ評価・状態の帯の元データ) ----------------
    graph.set_window(10)
    start_velocity_test("step", StepParams(target="velocity", low=0.0, high=2.0, period=3.0))
    spin(app, 0.5)

    # K_01: トリガの設定(ポップオーバー)。速度が 1.0 rad/s を立上りで横切るのを待つ
    mon.analysis.open_below(mon.analysis_btn)
    ch = mon.analysis.channel_combo.findData("m1.vel")
    mon.analysis.channel_combo.setCurrentIndex(ch)
    mon.analysis.level_edit.set_value(1.0)
    spin(app, 0.4)
    save_screen("K_01_trigger_popover", page)
    mon.analysis.hide()
    spin(app, 0.2)
    graph.arm_trigger(TriggerSpec("m1.vel", 1.0, True, 2.0))
    spin(app, 0.5)
    wait_for(app, lambda: graph.trigger_state() == "captured", timeout=15.0)
    spin(app, 0.3)
    save("K_02_trigger_captured", win)

    # K_03: Δ カーソル(トリガで止まったグラフに A・B を置く)
    rel = graph.trigger_rel() or -2.0
    graph.add_mark(rel - 0.35)
    graph.add_mark(rel + 0.55)
    graph.set_cursor_time(rel + 0.55)
    spin(app, 0.3)
    save("K_03_cursors", win)
    graph.set_paused(False)
    graph.clear_marks()

    # K_05: ステップ応答の評価(走行中)
    graph.set_window(10)
    spin(app, 5.0)
    save("K_05_step_metrics", win)
    stop_all()

    # ---------------- FFT ----------------
    start_velocity_test("sine", SineParams(target="velocity", amplitude=2.0, frequency=3.0))
    spin(app, 6.0)
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("fft"))
    idx = mon.fft_combo.findData("m1.vel")
    mon.fft_combo.setCurrentIndex(idx)
    graph.set_window(10)
    spin(app, 1.5)
    save("K_04_fft", win)
    mon.preset_combo.setCurrentIndex(mon.preset_combo.findData("standard"))
    stop_all()

    # ---------------- 状態の帯(状態の変化・モード・制限) ----------------
    graph.set_window(30)
    bridge.clear_scope()
    graph.clear()
    spin(app, 2.0)  # 停止中
    bridge.enable([1], 2)
    wait_for(app, lambda: ui(1) == "enabled")
    spin(app, 2.0)
    bridge.start_controller("step", {"motor": 1}, StepParams(target="velocity", low=0.0, high=1.5, period=3.0))
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 4.0)
    bridge.stop_controller("step", {"motor": 1})
    wait_for(app, lambda: ui(1) == "enabled")
    spin(app, 1.5)
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")
    bridge.enable([1], 0)
    wait_for(app, lambda: ui(1) == "enabled")
    s = bridge.states()[1]
    bridge.start_controller(
        "mit", {"motor": 1},
        MitParams(position=s.position_multi, velocity=30.0, kp=0.0, kd=0.5, torque_ff=0.0),
    )
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 4.0)
    save("K_06_timeline", win)
    stop_all()

    # ---------------- 2台連動(インスペクタの「その他 ▾ → 2台連動」を実際に操作)----------------
    if want("K_07"):
        graph.set_window(10)
        bridge.clear_scope()
        graph.clear()
        insp = page.inspector
        bridge.select_motor(1)
        spin(app, 0.3)
        insp.pick_other("link")
        spin(app, 0.3)
        insp.primary.click()  # 有効化(2 台とも)
        wait_for(app, lambda: ui(1) == "enabled" and ui(127) == "enabled")
        insp.primary.click()  # 運転開始 = bilateral
        wait_for(app, lambda: ui(1) == "running" and ui(127) == "running", timeout=5.0)
        spin(app, 2.5)
        save("K_07_link", win)
        if not insp.link_box.details.expanded():
            insp.link_box.details.toggle()
        spin(app, 0.3)
        save("K_08_link_details", win)
        insp.primary.click()  # 運転停止
        wait_for(app, lambda: ui(1) == "enabled", timeout=5.0)
        insp.secondary.click()
        wait_for(app, lambda: ui(1) == "disabled", timeout=5.0)

    win.close()
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
