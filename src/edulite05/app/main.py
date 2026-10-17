"""GUI エントリポイント。

共有フォルダ(UNC)上の venv では import が遅い(pyqtgraph 約 13 秒、pyvista 込みで 40〜60 秒)ため、

1. まず小さな「読み込み中」ウィンドウを出す
2. 重いモジュール(pyqtgraph・各ページ)を別スレッドで import する
3. 読み込み完了後にメインウィンドウを表示する
4. pyvista / pyvistaqt はさらにその後、別スレッドで先読みし、終わったら 3D ビューへ切替える

という順に進める。`EDULITE05_AUTOCLOSE=1` のときはウィンドウ表示 2 秒後に自動終了する(起動確認用)。
`--gallery` は部品ギャラリー(`app/gallery.py`)だけを開く。

接続先(Bridge)の選び方:
- 指定なし … `CoreBridge`(本物のコア)。未接続で起動し、接続フォームで COM ポートを選ぶ(CH340 既定)
- `--sim [N]` … `CoreBridge` + シミュレータ(既定 2 台。ID は 127 と 1)。起動時に自動接続する
- `--demo` … `DemoBridge`(ダミーデータ。UI 開発用)。`--sim` を併用すると表示が「シミュレータ」になる
`--start-offline` は未接続から始める(どちらの Bridge でも)。

表示言語は `--lang ja|en` > 環境変数 `EDULITE05_LANG` > 設定ページで保存した値(`prefs/language`)
> ja の順で決める(`app/i18n`)。設定ページで選んだ言語は次回の起動から反映する。
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import threading
import time
import traceback
from pathlib import Path


def _parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    ap = argparse.ArgumentParser(prog="edulite05", add_help=True)
    ap.add_argument("--sim", nargs="?", const=2, default=None, type=int, metavar="N",
                    help="シミュレータ(本物のコア + SimTransport。N 台、既定 2 台: ID 127 と 1)")
    ap.add_argument("--demo", action="store_true",
                    help="DemoBridge(ダミーデータ。UI 開発用。実機・シミュレータにはつながない)")
    ap.add_argument("--no-3d", action="store_true", help="3D ビューを使わず 2D ダイヤルのみ")
    ap.add_argument("--no-demo", action="store_true",
                    help="(--demo のとき)起動時のデモ動作(モータ 1)を無効化")
    ap.add_argument("--start-offline", action="store_true",
                    help="未接続から始める(接続 → 自動スキャンの流れを確認できる)")
    ap.add_argument("--reset-layout", action="store_true",
                    help="保存済みのウィンドウ配置を使わない")
    ap.add_argument("--gallery", action="store_true", help="部品ギャラリーを開く")
    ap.add_argument("--lang", type=_lang_arg, default=None, metavar="LANG",
                    help="表示言語 ja | en(環境変数 EDULITE05_LANG でも可。設定ページの値より優先)")
    return ap.parse_known_args(argv)


def _lang_arg(text: str) -> str:
    from edulite05.app.i18n import SUPPORTED, normalize_language

    lang = normalize_language(text)
    if lang is None:
        raise argparse.ArgumentTypeError(f"{text!r}(対応: {', '.join(SUPPORTED)})")
    return lang


def saved_language(settings_path: Path | None) -> str | None:
    """設定ファイル(`user_data/ui_state.ini`)に保存した表示言語。無ければ None。"""
    if settings_path is None or not settings_path.is_file():
        return None
    from PySide6.QtCore import QSettings

    value = QSettings(str(settings_path), QSettings.Format.IniFormat).value("prefs/language")
    return None if value is None else str(value)


def _make_bridge(args: argparse.Namespace):  # noqa: ANN202
    """起動オプションから Bridge を作る(`--demo` なら DemoBridge、それ以外は CoreBridge)。"""
    if args.demo:
        from edulite05.app.demo_bridge import DemoBridge

        return DemoBridge(
            kind="simulator" if args.sim is not None else "mock",
            demo_motion=not args.no_demo,
            start_offline=args.start_offline,
        )
    from edulite05.app.core_bridge import CoreBridge

    return CoreBridge(
        kind="simulator" if args.sim is not None else "hardware",
        sim_motors=args.sim if args.sim is not None else 2,
        start_offline=args.start_offline,
    )


def main(argv: list[str] | None = None) -> int:
    t_start = time.perf_counter()
    raw = sys.argv[1:] if argv is None else argv
    args, rest = _parse(raw)

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QProgressBar, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([sys.argv[0], *rest])

    # 表示言語(ウィンドウを作る前に決める。実行中は切り替えない)
    from edulite05.app import i18n

    settings_file = Path(__file__).resolve().parents[3] / "user_data" / "ui_state.ini"
    use_saved = os.environ.get("EDULITE05_NO_STATE") != "1"
    i18n.load_catalog(
        i18n.resolve_language(args.lang, saved_language(settings_file) if use_saved else None)
    )

    from edulite05.app.i18n import tr
    from edulite05.app.ui.label import Label
    from edulite05.app.ui.style import theme_manager
    from edulite05.app.ui.tokens import SP_RELATED, SP_SECTION
    from edulite05.app.ui.util import styled_background

    theme_manager().apply("dark")

    if args.gallery:
        from edulite05.app.gallery import GalleryWindow

        gallery = GalleryWindow()
        gallery.show()
        if os.environ.get("EDULITE05_AUTOCLOSE") == "1":
            QTimer.singleShot(2000, app.quit)
        return app.exec()

    # ---- 読み込み中ウィンドウ ----
    splash = QWidget()
    styled_background(splash, "layer")
    splash.setWindowTitle(tr("app.title"))
    splash.resize(520, 170)
    lay = QVBoxLayout(splash)
    lay.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, SP_SECTION)
    lay.setSpacing(SP_RELATED)
    lay.addWidget(Label(tr("app.title"), "title"))
    lay.addWidget(Label(tr("app.loading"), "secondary", wrap=True))
    lay.addStretch(1)
    bar = QProgressBar()
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    lay.addWidget(bar)
    splash.show()

    # ---- 重いモジュールを別スレッドで import ----
    result: dict[str, object] = {}

    def _load() -> None:
        try:
            importlib.import_module("edulite05.app.main_window")
            # 運転ページ(pyqtgraph。共有フォルダ上で約 13 秒)もここで読み、構築を待たせない
            importlib.import_module("edulite05.app.pages.operate")
            importlib.import_module("edulite05.app.demo_bridge")
            importlib.import_module("edulite05.app.core_bridge")
        except Exception:  # noqa: BLE001
            result["error"] = traceback.format_exc()
        result["done"] = True

    threading.Thread(target=_load, name="import-gui", daemon=True).start()

    state: dict[str, object] = {}

    def _poll() -> None:
        if not result.get("done"):
            return
        poll.stop()
        if "error" in result:
            sys.stderr.write(str(result["error"]))
            app.exit(1)
            return
        from edulite05.app.main_window import MainWindow, default_settings_path, project_root
        from edulite05.app.plugin_host import create_manager

        bridge = _make_bridge(args)
        settings = None
        if not args.reset_layout and os.environ.get("EDULITE05_NO_STATE") != "1":
            settings = default_settings_path()
        # プラグイン: 組込み + plugins/<name>/plugin.py + entry point
        # (読込失敗はそのプラグインだけ無効)
        plugins = create_manager(settings, project_root=project_root())
        win = MainWindow(bridge, settings_path=settings, enable_3d=not args.no_3d, plugins=plugins)
        state["win"] = win
        win.show()
        splash.close()
        bridge.start()
        if os.environ.get("EDULITE05_TIMING") == "1":
            elapsed = time.perf_counter() - t_start
            sys.stderr.write(f"[startup] main window shown after {elapsed:.1f} s\n")
        if os.environ.get("EDULITE05_AUTOCLOSE") == "1":
            QTimer.singleShot(2000, app.quit)
        else:  # --no-3d でも呼ぶ(3D ビューの説明文を「読み込み中」にしないため)
            QTimer.singleShot(800, win.start_3d_preload)

    poll = QTimer()
    poll.setTimerType(Qt.TimerType.CoarseTimer)
    poll.setInterval(50)
    poll.timeout.connect(_poll)
    poll.start()
    code = app.exec()
    win = state.get("win")
    if win is not None:
        shutdown = getattr(getattr(win, "bridge", None), "shutdown", None)
        if callable(shutdown):
            shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
