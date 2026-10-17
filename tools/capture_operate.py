"""運転ページ(P1)のスクリーンショットを docs/design/ui_mock_v2/ に保存する(1536×912)。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う。共有フォルダ上では数分かかる):

    uv run --frozen python tools/capture_operate.py [--out docs/design/ui_mock_v2] [--only 01 03 ...]

撮るもの(`--only` に番号の一部を渡すと絞り込める):
    P1_01_operate_dark_disabled     … 停止中(有効化ボタン)、過去の運転の波形
    P1_02_operate_running_velocity  … 速度モードで運転中(2 台)
    P1_03_operate_mit_presets       … MIT(プリセット・MIT 解析のグラフ)
    P1_04_operate_jog_enabled       … JOG で有効(押下保持ボタン 2 つ)
    P1_05_operate_3d                … 3D ビュー(PyVista。実画面を撮る)
    P1_06_operate_offline_popover   … 未接続の空状態 + 接続ポップオーバー(実画面を撮る)
    P1_07_operate_light             … ライトテーマ(運転中)
    P1_08_operate_1280x720          … 最小サイズ(1280×720)
    P1_09_operate_paused_cursor     … 一時停止 + 同期カーソル
    P1_10_operate_fault             … 故障(原因表示 + 故障をリセット)
    P1_11_operate_no_motors         … モータなし(確認項目 + 再スキャン)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
# 画像のピクセルサイズを 1536x912 に揃える(Windows の表示倍率に左右されないように)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_FONT_DPI", "96")

SIZE = (1536, 912)


def spin(app: QApplication, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "design" / "ui_mock_v2"))
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    from edulite05.app.demo_bridge import DemoBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.ui.style import theme_manager

    def want(name: str) -> bool:
        return args.only is None or any(o in name for o in args.only)

    def save(name: str, widget: QWidget) -> None:
        if not want(name):
            return
        img = widget.grab()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})", flush=True)

    def save_screen(name: str, win: QWidget, *, raise_window: bool = True) -> None:
        """実画面から撮る(ポップアップ・VTK のウィジェットは QWidget.grab() に写らない)。"""
        if not want(name):
            return
        if raise_window:  # ポップアップを開いたままのときは、アクティブ化すると閉じてしまう
            win.raise_()
            win.activateWindow()
        spin(app, 0.5)
        screen = win.screen()
        geo = win.geometry()
        origin = win.mapToGlobal(QPoint(0, 0))
        rect = QRect(origin, geo.size())
        img = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()}) [screen]", flush=True)

    def open_shell(**kw: object) -> tuple[MainWindow, DemoBridge]:
        enable_3d = bool(kw.pop("enable_3d", False))
        bridge = DemoBridge(kind="simulator", demo_motion=bool(kw.pop("demo", False)), seed=3, **kw)  # type: ignore[arg-type]
        win = MainWindow(bridge, settings_path=None, enable_3d=enable_3d)
        # 実画面から撮る図で他のウィンドウが写り込まないよう、最前面に出す
        win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        win.show()
        win.raise_()
        win.activateWindow()
        bridge.start()
        spin(app, 0.4)
        win.resize(*SIZE)
        spin(app, 0.5)
        return win, bridge

    def operate(win: MainWindow):  # noqa: ANN202
        return win.page("operate")

    def run_velocity(win: MainWindow, bridge: DemoBridge, motor: int, v: float) -> None:
        insp = operate(win).inspector
        bridge.select_motor(motor)
        spin(app, 0.2)
        insp.mode_seg.button("velocity").click()
        insp.forms["velocity"].set_value("velocity", v)
        insp.trigger_primary()  # 有効化
        spin(app, 0.3)
        insp.trigger_primary()  # 運転開始
        spin(app, 0.3)

    # ---- P1_01 停止中 ----
    theme_manager().apply("dark")
    win, bridge = open_shell(demo=True)
    spin(app, 4.0)  # デモの運転(ID1 の正弦波)を溜める
    bridge.disable([1])
    operate(win).inspector.mode_seg.button("velocity").click()
    spin(app, 2.5)
    save("P1_01_operate_dark_disabled", win)

    # ---- P1_09 一時停止 + 同期カーソル ----
    mon = operate(win).monitor
    mon.graph.set_paused(True)
    spin(app, 0.3)
    g = mon.graph
    g.set_cursor_time(-4.0)
    spin(app, 0.3)
    save("P1_09_operate_paused_cursor", win)
    g.set_paused(False)
    win.close()

    # ---- P1_02 速度モードで運転中 ----
    theme_manager().apply("dark")
    win, bridge = open_shell()
    run_velocity(win, bridge, 2, -1.5)
    run_velocity(win, bridge, 1, 2.0)
    spin(app, 4.0)
    save("P1_02_operate_running_velocity", win)

    # ---- P1_07 ライト ----
    theme_manager().apply("light")
    spin(app, 1.0)
    save("P1_07_operate_light", win)
    theme_manager().apply("dark")
    spin(app, 0.4)

    # ---- P1_08 1280x720 ----
    win.resize(1280, 720)
    spin(app, 0.6)
    save("P1_08_operate_1280x720", win)
    win.close()

    # ---- P1_03 MIT ----
    win, bridge = open_shell()
    op = operate(win)
    insp = op.inspector
    bridge.select_motor(1)
    spin(app, 0.3)
    insp.mode_seg.button("mit").click()
    spin(app, 0.2)
    insp.apply_preset("spring")
    op.monitor.preset_combo.setCurrentIndex(op.monitor.preset_combo.findData("mit"))
    insp.trigger_primary()
    spin(app, 0.3)
    insp.trigger_primary()
    spin(app, 3.0)
    # 外力を再現するため目標を動かす
    insp.forms["mit"].set_value("position", 1.5)
    spin(app, 2.5)
    save("P1_03_operate_mit_presets", win)
    win.close()

    # ---- P1_04 JOG(有効)----
    win, bridge = open_shell()
    insp = operate(win).inspector
    bridge.select_motor(1)
    spin(app, 0.3)
    insp.mode_seg.button("jog").click()
    insp.trigger_primary()
    spin(app, 2.0)
    save("P1_04_operate_jog_enabled", win)
    win.close()

    # ---- P1_10 故障 ----
    win, bridge = open_shell(demo=True)
    spin(app, 3.0)
    bridge.inject_fault(1, "overtemp")
    spin(app, 1.0)
    save("P1_10_operate_fault", win)
    win.close()

    # ---- P1_06 未接続 + ポップオーバー ----
    win, bridge = open_shell(start_offline=True)
    spin(app, 0.5)
    win.open_connection()
    spin(app, 0.6)
    save_screen("P1_06_operate_offline_popover", win, raise_window=False)
    win._conn_popover.hide()  # noqa: SLF001
    win.close()

    # ---- P1_11 モータなし ----
    win, bridge = open_shell(start_offline=True)
    bridge._create_motors = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    bridge.connect_adapter({"adapter": "simulator", "port": ""})
    spin(app, 2.0)
    save("P1_11_operate_no_motors", win)
    win.close()

    # ---- P1_05 3D(PyVista の先読みに時間がかかる)----
    if want("P1_05"):
        win, bridge = open_shell(enable_3d=True)
        op = operate(win)
        run_velocity(win, bridge, 1, 1.5)
        op.monitor.set_view("3d")
        t0 = time.perf_counter()
        win.start_3d_preload()
        while op.monitor.view3d.state in ("idle", "loading") and time.perf_counter() - t0 < 240:
            spin(app, 0.5)
        print("3d state:", op.monitor.view3d.state, op.monitor.view3d.failure_reason, flush=True)
        spin(app, 3.0)
        save_screen("P1_05_operate_3d", win)
        win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
