"""診断ページ(ui_design.md §5.3)のテスト(offscreen)。

故障(カード・正常 1 行・履歴・未確認の赤い点の元)、イベントログ、CAN フレーム、通信統計。
"""

from __future__ import annotations

import csv
import time
from types import SimpleNamespace

import pytest

from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.pages.diagnostics import DiagnosticsPage
from edulite05.app.pages.diagnostics import frames_view as fv
from edulite05.app.pages.diagnostics.models import comm_type, motor_of
from edulite05.app.ui import Button, Label
from edulite05.app.ui.style import theme_manager
from edulite05.app.viewmodels import BusStats, FrameEntry, LogEntry


@pytest.fixture
def env(qtbot):
    theme_manager().apply("dark")
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    page = DiagnosticsPage(bridge)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    bridge.start()
    yield page, bridge
    bridge.shutdown()


def _frame(direction: str, ctype: int, motor: int, data: bytes = b"\x00" * 8, text: str = ""):
    host = 0xFD
    can_id = (ctype << 24) | (host << 8) | motor if direction == "TX" else (
        (ctype << 24) | (motor << 8) | host
    )
    return FrameEntry(time.time(), direction, can_id, data, text)


# ---------------------------------------------------------------- 構成
def test_structure_and_segment(env) -> None:
    page, _b = env
    assert page.seg.keys() == ["faults", "log", "frames", "stats"]
    assert [page.seg.button(k).text() for k in page.seg.keys()] == [
        tr("diag.view_faults"), tr("diag.view_log"), tr("diag.view_frames"), tr("diag.view_stats"),
    ]
    assert page.current_view() == "faults" and page.stack.currentWidget() is page.faults_view
    page.show_view("frames")
    assert page.stack.currentWidget() is page.frames_view
    page.show_view("stats")
    assert page.stack.currentWidget() is page.stats_view
    page.show_view("nope")  # 知らないビューは無視
    assert page.current_view() == "stats"


# ---------------------------------------------------------------- 故障
def test_healthy_is_a_single_line(env) -> None:
    page, _b = env
    fvw = page.faults_view
    assert fvw.card_count() == 0
    labels = [lb for lb in fvw.summary.findChildren(Label) if lb.text()]
    assert [lb.text() for lb in labels] == [tr("diag.no_fault")]
    assert fvw.history_rows() == 0 and fvw.history_empty.isVisibleTo(fvw)


def test_fault_card_content_and_reset(env) -> None:
    page, bridge = env
    bridge.inject_fault(2, "overtemp")
    fvw = page.faults_view
    assert fvw.active_faults() == {(2, "overtemp")} and fvw.card_count() == 1
    text = " ".join(lb.text() for lb in fvw.summary.findChildren(Label))
    d = bridge.fault_details(2)[0]
    assert d.name in text and d.description in text and d.remedy in text
    assert "138.2" in text and "135.0" in text  # 測定値と閾値
    for cause in d.causes:
        assert cause in text
    assert tr("diag.no_fault") not in text
    # モータごとに [故障をリセット] が 1 つ。強調色
    btns = fvw.summary.findChildren(Button, "FaultResetButton")
    assert len(btns) == 1 and btns[0].text() == tr("diag.fault_reset")
    assert btns[0].variant == "accent"
    # 履歴に「発生」
    assert fvw.history_rows() == 1
    assert fvw.history.item(0, 3).text() == tr("diag.event_occurred")
    assert fvw.history.item(0, 1).text() == tr("ui.motor_id", id=2)
    # リセット → 正常に戻り、履歴に「解消」
    btns[0].click()
    assert fvw.card_count() == 0 and fvw.active_faults() == set()
    assert fvw.history_rows() == 2 and fvw.history.item(0, 3).text() == tr("diag.event_cleared")
    assert bridge.fault_details(2) == []


def test_repeated_fault_is_counted_not_duplicated(env) -> None:
    page, bridge = env
    bridge.inject_fault(1, "overcurrent")
    bridge.inject_fault(1, "overcurrent")
    bridge.inject_fault(1, "overcurrent")
    fvw = page.faults_view
    fvw._poll()  # 0.5 s ごとの確認を待たずに呼ぶ
    assert fvw.card_count() == 1 and fvw.history_rows() == 1
    text = " ".join(lb.text() for lb in fvw.summary.findChildren(Label))
    assert tr("diag.occurrences", n=3) in text
    # 2 種類の故障 → カード 2 枚
    bridge.inject_fault(1, "encoder")
    assert fvw.card_count() == 2
    assert page.seg.button("faults").text() == tr("diag.view_faults_n", n=2)


