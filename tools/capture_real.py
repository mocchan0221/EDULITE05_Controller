"""CoreBridge(本物のコア + シミュレータ)で動かした画面のスクリーンショットを
docs/design/ui_mock_v2/real/ に保存する(phase3.md §3)。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う):

    uv run --frozen python tools/capture_real.py [--out docs/design/ui_mock_v2/real] [--only NAME ...]

撮るもの(すべて `--sim` 相当: CoreBridge + SimTransport 2 台、ID 127 と 1):
    R_01_operate_velocity_running  … 運転: ID 1 を速度 2 rad/s で運転中(実データのグラフ・読み値)
    R_02_operate_mit_limited       … 運転: MIT を LOW の上限で制限中(「制限中」の注記)
    R_03_params_read               … パラメータ: シミュレータから全読出した値
    R_04_diag_frames               … 診断: CAN フレーム(TX / RX とデコード)
    R_05_diag_log                  … 診断: イベントログ(日本語)
    R_06_diag_stats                … 診断: 通信統計
    R_07_maintenance               … 保守
    R_08_fault                     … 故障注入 → 故障の表示(診断の故障ビュー)
    R_09_comm_lost                 … 通信断(バナー)
実機(COM7)には接続しない。
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
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.ui.style import theme_manager
    from edulite05.core.control.params import MitParams, VelocityParams

    def want(name: str) -> bool:
        return args.only is None or any(o in name for o in args.only)

    def save(name: str, widget: QWidget) -> None:
        if not want(name):
            return
        img = widget.grab()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    theme_manager().apply("dark")
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    win.resize(*SIZE)
    win.show()
    bridge.start()
    ui = lambda mid: (bridge.states().get(mid).ui_state if bridge.states().get(mid) else "none")  # noqa: E731
    if not wait_for(app, lambda: len(bridge.motors()) == 2 and ui(1) == "disabled"):
        print("接続できませんでした", file=sys.stderr)
        return 1
    spin(app, 0.5)

    # ---- 運転: 速度 2 rad/s ----
    bridge.enable([1], 2)
    wait_for(app, lambda: ui(1) == "enabled")
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=2.0))
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 2.5)
    bridge.update_controller_params("velocity", {"motor": 1}, VelocityParams(velocity=-1.0))
    spin(app, 2.5)
    bridge.update_controller_params("velocity", {"motor": 1}, VelocityParams(velocity=2.5))
    spin(app, 3.0)
    win.navigate("operate")
    spin(app, 0.3)
    save("R_01_operate_velocity_running", win)

    # ---- 診断(走行中にフレーム・統計) ----
    win.navigate("diagnostics", "frames")
    spin(app, 1.2)
    save("R_04_diag_frames", win)
    win.navigate("diagnostics", "stats")
    spin(app, 1.5)
    save("R_06_diag_stats", win)
    bridge.stop_controller("velocity", {"motor": 1})
    wait_for(app, lambda: ui(1) == "enabled")
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")
    win.navigate("diagnostics", "log")
    spin(app, 0.5)
    save("R_05_diag_log", win)

    # ---- MIT を LOW の上限で制限 ----
    bridge.enable([1], 0)
    wait_for(app, lambda: ui(1) == "enabled")
    s = bridge.states()[1]
    bridge.start_controller(
        "mit", {"motor": 1},
        MitParams(position=s.position_multi, velocity=30.0, kp=0.0, kd=0.5, torque_ff=0.0),
    )
    wait_for(app, lambda: ui(1) == "running")
    win.navigate("operate")
    spin(app, 3.0)
    save("R_02_operate_mit_limited", win)
    bridge.stop_controller("mit", {"motor": 1})
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")

    # ---- パラメータ(シミュレータから全読出)----
    win.navigate("params")
    spin(app, 0.5)
    bridge.read_all_params(1)
    spin(app, 2.0)
    save("R_03_params_read", win)

    # ---- 保守 ----
    win.navigate("maintenance")
    spin(app, 0.6)
    save("R_07_maintenance", win)

    # ---- 故障 ----
    bridge.inject_fault(1, "overtemp")
    wait_for(app, lambda: ui(1) == "fault")
    win.navigate("diagnostics", "faults")
    spin(app, 1.0)
    save("R_08_fault", win)
    bridge.clear_fault([1])
    wait_for(app, lambda: ui(1) == "disabled")

    # ---- 通信断 ----
    win.navigate("operate")
    bridge.inject_comm_loss()
    wait_for(app, lambda: bridge.connection_info().lost, timeout=10.0)
    spin(app, 0.8)
    save("R_09_comm_lost", win)

    win.close()
    bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
