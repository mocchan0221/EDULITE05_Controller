"""英語 UI(`--lang en` 相当)の主要ページのスクリーンショットを
docs/design/ui_mock_v2/real/ に保存する(phase4_5.md P)。

実行(フォント描画のため offscreen ではなく実ウィンドウ。実機 COM7 には接続しない):

    uv run --frozen python tools/capture_en.py [--out docs/design/ui_mock_v2/real] [--theme dark|light]

撮るもの(CoreBridge + SimTransport 2 台、ID 127 と 1。プロジェクトの plugins/ をそのまま読む):
    P_01_operate_velocity   … 運転(速度モードで運転中)
    P_02_operate_mit        … 運転(MIT・故障バナーなし。モードの並びと入力欄)
    P_03_params             … パラメータ(全読出)
    P_04_diag_frames        … 診断: CAN フレーム
    P_05_diag_faults        … 診断: 故障(過温を注入)
    P_06_maintenance        … 保守
    P_07_settings           … 設定(言語の行)
    P_08_script_console     … スクリプト: コンソール
    P_09_script_sequence    … スクリプト: シーケンス
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
    ap.add_argument("--theme", choices=["dark", "light"], default="dark")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--size", nargs=2, type=int, default=list(SIZE), metavar=("W", "H"))
    ap.add_argument("--prefix", default="P")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    from edulite05.app import i18n

    i18n.load_catalog("en")  # ウィンドウを作る前に(main.py と同じ)

    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.plugin_host import create_manager
    from edulite05.app.ui.style import theme_manager
    from edulite05.core.control.params import VelocityParams

    def save(name: str, widget: QWidget) -> None:
        if args.only is not None and not any(o in name for o in args.only):
            return
        img = widget.grab()
        path = out / f"{args.prefix}_{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    theme_manager().apply(args.theme)
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    win = MainWindow(bridge, settings_path=None, enable_3d=False,
                     plugins=create_manager(None, project_root=ROOT))
    win.resize(*args.size)
    win.show()
    bridge.start()
    ui = lambda mid: (bridge.states().get(mid).ui_state if bridge.states().get(mid) else "none")  # noqa: E731
    if not wait_for(app, lambda: len(bridge.motors()) == 2 and ui(1) == "disabled"):
        print("could not connect", file=sys.stderr)
        return 1
    spin(app, 0.5)

    # ---- 運転: 速度 ----
    bridge.enable([1], 2)
    wait_for(app, lambda: ui(1) == "enabled")
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=2.0))
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 2.5)
    bridge.update_controller_params("velocity", {"motor": 1}, VelocityParams(velocity=-1.0))
    spin(app, 2.5)
    bridge.update_controller_params("velocity", {"motor": 1}, VelocityParams(velocity=2.5))
    spin(app, 2.5)
    win.navigate("operate")
    spin(app, 0.3)
    save("01_operate_velocity", win)

    # ---- 診断: フレーム(走行中) ----
    win.navigate("diagnostics", "frames")
    spin(app, 1.2)
    save("04_diag_frames", win)
    bridge.stop_controller("velocity", {"motor": 1})
    wait_for(app, lambda: ui(1) == "enabled")
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")

    # ---- 運転: MIT(モード切替と入力欄。有効化して開始まで) ----
    win.navigate("operate")
    bridge.enable([1], 0)
    wait_for(app, lambda: ui(1) == "enabled")
    spin(app, 0.5)
    inspector = win.page("operate").inspector
    inspector.mode_seg.selected.emit("mit")
    spin(app, 0.6)
    inspector.primary.click()  # 開始(既定のパラメータ。出力段階 LOW の上限で制限される)
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 3.0)
    save("02_operate_mit", win)
    inspector.primary.click()  # 停止
    spin(app, 0.5)
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")

    # ---- パラメータ ----
    win.navigate("params")
    spin(app, 0.5)
    bridge.read_all_params(1)
    spin(app, 2.0)
    save("03_params", win)

    # ---- 保守・設定 ----
    win.navigate("maintenance")
    spin(app, 0.6)
    save("06_maintenance", win)
    win.navigate("settings")
    spin(app, 0.6)
    save("07_settings", win)

    # ---- スクリプト ----
    if "scripting" in [p.id for p in win.plugins.ui.pages()]:
        win.navigate("scripting")
        spin(app, 0.5)
        page = win.page("scripting")
        console = page.console
        console.input.setPlainText("ids()")
        console.submit()
        wait_for(app, lambda: not console.running, 15)
        spin(app, 0.3)
        console.input.setPlainText("service.motor_ids()")
        spin(app, 0.2)
        save("08_script_console", win)
        console.input.clear()
        page.show_view("sequence")
        spin(app, 0.5)
        seq = page.sequence
        if seq.file_combo.count():
            seq.file_combo.setCurrentIndex(0)
            spin(app, 0.4)
        save("09_script_sequence", win)

    # ---- 診断: 故障 ----
    bridge.inject_fault(1, "overtemp")
    wait_for(app, lambda: ui(1) == "fault")
    win.navigate("diagnostics", "faults")
    spin(app, 1.0)
    save("05_diag_faults", win)
    bridge.clear_fault([1])
    wait_for(app, lambda: ui(1) == "disabled")

    scripting = win.page("scripting") if "scripting" in [p.id for p in win.plugins.ui.pages()] else None
    if scripting is not None:
        scripting.shutdown()
    win.close()
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
