"""「スクリプト」ページ(プラグイン plugins/scripting)のスクリーンショットを
docs/design/ui_mock_v2/real/ に保存する(phase4_5.md M)。

実行(フォント描画のため offscreen ではなく実ウィンドウ。実機 COM7 には接続しない):

    uv run --frozen python tools/capture_scripting.py [--out docs/design/ui_mock_v2/real]

撮るもの(CoreBridge + SimTransport 2 台、ID 127 と 1。プロジェクトの plugins/ をそのまま読む):
    M_01_console_idle            … コンソール(待機中。空の状態の例)
    M_02_console_after_command   … コマンド実行後(出力・エコー・結果)
    M_03_sequence_running        … シーケンス実行中(現在のステップ・進捗・ログ)
    M_04_sequence_problems       … 検証エラー(行番号つき)で実行できない状態
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_FONT_DPI", "96")

SIZE = (1536, 912)

BAD = """\
name: 検証エラーの例
steps:
  - enable: velocity
  - run:
      controller: velocity
      params: {velocity: abc, accel: 20}
      seconds: 2
  - wait: -1
  - dance
"""


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
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.plugin_host import create_manager
    from edulite05.app.ui.style import theme_manager

    def save(name: str, widget: QWidget) -> None:
        img = widget.grab()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    # 検証エラーの例を見せるため、sequences/ を一時フォルダへ写してそこへ置く
    tmp = Path(tempfile.mkdtemp(prefix="edulite05_scripting_"))
    shutil.copytree(ROOT / "plugins" / "scripting", tmp / "plugins" / "scripting",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "sequences").mkdir()
    shutil.copy(ROOT / "sequences" / "velocity_steps.yaml", tmp / "sequences")
    (tmp / "sequences" / "bad_example.yaml").write_text(BAD, encoding="utf-8")

    theme_manager().apply(args.theme)
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    win = MainWindow(bridge, settings_path=None, enable_3d=False,
                     plugins=create_manager(None, project_root=tmp))
    win.resize(*SIZE)
    win.show()
    bridge.start()
    ui = lambda mid: (bridge.states().get(mid).ui_state if bridge.states().get(mid) else "none")  # noqa: E731
    if not wait_for(app, lambda: len(bridge.motors()) == 2 and ui(1) == "disabled"):
        raise SystemExit("接続できませんでした")
    spin(app, 0.5)

    win.navigate("scripting")
    spin(app, 0.5)
    page = win.page("scripting")
    console = page.console

    # ---- M_01: コンソール(待機中) ----
    save("M_01_console_idle", win)

    # ---- M_02: コマンド実行後 ----
    def run(code: str, timeout: float = 15.0) -> None:
        console.input.setPlainText(code)
        console.submit()
        wait_for(app, lambda: not console.running, timeout)
        spin(app, 0.2)

    run("ids()")
    run('wait(service.enable(1, RunMode.VELOCITY), 8)\n'
        'service.start(1, make("velocity", velocity=1.5, accel=20))\n'
        'sleep(1.5)\n'
        'print("速度", round(telemetry.latest("m1.vel")[1], 2), "rad/s")')
    run('service.stop_motion(1); sleep(0.5); service.ui_state(1)')
    run('service.start(1, make("velocity", velocity=30.0))')  # SafetyGuard: 出力段階 LOW の上限へ
    run('sleep(1.0); round(telemetry.latest("m1.vel")[1], 2)')
    run('service.stop_motion(1); wait(service.disable(1), 8)')
    run('1 / 0')
    console.input.setPlainText("service.motor_ids()")
    spin(app, 0.3)
    save("M_02_console_after_command", win)
    console.input.clear()

    # ---- M_03: シーケンス実行中 ----
    page.show_view("sequence")
    spin(app, 0.4)
    seq = page.sequence
    seq.select_file(tmp / "sequences" / "velocity_steps.yaml")
    spin(app, 0.3)
    seq.motor_combo.setCurrentIndex(seq.motor_combo.findData(1))
    seq.start()
    wait_for(app, lambda: seq.step_rows and seq.step_rows[5].mark in ("current", "done"), 20)
    spin(app, 0.8)
    save("M_03_sequence_running", win)
    seq.abort()
    wait_for(app, lambda: not seq.running, 10)
    spin(app, 0.4)

    # ---- M_04: 検証エラー ----
    seq.select_file(tmp / "sequences" / "bad_example.yaml")
    spin(app, 0.5)
    save("M_04_sequence_problems", win)

    win.page("scripting").shutdown()
    win.close()
    bridge.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
