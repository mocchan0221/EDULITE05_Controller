"""GUI(CoreBridge)を実機 COM7 で確認する(hw_bench.md HW-3): 接続→スキャン→読出→LOW で JOG 0.5 rad/s を約 5 s→停止→無効化。

**モータが回る。** 実行は CLAUDE.md の実機ルール(ユーザーの許可・LOW 段階)に従うこと。

使い方(リポジトリ直下で): uv run --frozen python tools/hw_gui_check.py <出力ディレクトリ>
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_FONT_DPI", "96")
from PySide6.QtWidgets import QApplication  # noqa: E402

S = Path(sys.argv[1])
ROOT = Path.cwd()


def spin(app, sec):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, cond, timeout=10.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return bool(cond())


def cam(name):
    def run():
        subprocess.run(
            ["uv", "run", "--no-project", "--with", "opencv-python", "python",
             str(ROOT / "tools" / "camera_snapshot.py"), str(S / f"{name}.jpg"), "0"],
            capture_output=True, cwd=str(S),
        )
    t = threading.Thread(target=run)
    t.start()
    return t


def main():
    app = QApplication(sys.argv)
    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.ui.style import theme_manager
    from edulite05.core.control.params import JogParams

    theme_manager().apply("dark")
    bridge = CoreBridge(kind="hardware", default_port="COM7")
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    win.resize(1536, 912)
    win.show()
    bridge.start()
    logs = []
    bridge.log_message.connect(lambda *a: logs.append(a))

    def ui(mid):
        s = bridge.states().get(mid)
        return s.ui_state if s else "none"

    def save(name):
        win.grab().save(str(S / f"{name}.png"))
        print("saved", name)

    rc = 0
    try:
        bridge.connect_adapter({"adapter": "robstride_at", "port": "COM7"})
        ok = wait_for(app, lambda: bridge.connection_info().connected, 10)
        print("connected", ok, bridge.connection_info())
        if not any(m.id == 127 for m in bridge.motors()):
            bridge.scan()
            wait_for(app, lambda: any(m.id == 127 for m in bridge.motors()), 15)
        print("motors", bridge.motors())
        ok = wait_for(app, lambda: ui(127) == "disabled", 10)
        print("ui127", ui(127), bridge.power_tier())
        if not ok or bridge.power_tier() != "low":
            print("ABORT: not disabled / not LOW")
            return 2
        bridge.select_motor(127)
        spin(app, 2.0)
        s = bridge.states()[127]
        print("state", s)
        win.navigate("operate")
        spin(app, 0.5)
        save("G_01_hw_connected")

        win.navigate("params")
        spin(app, 0.3)
        bridge.read_all_params(127)
        spin(app, 4.0)
        save("G_02_hw_params")

        # ---- 低速 JOG ----
        win.navigate("operate")
        cb = cam("G_cam_before")
        cb.join()
        p0 = bridge.states()[127].position_multi
        bridge.enable([127], 2)  # JOG は速度モード
        if not wait_for(app, lambda: ui(127) == "enabled", 5):
            print("ABORT: enable failed", ui(127))
            return 3
        bridge.start_controller("jog", {"motor": 127}, JogParams(speed=0.5, accel=5.0, current_limit=1.0))
        wait_for(app, lambda: ui(127) == "running", 3)
        cm = cam("G_cam_mid")
        spin(app, 4.5)
        s = bridge.states()[127]
        print("running vel", round(s.velocity, 3), "torque", round(s.torque, 3), "temp", s.temperature)
        save("G_03_hw_jog")
        cm.join()
        bridge.stop_controller("jog", {"motor": 127})
        wait_for(app, lambda: ui(127) == "enabled", 5)
        spin(app, 0.5)
        bridge.disable([127])
        ok = wait_for(app, lambda: ui(127) == "disabled", 5)
        p1 = bridge.states()[127].position_multi
        print("disabled", ok, "moved rad", round(p1 - p0, 3))
        cam("G_cam_after").join()
        save("G_04_hw_after")
        win.navigate("diagnostics", "stats")
        spin(app, 1.0)
        save("G_05_hw_stats")
    except Exception as e:  # noqa: BLE001
        print("ERROR", repr(e))
        rc = 1
        try:
            bridge.estop()
        except Exception:  # noqa: BLE001
            pass
    finally:
        for a in logs[-25:]:
            print("LOG", a)
        win.close()
        bridge.shutdown()
        spin(app, 0.5)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
