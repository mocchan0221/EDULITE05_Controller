"""モータ色の割り当て(ui_design.md §7.2): 初登録順で固定、ID の値では決めない。"""

from __future__ import annotations

from edulite05.app.motor_colors import MotorColorRegistry, fallback_index


def test_ids_127_and_1_get_different_slots() -> None:
    """既定 ID 1 とシミュレータの 127 が同じ色になっていた不具合(旧: (id-1) % 3 で 0 と 0)。"""
    r = MotorColorRegistry()
    r.register([127, 1])
    assert r.slot(127) == 0 and r.slot(1) == 1
    assert r.slot(127) % 3 != r.slot(1) % 3
    assert fallback_index(1) == fallback_index(127)  # 旧方式では衝突していた


def test_survivor_is_not_repainted_and_slots_are_not_reused() -> None:
    r = MotorColorRegistry()
    r.register([5, 9])
    assert (r.slot(5), r.slot(9)) == (0, 1)
    r.register([9])  # 5 が消えて再スキャン: 9 の色は変わらない
    assert r.slot(9) == 1
    r.register([9, 3])  # 新顔は次の枠(消えた 5 の枠は再利用しない)
    assert r.slot(3) == 2
    assert r.slot(9) == 1


def test_rename_keeps_slot() -> None:
    r = MotorColorRegistry()
    r.register([127, 1])
    r.rename(1, 5)
    assert r.slot(5) == 1 and 1 not in r.known()
    assert r.slot(127) == 0


def test_reset_starts_a_new_session() -> None:
    r = MotorColorRegistry()
    r.register([127, 1])
    r.reset()
    r.register([1, 127])
    assert (r.slot(1), r.slot(127)) == (0, 1)


def test_unknown_id_is_registered_on_demand_and_wraps_beyond_palette() -> None:
    r = MotorColorRegistry()
    slots = [r.slot(i) for i in (10, 20, 30, 40)]
    assert slots == [0, 1, 2, 3]  # 4 台目以降だけ Tokens.motor_color が % 3 で循環する