def test_unseen_dot_logic(env) -> None:
    page, bridge = env
    changes: list[bool] = []
    page.unseen_changed.connect(changes.append)
    assert page.has_unseen_faults() is False
    # ページを開いていない間に故障 → 未確認
    bridge.inject_fault(2, "overtemp")
    assert page.has_unseen_faults() is True and changes == [True]
    # ページを開く(故障ビューが出る)と確認済み
    page.show_view("log")
    page.activated()
    assert page.current_view() == "faults"  # 未確認の故障があれば故障ビューから見せる
    assert page.has_unseen_faults() is False and changes == [True, False]
    # 見ている間に増えた故障も確認済み
    bridge.inject_fault(1, "overload")
    assert page.has_unseen_faults() is False
    # 別のビューを見ているときの故障は未確認。故障ビューに切り替えると確認済み
    page.show_view("log")
    bridge.inject_fault(1, "undervoltage")
    assert page.has_unseen_faults() is True and changes[-1] is True
    page.show_view("faults")
    assert page.has_unseen_faults() is False
    # 解消すると記録も消え、再発で再び未確認
    page.deactivated()
    bridge.clear_fault([1, 2])
    assert page.has_unseen_faults() is False
    bridge.inject_fault(2, "overtemp")
    assert page.has_unseen_faults() is True


def test_offline_shows_no_motor_line(qtbot) -> None:
    bridge = DemoBridge(kind="simulator", demo_motion=False, seed=0, start_offline=True)
    page = DiagnosticsPage(bridge)
    qtbot.addWidget(page)
    page.show()
    bridge.start()
    texts = [lb.text() for lb in page.faults_view.summary.findChildren(Label)]
    assert tr("diag.no_motor") in texts
    bridge.shutdown()


# ---------------------------------------------------------------- イベントログ
def test_event_log_filter_search_clear_export(env, tmp_path) -> None:
    page, bridge = env
    page.show_view("log")
    lv = page.log_view
    t = time.time()
    for i, (lvl, text) in enumerate([
        ("debug", "内部の詳細"), ("info", "接続しました"), ("warn", "温度が高めです"),
        ("error", "通信エラー"), ("info", "速度モードで運転開始"),
    ]):
        bridge.log_message.emit(LogEntry(t + i, lvl, text))
    lv.flush()
    n_info = lv.row_count()
    texts = [lv.model.row_at(i).text for i in range(n_info)]
    assert "内部の詳細" not in texts  # 既定は「情報以上」
    assert texts[-4:] == ["接続しました", "温度が高めです", "通信エラー", "速度モードで運転開始"]
    # レベル: 警告以上
    lv.level_combo.setCurrentIndex(lv.level_combo.findData("warn"))
    assert [lv.model.row_at(i).text for i in range(lv.row_count())][-2:] == [
        "温度が高めです", "通信エラー"]
    assert all(lv.model.row_at(i).level in ("warn", "error") for i in range(lv.row_count()))
    # デバッグ以上 + 検索
    lv.level_combo.setCurrentIndex(lv.level_combo.findData("debug"))
    lv.search.setText("運転")
    assert [lv.model.row_at(i).text for i in range(lv.row_count())] == ["速度モードで運転開始"]
    # 書き出し(フィルタ後)
    out = tmp_path / "log.csv"
    assert lv.export_csv(out) == 1
    rows = list(csv.reader(out.open(encoding="utf-8-sig")))
    assert rows[0] == [tr("diag.col_time"), tr("diag.col_level"), tr("diag.col_message")]
    assert rows[1][1:] == ["info", "速度モードで運転開始"]
    # クリア
    lv.search.setText("")
    lv.clear()
    assert lv.row_count() == 0


def test_event_log_keeps_collecting_while_hidden(env) -> None:
    page, bridge = env
    # ログビューは非表示(故障ビューが表示中)でも溜め、開いたときに出す
    for i in range(20):
        bridge.log_message.emit(LogEntry(time.time(), "info", f"msg{i}"))
    assert page.log_view.row_count() == 0
    page.show_view("log")
    texts = [page.log_view.model.row_at(i).text for i in range(page.log_view.row_count())]
    assert texts[-20:] == [f"msg{i}" for i in range(20)]


def test_event_log_ring_buffer_cap(env) -> None:
    page, bridge = env
    page.show_view("log")
    lv = page.log_view
    from edulite05.app.pages.diagnostics.log_view import MAX_ENTRIES

    for i in range(MAX_ENTRIES + 300):
        bridge.log_message.emit(LogEntry(time.time(), "info", f"m{i}"))
    lv.flush()
    assert lv.row_count() == MAX_ENTRIES
    assert lv.model.row_at(lv.row_count() - 1).text == f"m{MAX_ENTRIES + 299}"


