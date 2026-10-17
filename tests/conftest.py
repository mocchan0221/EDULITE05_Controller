"""GUI テストは offscreen で実行する(pytest-qt)。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import pytest  # noqa: E402


@pytest.fixture
def english():
    """UI 文字列を英語にして、終わったら日本語へ戻す(言語はモジュール単位の状態のため)。"""
    from edulite05.app import i18n

    i18n.load_catalog("en")
    yield
    i18n.load_catalog("ja")


@pytest.fixture(scope="session")
def qapp(qapp):
    """pytest-qt の QApplication を「最後のウィンドウを閉じても quit しない」にする。

    `main()`(`--lang` のテスト)は `app.exec()` を抜けた後、`MainWindow.close()` で最後のウィンドウを
    閉じると `quitOnLastWindowClosed` が quit() を呼び、イベントループのない状態で「終了要求」が残る。
    すると以後の `QEventLoop.exec()` が即座に戻り、`qtbot.wait` / `waitUntil` が 0 ms の空回りになって
    (QTimer が一度も発火せず)タイマ駆動の CoreBridge / DemoBridge のテストが接続待ちで時間切れになった
    (test_language_en → test_link_bilateral の順序依存)。テストでは実行中のアプリを終了させたくない。
    """
    qapp.setQuitOnLastWindowClosed(False)
    load_system_fonts()
    return qapp


# offscreen プラットフォーム(Windows)にはフォントが 1 つも無く、全文字が 1 em 幅の代替フォントで
# 測られる(28 px の読み値 "+0.000" が 99 px でなく 168 px)。これだと最小幅が実機より 300 px ほど
# 大きく出て、「1280 に収まる」検査が意味を持たない。OS のフォントを読み込んで実機に近づける。
_SYSTEM_FONT_FILES = (
    "YuGothM.ttc", "YuGothB.ttc", "meiryo.ttc", "meiryob.ttc", "consola.ttf", "consolab.ttf",
    "segoeui.ttf", "seguisb.ttf",
)


def load_system_fonts() -> bool:
    """Windows のフォントを QFontDatabase に足す。UI 用フォントが使えれば True(他 OS では False)。"""
    import os
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in _SYSTEM_FONT_FILES:
        path = fonts_dir / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    return "Yu Gothic UI" in QFontDatabase.families()


def _top_levels() -> list:
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        return []
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return list(app.topLevelWidgets()) if app is not None else []


@pytest.fixture(autouse=True)
def _flush_deferred_deletes():
    """各テストの後始末で、テスト中に作られたトップレベルのウィジェットを実際に破棄する。

    pytest-qt の `qtbot.addWidget` は終了時に `close()` + `deleteLater()` するが、テストにはイベント
    ループがなく `processEvents()` は DeferredDelete を配送しないため、MainWindow(約 1300 ウィジェット)
    が次の実モーダルまで溜まり続けた。溜まった分だけ `theme_manager().apply()`(全ウィジェットへ QSS を
    再適用)が線形に遅くなり、保守ページのテストが 2 秒 → 35 秒に伸びて「止まる」ように見えていた。
    加えて、親なしのまま Python 側に参照が残るウィジェット(QMenu / ParamForm 等)がテストごとに
    約 30 個残るので、テスト中に増えたトップレベルは明示的に deleteLater する。
    autouse なので qtbot より先にセットアップされ、qtbot の後始末の後(= 最後)にここが走る。
    """
    before = {id(w) for w in _top_levels()}
    yield
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    # close() はしない(closeEvent が二重に走る)。持ち主(ウィンドウ)を先に破棄し、持ち主の消えた
    # 親なしの部品(QMenu / ParamForm 等)を次に破棄する。順序を逆にすると持ち主が破棄済みの部品を
    # 触って落ちる(access violation)
    from PySide6.QtWidgets import QMainWindow

    for owners_only in (True, False):
        for w in _top_levels():
            if id(w) in before or w.parent() is not None:
                continue
            if owners_only and not isinstance(w, QMainWindow):
                continue
            w.deleteLater()
        for _ in range(3):  # 破棄が別の deleteLater を呼ぶ入れ子の分
            gc.collect()
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


_OWN_THREAD_PREFIXES = ("edulite05", "sim-")  # コア・シミュレータ・ブリッジのスレッド名(daemon でも検査する)


@pytest.fixture(autouse=True)
def _no_leaked_threads():
    """テストが起こしたスレッドが、テスト終了時に残っていたら失敗にする(後始末漏れの検出)。

    対象は非 daemon スレッドと、本アプリのスレッド(`edulite05-*` / `sim-*`)。CoreBridge.shutdown /
    MotorService.close / Session.close の呼び忘れが後続のテストを遅く・不安定にするのを防ぐ。
    停止が非同期のものがあるので、最大 5 秒は終了を待つ。
    """
    import threading
    import time

    before = set(threading.enumerate())
    yield

    def leaked() -> list[threading.Thread]:
        return [
            t
            for t in threading.enumerate()
            if t not in before
            and t.is_alive()
            and (not t.daemon or t.name.startswith(_OWN_THREAD_PREFIXES))
        ]

    deadline = time.monotonic() + 5.0
    while leaked() and time.monotonic() < deadline:
        time.sleep(0.05)
    left = leaked()
    assert not left, f"テストが終わっても残っているスレッド: {[t.name for t in left]}"


@pytest.fixture(scope="session")
def real_fonts(qapp) -> bool:
    """UI 用フォント(Yu Gothic UI)が使えるか。幅を測るテストはこれが無いと意味がないので skip する。"""
    from PySide6.QtGui import QFontDatabase

    return "Yu Gothic UI" in QFontDatabase.families()


@pytest.fixture(autouse=True)
def _restore_operate_mode_tables():
    """運転ページのモード対応表(`modes.install()` が書き換えるモジュール変数)をテストごとに戻す。

    プラグイン(example_triangle 等)入りの MainWindow を作ると install() が TARGET_FIELDS などへ
    プラグインのモードを足し、ウィンドウを閉じて PARAMS_BY_CONTROLLER から消えた後も残るため、
    後続の `test_modes_module_mapping` 等が実行順で失敗した。
    """
    import sys

    md = sys.modules.get("edulite05.app.pages.operate.modes")
    if md is None:
        yield
        return
    names = ("TARGET_FIELDS", "LIMIT_FIELDS", "LABEL_OVERRIDES", "MODE_SPECS")
    saved = {n: dict(getattr(md, n)) for n in names}
    seqs = {n: getattr(md, n) for n in ("MAIN_MODES", "OTHER_MODES", "ALL_MODES")}
    yield
    for n, v in saved.items():
        d = getattr(md, n)
        d.clear()
        d.update(v)
    for n, v in seqs.items():
        setattr(md, n, v)
