"""Bridge 経由のモータ色(ui_design.md §7.2): ID 127 と 1 が別の色、CAN ID 変更でも変わらない。"""

from __future__ import annotations

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.ui.style import theme_manager, tokens

WAIT = 8000


def test_core_bridge_sim_ids_127_and_1_have_different_colors(qtbot) -> None:
    theme_manager().apply("dark")
    b = CoreBridge(kind="simulator", sim_motors=2, start_offline=True)
    try:
        b.start()
        b.connect_adapter({"adapter": "sim", "n": 2})
        qtbot.waitUntil(lambda: len(b.motors()) == 2, timeout=WAIT)
        infos = {m.id: m for m in b.motors()}
        assert set(infos) == {1, 127}
        t = tokens()
        assert t.motor_color(infos[1].color_index) != t.motor_color(infos[127].color_index)
        assert b.motor_color_index(1) == infos[1].color_index
        before = {m.id: t.motor_color(m.color_index) for m in b.motors()}
        # CAN ID を変えても、そのモータの色は変わらない(もう 1 台も塗り替えない)
        b.run_tool("set_can_id", 1, {"new_id": 5})
        qtbot.waitUntil(lambda: sorted(m.id for m in b.motors()) == [5, 127], timeout=WAIT)
        after = {m.id: t.motor_color(m.color_index) for m in b.motors()}
        assert after[5] == before[1] and after[127] == before[127]
        # 切断で枠を空ける(新しいセッション)
        b.disconnect_adapter()
        assert b.colors.known() == {}
    finally:
        b.shutdown()


def test_demo_bridge_motors_have_distinct_colors(qtbot) -> None:
    b = DemoBridge(kind="simulator", demo_motion=False, seed=0)
    try:
        b.start()
        qtbot.waitUntil(lambda: len(b.motors()) == 2, timeout=WAIT)
        a, c = b.motors()
        assert a.color_index != c.color_index
    finally:
        b.shutdown()
