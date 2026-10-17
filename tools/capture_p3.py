"""P3(保守・設定ページとダイアログ)のスクリーンショットを docs/design/ui_mock_v2/ に保存する。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う。共有フォルダ上では 1 分前後かかる):

    uv run --frozen python tools/capture_p3.py [--out docs/design/ui_mock_v2] [--only NAME ...]

撮るもの:
    P3_01_maintenance_dark          … 保守ページ(ダーク 1536×912。モータ停止中 = 実行できる)
    P3_02_maintenance_locked        … モータが有効 → [設定…] が押せず、理由を表示
    P3_03_dialog_can_id             … CAN ID を変更ダイアログ(開いた直後)
    P3_04_dialog_protocol           … プロトコル切替ダイアログ(MIT を選んだ状態)
    P3_05_dialog_high_tier          … 出力段階 HIGH への切替ダイアログ
    P3_06_dialog_estop_release      … 非常停止の解除ダイアログ
    P3_07_settings_dark             … 設定ページ
    P3_08_maintenance_light         … 保守ページ(ライト)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog, QWidget

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
    from edulite05.app.ui.dialogs import (
        EStopReleaseDialog,
        HighTierDialog,
        MotorTarget,
        RestoreProtocolDialog,
        SetCanIdDialog,
        SwitchProtocolDialog,
    )
    from edulite05.app.ui.style import theme_manager

    def want(name: str) -> bool:
        return args.only is None or any(o in name for o in args.only)

    def save(name: str, widget: QWidget) -> None:
        if not want(name):
            return
        img = widget.grab()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    def show_dialog(name: str, dlg: QDialog) -> None:
        """モーダルを開かずに表示して撮る(exec() だと撮影が止まる)。"""
        if not want(name):
            return
        dlg.show()
        spin(app, 0.5)
        save(name, dlg)
        dlg.close()

    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=1)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    win.show()
    bridge.start()
    spin(app, 0.4)
    win.resize(*SIZE)
    win.navigate("maintenance")
    spin(app, 0.6)
    save("P3_01_maintenance_dark", win)

    # 有効にすると保守は実行できない(押せず、理由を言う)
    bridge.enable([1], 2)
    spin(app, 0.5)
    save("P3_02_maintenance_locked", win)
    bridge.disable([1])
    spin(app, 0.3)

    motor = MotorTarget(1, "EL05 #1", "disabled")
    show_dialog("P3_03_dialog_can_id", SetCanIdDialog(win, motor))
    proto = SwitchProtocolDialog(win, motor)
    proto.combo.setCurrentIndex(proto.combo.findData(2))
    show_dialog("P3_04_dialog_protocol", proto)
    show_dialog("P3_05_dialog_high_tier", HighTierDialog(win))
    show_dialog("P3_06_dialog_estop_release", EStopReleaseDialog(win))

    win.navigate("settings")
    spin(app, 0.5)
    save("P3_07_settings_dark", win)

    theme_manager().apply("light")
    win.navigate("maintenance")
    spin(app, 0.6)
    save("P3_08_maintenance_light", win)
    theme_manager().apply("dark")
    restore = RestoreProtocolDialog(win, None)  # モータを選ばなくても開ける。ID とプロトコルを入力
    restore.id_spin.setValue(7)
    restore.combo.setCurrentIndex(restore.combo.findData("mit"))
    show_dialog("P3_09_dialog_restore_protocol", restore)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
