"""phase4-fb-X の画面(安定したグラフ枠・位置の目標の基準・原点に設定・MIT の抵抗ゼロ)を
`--sim`(CoreBridge + シミュレータ)の実ウィンドウで撮り、docs/design/ui_mock_v2/real/ に保存する。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う。実機には接続しない):

    uv run --frozen python tools/capture_x.py [--out docs/design/ui_mock_v2/real] [--only NAME ...]

撮るもの:
    X_01_graph_no_step       … ステップ結果が無い(行は淡いプレースホルダ)。プロットの高さを表示
    X_02_graph_step          … ステップ応答の結果あり。X_01 とプロットの高さが同じ
    X_03_position_turn_big   … 位置モード・1 回転内(最短)。多回転 +400 rad の位置から
    X_04_zero_popover        … 運転中に [原点に設定] → 確認ポップオーバー
    X_05_zero_done           … 設定後(読み値 0・トースト)
    X_06_mit_presets         … MIT のプリセット(抵抗ゼロ)と負の Kd
    X_07_mit_limits          … 「制限と加速度」を開いた摩擦補償
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter
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
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    import edulite05.sim.transport as sim_transport
    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.ui.style import theme_manager
    from edulite05.core.control.params import StepParams, VelocityParams

    def want(name: str) -> bool:
        return args.only is None or any(o in name for o in args.only)

    def save(name: str, widget: QWidget, *overlays: QWidget) -> None:
        if not want(name):
            return
        img = widget.grab()
        if overlays:  # ポップオーバーは別ウィンドウなので、位置を合わせて重ねる
            p = QPainter(img)
            origin = widget.mapToGlobal(QPoint(0, 0))
            for ov in overlays:
                p.drawPixmap(ov.mapToGlobal(QPoint(0, 0)) - origin, ov.grab())
            p.end()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    theme_manager().apply("dark")

    def open_session(initial_position: float = 0.0):
        orig = sim_transport.make_motors

        def patched(n=1, ids=None, profile=None, **kw):  # noqa: ANN001, ANN202
            if initial_position:
                kw["initial_position"] = initial_position
            return orig(n, ids, profile, **kw)

        sim_transport.make_motors = patched
        bridge = CoreBridge(kind="simulator", sim_motors=2)
        win = MainWindow(bridge, settings_path=None, enable_3d=False)
        win.resize(*SIZE)
        win.show()
        bridge.start()
        ui = lambda mid: (  # noqa: E731
            bridge.states().get(mid).ui_state if bridge.states().get(mid) else "none"
        )
        if not wait_for(app, lambda: len(bridge.motors()) == 2 and ui(1) == "disabled"):
            raise SystemExit("接続できませんでした")
        sim_transport.make_motors = orig
        spin(app, 0.6)
        win.navigate("operate")
        spin(app, 0.3)
        return bridge, win, ui

    # ================= A: 通常の位置(グラフ・MIT)=================
    bridge, win, ui = open_session()
    op = win.page("operate")
    g = op.monitor.graph
    insp = op.inspector
    bridge.enable([1], 2)
    wait_for(app, lambda: ui(1) == "enabled")
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=2.0))
    wait_for(app, lambda: ui(1) == "running")
    g.set_window(5)  # 立ち上がり(ステップ)が窓から出るまで待つと、結果の行は淡いプレースホルダになる
    spin(app, 7.5)
    print("step result:", g.step_result())
    h1 = g.glw.height()
    print(f"plot height without step result: {h1}")
    save("X_01_graph_no_step", win)

    bridge.stop_controller("velocity", {"motor": 1})
    wait_for(app, lambda: ui(1) == "enabled")
    g.set_window(10)
    bridge.start_controller(
        "step", {"motor": 1}, StepParams(target="velocity", low=0.0, high=2.0, period=2.0)
    )
    wait_for(app, lambda: ui(1) == "running")
    wait_for(app, lambda: g.step_result() is not None, timeout=15.0)
    spin(app, 1.0)
    h2 = g.glw.height()
    print(f"plot height with step result:    {h2}  (same: {h1 == h2})")
    save("X_02_graph_step", win)
    bridge.stop_controller("step", {"motor": 1})
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")

    # ---- MIT: プリセット(抵抗ゼロ)と負の Kd ----
    insp.mode_seg.button("mit").click()
    spin(app, 0.3)
    insp.apply_preset("zero")
    insp.forms["mit"].set_value("kd", -0.03)
    spin(app, 0.4)
    save("X_06_mit_presets", win)
    insp.limits.set_expanded(True)
    spin(app, 0.3)
    insp.scroll.verticalScrollBar().setValue(insp.scroll.verticalScrollBar().maximum())
    spin(app, 0.3)
    save("X_07_mit_limits", win)
    win.close()
    bridge.shutdown()

    # ================= B: 多回転 +400 rad の位置 =================
    bridge, win, ui = open_session(400.0)
    op = win.page("operate")
    insp = op.inspector
    insp.mode_seg.button("position").click()
    spin(app, 0.5)
    insp.turn_row.set_value(1.0, emit=True)
    spin(app, 0.5)
    print("hint:", insp.target_hint_text())
    save("X_03_position_turn_big", win)

    insp.primary.click()  # 有効化
    wait_for(app, lambda: ui(1) == "enabled")
    insp.turn_row.set_value(1.0, emit=True)
    insp.primary.click()  # 運転開始
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 1.0)
    op.strip.zero_btn.clicked.emit()
    spin(app, 0.4)
    save("X_04_zero_popover", win, op.zero_popover)
    op.zero_popover.confirm_btn.click()
    wait_for(app, lambda: abs(bridge.states()[1].position_multi) < 0.5, timeout=15.0)
    spin(app, 0.8)
    save("X_05_zero_done", win)
    win.close()
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
