"""段階 2 / P2(パラメータ・診断ページ)のスクリーンショットを docs/design/ui_mock_v2/ に保存する(1536×912)。

実行(フォント描画のため offscreen ではなく実ウィンドウを使う。共有フォルダ上では 1〜2 分かかる):

    uv run --frozen python tools/capture_p2.py [--out docs/design/ui_mock_v2] [--only NAME ...]

撮るもの(`--only` に番号付き名の一部を渡すと絞り込める):
    P2_01_params_dark            … パラメータ(未書込 2 + Flash 未保存 1。空のセルにホバーで鉛筆)
    P2_02_params_locked          … モータ有効中の書込ロック(ボタンのツールチップ付き)
    P2_03_diag_faults            … 診断: 故障あり(カード + 履歴)
    P2_04_diag_healthy           … 診断: 故障なし(1 行)
    P2_05_diag_frames            … 診断: CAN フレーム
    P2_06_diag_log               … 診断: イベントログ
    P2_07_params_light           … パラメータ(ライト)
    P2_08_params_range_error     … 範囲外入力(入力欄の赤枠 + 行の右端に理由)
    P2_09_diag_stats             … 診断: 通信統計
    P2_10_params_flash_confirm   … Flash 保存の確認ダイアログ(共通の FlashSaveDialog)
    P2_11_params_1280x720        … パラメータを最小サイズ 1280×720 で(ツールバーが 2 行に折り返す)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

ROOT = Path(__file__).resolve().parents[1]
# 画像のピクセルサイズを 1536x912 に揃える(Windows の表示倍率に左右されないように)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_FONT_DPI", "96")

SIZE = (1536, 912)
SPD_KP, SPD_KI, LIMIT_SPD, LIMIT_TORQUE = 0x701F, 0x7020, 0x7017, 0x700B


def spin(app: QApplication, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def with_tooltip(app: QApplication, win: QWidget, anchor: QWidget, text: str) -> QPixmap:
    """ツールチップを出して、ウィンドウの画像に重ねる(ツールチップは別ウィンドウなので grab に写らない)。"""
    pos = anchor.mapToGlobal(QPoint(anchor.width() // 2, anchor.height() + 2))
    QToolTip.showText(pos, text, anchor, anchor.rect(), 60000)
    spin(app, 0.4)
    img = win.grab()
    for w in QApplication.topLevelWidgets():
        if w.isVisible() and w.metaObject().className() == "QTipLabel":
            p = QPainter(img)
            p.drawPixmap(win.mapFromGlobal(w.pos()), w.grab())
            p.end()
    QToolTip.hideText()
    return img


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
    from edulite05.core.control.params import VelocityParams

    def want(name: str) -> bool:
        return args.only is None or any(o in name for o in args.only)

    def save(name: str, image: QWidget | QPixmap) -> None:
        if not want(name):
            return
        img = image.grab() if isinstance(image, QWidget) else image
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=1)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    win.show()
    bridge.start()
    spin(app, 0.4)
    win.resize(*SIZE)
    spin(app, 0.4)

    # ================= パラメータ =================
    win.navigate("params")
    page = win.page("params")
    spin(app, 0.8)  # 初回は自動で全読込
    bridge.write_param(1, SPD_KI, 0.05)  # 書込済・Flash 未保存 1
    page.set_pending(SPD_KP, 8.0)  # 未書込 2
    page.set_pending(LIMIT_SPD, 3.0)
    spin(app, 0.5)
    # ホバー: 空の「新しい値」セルに鉛筆が出る(電流ループ Ki の行)
    hover_row = page._rows[0x7011].row
    cell = page.table.visualRect(page.table.model().index(hover_row, 3)).center()
    QTest.mouseMove(page.table.viewport(), cell)
    spin(app, 0.4)
    save("P2_01_params_dark", win)
    QTest.mouseMove(page.table.viewport(), QPoint(-1, -1))

    # 範囲外入力(入力欄の赤枠 + 行の右端に理由)
    edit = page.begin_edit(LIMIT_TORQUE)
    edit.type_text("9")
    spin(app, 0.3)
    page.table.scrollToItem(page.table.item(page._rows[LIMIT_TORQUE].row, 3))
    spin(app, 0.3)
    save("P2_08_params_range_error", win)
    edit.type_text("")
    page.end_edit()
    page.table.setCurrentCell(-1, -1)  # キーボードカーソルの枠を消す
    page.table.clearFocus()
    page.table.scrollToTop()

    # モータ有効中: 書込ロック + ツールチップ
    bridge.enable([1], 2)
    spin(app, 0.5)
    if want("P2_02"):
        save("P2_02_params_locked", with_tooltip(app, win, page.write_btn, page.write_btn.toolTip()))
    bridge.disable([1])
    spin(app, 0.3)

    # ライト
    theme_manager().apply("light")
    spin(app, 0.6)
    save("P2_07_params_light", win)
    theme_manager().apply("dark")
    spin(app, 0.4)

    # 最小サイズ 1280×720(ツールバーの折返し)
    if want("P2_11"):
        win.resize(1280, 720)
        spin(app, 0.6)
        save("P2_11_params_1280x720", win)
        win.resize(*SIZE)
        spin(app, 0.4)

    # Flash 保存の確認ダイアログ(exec せずに表示して撮る)
    if want("P2_10"):
        from edulite05.app.pages.params import page as params_page_mod
        from edulite05.app.ui.dialogs import FlashSaveDialog

        bridge.write_param(1, 0x7011, 0.02)
        bridge.write_param(1, LIMIT_TORQUE, 4.0)
        spin(app, 0.3)

        def grab_dialog(parent: QWidget, motor_id: int, items: object = ()) -> bool:
            dlg = FlashSaveDialog(parent, motor_id=motor_id, items=items)  # type: ignore[arg-type]
            dlg.show()
            spin(app, 0.5)
            save("P2_10_params_flash_confirm", dlg.grab())
            dlg.close()
            return False

        original = params_page_mod.confirm_flash_save
        params_page_mod.confirm_flash_save = grab_dialog  # type: ignore[assignment]
        page.save_flash()
        params_page_mod.confirm_flash_save = original

    # ================= 診断 =================
    win.navigate("diagnostics")
    diag = win.page("diagnostics")
    spin(app, 0.4)

    # CAN フレーム: 速度コントローラを動かして流す
    bridge.enable([1], 2)
    bridge.start_controller("velocity", {"motor": 1}, VelocityParams(velocity=1.5))
    bridge.read_all_params(1)
    diag.show_view("frames")
    spin(app, 3.0)
    save("P2_05_diag_frames", win)

    diag.show_view("stats")
    spin(app, 3.0)
    save("P2_09_diag_stats", win)
    bridge.stop_controller("velocity", {"motor": 1})
    bridge.disable([1])
    spin(app, 0.3)

    # 故障: 履歴に「発生 → 解消」を 1 組作ってから、ID2 の過温(2 回目)を出す
    bridge.inject_fault(1, "undervoltage")
    spin(app, 0.3)
    bridge.clear_fault([1])
    spin(app, 0.3)
    bridge.inject_fault(2, "overtemp")
    bridge.inject_fault(2, "overtemp")
    diag.show_view("faults")
    spin(app, 1.0)
    save("P2_03_diag_faults", win)

    diag.show_view("log")
    spin(app, 0.5)
    save("P2_06_diag_log", win)

    bridge.clear_fault([2])
    diag.show_view("faults")
    spin(app, 0.8)
    save("P2_04_diag_healthy", win)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
