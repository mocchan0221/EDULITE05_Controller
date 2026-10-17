"""2台連動 = core の Bilateral(phase4_5.md §K・§O)。CoreBridge(ID 1 と 127)と画面部品。"""

from __future__ import annotations

import pytest

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.pages.operate.link import LinkPanel, link_assign
from edulite05.app.ui.style import theme_manager
from edulite05.core.control.params import BilateralParams

WAIT = 10000


@pytest.fixture(autouse=True)
def _theme():
    theme_manager().apply("dark")


def ui(b, mid: int) -> str:
    s = b.states().get(mid)
    return s.ui_state if s else "none"


def test_link_assign_uses_core_slot_names() -> None:
    assert link_assign(1, 127) == {"leader": 1, "follower": 127}


def test_core_bridge_starts_updates_and_stops_bilateral(qtbot) -> None:
    b = CoreBridge(kind="simulator", sim_motors=2, start_offline=True)
    try:
        b.start()
        b.connect_adapter({"adapter": "sim", "n": 2})
        qtbot.waitUntil(
            lambda: len(b.motors()) == 2 and ui(b, 1) == "disabled" and ui(b, 127) == "disabled",
            timeout=WAIT,
        )
        b.enable([1, 127], 0)  # MIT(run_mode 0)の 2 台
        qtbot.waitUntil(lambda: ui(b, 1) == "enabled" and ui(b, 127) == "enabled", timeout=WAIT)
        assign = link_assign(1, 127)
        b.start_controller("bilateral", assign, BilateralParams(), period_ms=5)
        qtbot.waitUntil(lambda: ui(b, 1) == "running" and ui(b, 127) == "running", timeout=WAIT)
        assert b.states()[1].controller == "bilateral" and b.states()[127].controller == "bilateral"
        # 運転中の設定変更(LOW の上限 kp 20 を超える剛性は SafetyGuard が制限する)
        b.update_controller_params(
            "bilateral", assign, BilateralParams(mode="master_slave", stiffness=100.0)
        )
        qtbot.waitUntil(lambda: bool(b.clamp_note(1) or b.clamp_note(127)), timeout=WAIT)
        b.stop_controller("bilateral", assign)  # どちらか 1 台で止めると 2 台とも保持へ
        qtbot.waitUntil(lambda: ui(b, 1) == "enabled" and ui(b, 127) == "enabled", timeout=WAIT)
        # 未登録のモータ ID は受け付けない
        b.start_controller("bilateral", link_assign(1, 99), BilateralParams())
        assert ui(b, 1) == "enabled"
        b.disable([1, 127])
    finally:
        b.shutdown()


def test_demo_bridge_bilateral_stand_in(qtbot) -> None:
    b = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    try:
        b.start()
        qtbot.waitUntil(lambda: len(b.motors()) == 2, timeout=WAIT)
        assign = link_assign(1, 2)
        b.start_controller("bilateral", assign, BilateralParams())  # 有効化前は受け付けない
        assert ui(b, 1) == "disabled"
        b.enable([1, 2], 0)
        b.start_controller("bilateral", assign, BilateralParams(stiffness=100.0))
        assert ui(b, 1) == "running" and ui(b, 2) == "running"
        assert b.states()[2].controller == "bilateral"
        qtbot.waitUntil(lambda: "stiffness" in b.clamp_note(1), timeout=3000)  # 上限 kp 20
        b._motors[1].theta = 1.5  # noqa: SLF001
        qtbot.waitUntil(lambda: b._motors[2].theta > 0.2, timeout=5000)  # noqa: SLF001
        b.stop_controller("bilateral", assign)
        assert ui(b, 1) == "enabled" and ui(b, 2) == "enabled"
    finally:
        b.shutdown()


def test_link_panel_settings_and_modes(qtbot) -> None:
    b = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    panel = LinkPanel(b)
    qtbot.addWidget(panel)
    got: list[BilateralParams] = []
    panel.params_changed.connect(got.append)
    panel.set_followers([2, 3], 3)
    assert panel.follower() == 3 and panel.follower_combo.count() == 2
    panel.set_followers([2], 3)  # 候補に無ければ先頭
    assert panel.follower() == 2
    assert panel.params().mode == "symmetric"
    assert panel.form.row("leader_damping").isHidden()  # 対称型では出さない
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("master_slave"))
    assert got[-1].mode == "master_slave" and not panel.form.row("leader_damping").isHidden()
    panel.offset_mode_combo.setCurrentIndex(panel.offset_mode_combo.findData("zero"))
    assert got[-1].offset_mode == "zero"
    n = len(got)
    panel.set_params(BilateralParams(stiffness=2.5))  # 読み込みでは通知しない
    assert len(got) == n and panel.params().stiffness == 2.5
    assert panel.mode_combo.currentData() == "symmetric"
    # 段階の上限(LOW: kp 20)を超える入力は上限に切る
    panel.widen_edits()
    panel.form.set_value("stiffness", 100.0)
    assert panel.params().stiffness == pytest.approx(20.0)
    b.shutdown()