# ---------------------------------------------------------------- CAN フレーム
def test_frames_filters_pause_and_decode(env) -> None:
    page, bridge = env
    page.show_view("frames")
    frv = page.frames_view
    bridge.frame_logged.emit(_frame("TX", 18, 1, text="書込"))
    bridge.frame_logged.emit(_frame("RX", 2, 1, text="FB"))
    bridge.frame_logged.emit(_frame("RX", 2, 2, text="FB"))
    bridge.frame_logged.emit(_frame("TX", 17, 2, bytes(range(8)), text="読出"))
    frv.flush()
    assert frv.row_count() >= 4
    m = frv.model
    last = m.row_at(m.rowCount() - 1)
    assert comm_type(last) == 17 and motor_of(last) == 2
    # セルの表示(16 進・ID)
    idx = m.index(m.rowCount() - 1, 5)
    assert m.data(idx) == "00 01 02 03 04 05 06 07"
    assert m.data(m.index(m.rowCount() - 1, 2)) == f"0x{last.can_id:08X}"
    assert m.data(m.index(m.rowCount() - 1, 6)) == "読出"
    # フィルタ: type 2 のみ
    frv.type_combo.setCurrentIndex(frv.type_combo.findData(2))
    assert frv.row_count() == 2 and all(comm_type(m.row_at(i)) == 2 for i in range(2))
    # + モータ 2
    frv.motor_combo.setCurrentIndex(frv.motor_combo.findData(2))
    assert frv.row_count() == 1 and motor_of(m.row_at(0)) == 2
    # + 方向 TX → 0 件
    frv.dir_combo.setCurrentIndex(frv.dir_combo.findData("TX"))
    assert frv.row_count() == 0
    for c in (frv.type_combo, frv.motor_combo, frv.dir_combo):
        c.setCurrentIndex(0)
    n = frv.row_count()
    # 一時停止: 到着は溜めるが表には出ない。再開で追いつく
    frv.pause_btn.click()
    assert frv.is_paused() and frv.pause_btn.text() == tr("frames.resume")
    for _ in range(5):
        bridge.frame_logged.emit(_frame("RX", 2, 1))
    frv.flush()
    assert frv.row_count() == n and frv.pending_count() == 5
    assert tr("diag.paused_note", n=5) in frv.count_label.text()
    frv.pause_btn.click()
    assert not frv.is_paused() and frv.row_count() == n + 5 and frv.pending_count() == 0
    frv.clear()
    assert frv.row_count() == 0


def test_frames_ring_buffer(env) -> None:
    page, bridge = env
    page.show_view("frames")
    frv = page.frames_view
    for _ in range(fv.MAX_ROWS + 500):
        bridge.frame_logged.emit(_frame("RX", 2, 1))
    frv.flush()
    assert frv.row_count() == fv.MAX_ROWS


def test_frames_from_demo_bridge_show_up(env, qtbot) -> None:
    page, bridge = env
    page.show_view("frames")
    bridge.read_all_params(1)  # デモがフレームを流す
    qtbot.waitUntil(lambda: page.frames_view.row_count() > 0, timeout=3000)


# ---------------------------------------------------------------- 通信統計
def test_stats_view(env) -> None:
    page, bridge = env
    sv = page.stats_view
    assert sv.tx.value_label.text() == "—"
    bridge.stats_updated.emit(BusStats(tx_fps=400.0, rx_fps=398.0, loop_hz=200.0, jitter_ms=0.31))
    assert sv.tx.value_label.text() == "400" and sv.rx.value_label.text() == "398"
    assert sv.loop.value_label.text() == "200" and sv.jitter.value_label.text() == "0.31"
    # パース失敗・タイムアウトは BusStats の累計件数(0 件なら 0 と出す)
    assert sv.parse.value_label.text() == "0" and sv.timeout.value_label.text() == "0"
    assert sv.parse.note.text() == ""
    bridge.stats_updated.emit(BusStats(parse_errors=2, timeouts=7))
    assert sv.parse.value_label.text() == "2" and sv.timeout.value_label.text() == "7"
    # 再送・フレーム表示の間引き(CoreBridge)。再送を計測しない Bridge(None)は「計測していません」
    assert sv.retries.value_label.text() == "—"
    assert sv.retries.note.text() == tr("diag.stat_unmeasured")
    assert sv.dropped.value_label.text() == "0" and sv.dropped.note.text() == ""
    bridge.stats_updated.emit(BusStats(retries=3, frames_dropped=120))
    assert sv.retries.value_label.text() == "3" and sv.dropped.value_label.text() == "120"
    assert sv.dropped.note.text() == tr("diag.stat_dropped_note")
    # 計測していない Bridge(値が None)は「計測していません」と言う(0 と偽らない)
    bridge.stats_updated.emit(SimpleNamespace(
        tx_fps=0.0, rx_fps=0.0, loop_hz=0.0, jitter_ms=0.0, parse_errors=None, timeouts=None
    ))
    assert sv.parse.value_label.text() == "—"
    assert sv.parse.note.text() == tr("diag.stat_unmeasured")
    for i in range(70):
        bridge.stats_updated.emit(BusStats(tx_fps=float(i), rx_fps=1.0, loop_hz=1.0, jitter_ms=1.0))
    assert len(sv.tx.history) == 60  # 直近のみ保持
