"""±4π 付近で MIT の保持が減衰のみに切り替わったときの「制限中」表示(phase4-fix-W)。"""

from __future__ import annotations

import pytest

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.i18n import tr
from edulite05.core.safety import ClampNote

WAIT = 8000


@pytest.fixture
def bridge(qtbot):
    b = CoreBridge(kind="simulator", sim_motors=1, start_offline=True)
    logs: list = []
    b.log_message.connect(logs.append)
    b.start()
    b.connect_adapter({"adapter": "sim", "n": 1})
    qtbot.waitUntil(lambda: len(b.motors()) == 1, timeout=WAIT)
    mid = b.motors()[0].id
    qtbot.waitUntil(
        lambda: b.states().get(mid) is not None and b.states()[mid].ui_state == "disabled",
        timeout=WAIT,
    )
    yield b, mid, logs
    b.shutdown()


def test_i18n_key_exists_and_is_short() -> None:
    text = tr("core.clamp_position_wrap")
    assert "4π" in text and "減衰" in text and len(text) <= 40


def test_clamp_note_text_for_position_wrap(bridge) -> None:
    b, mid, logs = bridge
    b._on_clamped({"motor_id": mid, "notes": [ClampNote("kp", 5.0, 0.0, "position_wrap")]})
    note = b.clamp_note(mid)
    assert tr("core.clamp_position_wrap") in note and "kp" in note
    assert any(tr("core.clamp_position_wrap") in e.text for e in logs)


def test_hold_beyond_4pi_shows_clamp_note_end_to_end(qtbot, bridge) -> None:
    b, mid, _ = bridge
    conn = b._conn
    conn.transport.motor(mid).position = 15.0  # ±4π の外
    conn.transport.motor(mid)._sync_loc_ref()
    qtbot.waitUntil(lambda: abs(b.states()[mid].position_multi) < 100, timeout=WAIT)
    b.enable([mid], 0)  # 運控(MIT)。有効 = 保持(HoldCmd)が流れる
    qtbot.waitUntil(lambda: b.states()[mid].ui_state == "enabled", timeout=WAIT)
    qtbot.waitUntil(lambda: tr("core.clamp_position_wrap") in b.clamp_note(mid), timeout=WAIT)
    assert b.states()[mid].position_multi == pytest.approx(15.0, abs=0.1)  # mechPos に較正済み
    b.disable([mid])
