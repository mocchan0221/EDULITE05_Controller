"""プラグイン基盤(phase4_5 L)のスクリーンショットと、組込み登録の置換前後の見た目の比較。

実行(フォント描画のため offscreen ではなく実ウィンドウ。実機 COM7 には接続しない):

    uv run --frozen python tools/capture_plugins.py [--out docs/design/ui_mock_v2/real]
    uv run --frozen python tools/capture_plugins.py --equiv DIR     # 組込みの部品の画像を DIR へ
    uv run --frozen python tools/capture_plugins.py --compare A B   # 2 つの DIR の画像を比較

撮るもの(`--sim` 相当: CoreBridge + SimTransport 2 台。プロジェクトの `plugins/` ではなく、
サンプル + わざと壊したプラグインを置いた一時フォルダを読む):
    L_01_settings_plugins  … 設定ページの「プラグイン」(一覧・読込エラー)
    L_02_example_page      … サンプルの情報ページ(ナビに「三角波」)
    L_03_operate_menu      … 運転ページ「その他 ▾」に「三角波」
    L_04_operate_triangle  … 三角波で運転中(LOW の速度上限内)
    L_05_settings_disabled … 無効に切り替えた直後(「再起動で反映」)

`--equiv` は置換前後の比較用: ナビ・モード選択・「その他 ▾」・各モードの説明と目標を画像にする。
`git archive` した置換前のソースを `PYTHONPATH` の先頭に置いて同じものを撮り、`--compare` で比べる。
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


def compare(a: Path, b: Path) -> int:
    from PySide6.QtGui import QImage

    names = sorted(p.name for p in a.glob("*.png"))
    bad = 0
    for n in names:
        ia, ib = QImage(str(a / n)), QImage(str(b / n))
        same = (not ib.isNull()) and ia == ib
        print(f"{'same' if same else 'DIFF'}  {n}")
        bad += 0 if same else 1
    missing = sorted(set(p.name for p in b.glob("*.png")) - set(names))
    for n in missing:
        print(f"only-in-B  {n}")
    print(f"{len(names) - bad}/{len(names)} identical")
    return 1 if bad or missing else 0


def make_window(app: QApplication, plugins_root: Path | None):
    from edulite05.app.core_bridge import CoreBridge
    from edulite05.app.main_window import MainWindow
    from edulite05.app.ui.style import theme_manager

    theme_manager().apply("dark")
    bridge = CoreBridge(kind="simulator", sim_motors=2)
    kwargs = {}
    if plugins_root is not None:
        from edulite05.app.plugin_host import create_manager

        kwargs["plugins"] = create_manager(None, project_root=plugins_root)
    win = MainWindow(bridge, settings_path=None, enable_3d=False, **kwargs)
    win.resize(*SIZE)
    win.show()
    bridge.start()
    ui = lambda mid: (bridge.states().get(mid).ui_state if bridge.states().get(mid) else "none")  # noqa: E731
    if not wait_for(app, lambda: len(bridge.motors()) == 2 and ui(1) == "disabled"):
        raise SystemExit("接続できませんでした")
    spin(app, 0.5)
    return win, bridge, ui


def equiv(app: QApplication, out: Path) -> int:
    """置換前後で見た目が同じことを確かめるための部品画像(新旧どちらのコードでも動く)。"""
    out.mkdir(parents=True, exist_ok=True)
    win, bridge, _ui = make_window(app, None)

    def save(name: str, w: QWidget) -> None:
        w.grab().save(str(out / f"{name}.png"))

    save("nav", win.nav)
    win.navigate("operate")
    spin(app, 0.3)
    insp = win.page("operate").inspector
    insp.set_motor(1)
    spin(app, 0.3)
    for mode in ("jog", "velocity", "position", "mit", "current", "test", "link"):
        m = insp.model()
        m.mode = mode
        insp._sig = ()
        insp._load_model_mode_ui()
        insp._apply()
        spin(app, 0.2)
        save(f"inspector_{mode}", insp)
    for pid in ("operate", "params", "diagnostics", "maintenance", "settings"):
        win.navigate(pid)
        spin(app, 0.4)
        save(f"nav_{pid}", win.nav)
    win.navigate("operate")
    insp._open_other_menu()
    spin(app, 0.3)
    menu = getattr(insp, "_other_menu", None)
    if menu is not None:
        save("other_menu", menu)
        menu.close()
    win.close()
    bridge.shutdown()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "design" / "ui_mock_v2" / "real"))
    ap.add_argument("--equiv", default=None, metavar="DIR")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()
    if args.compare:
        return compare(Path(args.compare[0]), Path(args.compare[1]))

    app = QApplication(sys.argv)
    if args.equiv:
        return equiv(app, Path(args.equiv))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def save(name: str, widget: QWidget) -> None:
        img = widget.grab()
        path = out / f"{name}.png"
        img.save(str(path))
        print(f"saved {path.name} ({img.width()}x{img.height()})")

    # プロジェクト相当の一時フォルダ: サンプル + わざと壊したプラグイン
    tmp = Path(tempfile.mkdtemp(prefix="edulite05_plugins_"))
    shutil.copytree(
        ROOT / "plugins" / "example_triangle", tmp / "plugins" / "example_triangle",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    bad = tmp / "plugins" / "broken_sample"
    bad.mkdir(parents=True)
    (bad / "plugin.py").write_text(
        'raise RuntimeError("依存ライブラリ foo が見つかりません")\n', encoding="utf-8"
    )

    win, bridge, ui = make_window(app, tmp)
    from edulite05.core.control.params import PARAMS_BY_CONTROLLER  # noqa: F401

    # ---- 設定: プラグイン ----
    win.navigate("settings")
    spin(app, 0.5)
    page = win.page("settings")
    page._scroll.ensureWidgetVisible(page.plugin_rows["example_triangle"])
    page._scroll.verticalScrollBar().setValue(page._scroll.verticalScrollBar().maximum())
    spin(app, 0.4)
    save("L_01_settings_plugins", win)

    # ---- サンプルの情報ページ ----
    win.navigate("example_triangle")
    spin(app, 0.5)
    save("L_02_example_page", win)

    # ---- 運転: 「その他 ▾」に三角波 ----
    win.navigate("operate")
    spin(app, 0.4)
    insp = win.page("operate").inspector
    insp.set_motor(1)
    spin(app, 0.3)
    insp._open_other_menu()
    spin(app, 0.4)
    menu = getattr(insp, "_other_menu", None)
    save("L_03_operate_menu", win)
    if menu is not None:
        save("L_03b_other_menu", menu)
        menu.close()
    spin(app, 0.2)

    # ---- 運転: 三角波(LOW の上限を超える要求 → 5 rad/s 以内) ----
    insp.pick_other("triangle")
    form = insp.forms["triangle"]
    form.set_value("amplitude", 8.0)
    form.set_value("frequency", 0.5)
    spin(app, 0.3)
    insp._do_enable()
    wait_for(app, lambda: ui(1) == "enabled")
    insp._do_start()
    wait_for(app, lambda: ui(1) == "running")
    spin(app, 5.0)
    save("L_04_operate_triangle", win)
    insp._do_stop()
    wait_for(app, lambda: ui(1) == "enabled")
    bridge.disable([1])
    wait_for(app, lambda: ui(1) == "disabled")

    # ---- 設定: 無効に切り替えた直後 ----
    win.navigate("settings")
    spin(app, 0.4)
    page.plugin_switches["example_triangle"].click()
    spin(app, 0.3)
    page._scroll.verticalScrollBar().setValue(page._scroll.verticalScrollBar().maximum())
    spin(app, 0.3)
    save("L_05_settings_disabled", win)

    win.close()
    bridge.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
