"""UI v2 のスクリーンショットを docs/design/ui_mock_v2/ に保存する(1536×912)。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う。共有フォルダ上では 1 分前後かかる):

    uv run --frozen python tools/capture_ui_mock.py [--out docs/design/ui_mock_v2] [--only NAME ...]

撮るもの(`--only` に番号付き名の一部を渡すと絞り込める):
    01_gallery_dark / 02_gallery_light             … 部品ギャラリー
    03_shell_dark / 04_shell_light                 … シェル(運転ページ、低出力、接続中、モータ 2 台)
    05_shell_high_banner                           … 高出力(琥珀の常設バナー)
    06_shell_estop_latched                         … 非常停止ラッチ(赤い全幅バナー)
    07_shell_fault                                 … 故障(バナー + チップ + 診断の赤い点)
    08_shell_comm_lost                             … 通信断(赤バナー + 再接続)
    09_shell_offline                               … 未接続(`--start-offline` 相当)
    10_shell_min_1280x720                          … 最小サイズ(1280×720)
v1 の画像(docs/design/ui_mock/)は比較用にそのまま残す。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

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
    from edulite05.app.gallery import GalleryWindow
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
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    def open_shell(**kw: object) -> tuple[MainWindow, DemoBridge]:
        bridge = DemoBridge(kind="simulator", demo_motion=True, seed=1, **kw)  # type: ignore[arg-type]
        win = MainWindow(bridge, settings_path=None, enable_3d=False)
        win.show()
        bridge.start()
        spin(app, 0.4)
        win.resize(*SIZE)
        spin(app, 0.4)
        return win, bridge

    # ---- ギャラリー ----
    if want("gallery"):
        theme_manager().apply("dark")
        gal = GalleryWindow()
        gal.show()
        gal.resize(*SIZE)
        spin(app, 0.6)
        save("01_gallery_dark", gal)
        gal.theme_seg.set_current("light")
        theme_manager().apply("light")
        spin(app, 0.6)
        save("02_gallery_light", gal)
        gal.close()

    # ---- シェル(ダーク)----
    theme_manager().apply("dark")
    win, bridge = open_shell()
    spin(app, 1.5)
    save("03_shell_dark", win)

    win.resize(1280, 720)  # 最小サイズでも主要操作が見える
    spin(app, 0.5)
    save("10_shell_min_1280x720", win)
    win.resize(*SIZE)
    spin(app, 0.4)

    theme_manager().apply("light")
    spin(app, 0.5)
    save("04_shell_light", win)
    theme_manager().apply("dark")
    spin(app, 0.3)

    # 高出力: 全モータを無効化してから切り替える
    bridge.disable([1, 2])
    spin(app, 0.2)
    bridge.set_power_tier("high")
    spin(app, 0.5)
    save("05_shell_high_banner", win)
    bridge.set_power_tier("low")
    spin(app, 0.2)

    # 非常停止ラッチ
    bridge.enable([1], 2)
    spin(app, 0.2)
    bridge.estop()
    spin(app, 0.5)
    save("06_shell_estop_latched", win)
    bridge.release_estop()
    spin(app, 0.2)

    # 故障
    bridge.inject_fault(2, "overtemp")
    spin(app, 0.5)
    save("07_shell_fault", win)
    bridge.clear_fault([2])
    spin(app, 0.2)

    # 通信断
    bridge.inject_comm_loss()
    spin(app, 0.5)
    save("08_shell_comm_lost", win)
    win.close()

    # ---- 未接続 ----
    win2, _bridge2 = open_shell(start_offline=True)
    spin(app, 0.5)
    save("09_shell_offline", win2)
    win2.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
