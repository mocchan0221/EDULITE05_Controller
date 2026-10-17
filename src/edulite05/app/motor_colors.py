"""モータの色の割り当て(ui_design.md §7.2)。Qt を使わない。

色は「モータそのもの」に付く: セッション内で **最初に登録された順** に色の枠(slot)を 0, 1, 2, … と
割り当てる。CAN ID の値(既定 ID 1 とシミュレータの 127 など)では決めない。

- 一度割り当てた枠は、他のモータが増減しても変えない(生き残った側を塗り替えない)
- `rename`(CAN ID の変更)では枠を引き継ぐ
- 枠は切断(`reset`)まで再利用しない。パレットは 3 色なので、4 台目以降だけ枠 % 3 で循環する
  (`Tokens.motor_color` が循環する)
"""

from __future__ import annotations

from collections.abc import Iterable

PALETTE_SIZE = 3  # tokens.Tokens.motor の長さ(§7.2)


def fallback_index(motor_id: int) -> int:
    """割り当て表を持たない場面(ギャラリー・単体テスト)用の ID 順の色。"""
    return (motor_id - 1) % PALETTE_SIZE


class MotorColorRegistry:
    """`register(ids)` で初出の ID に枠を振り、`slot(id)` で引く。"""

    def __init__(self) -> None:
        self._slots: dict[int, int] = {}
        self._next = 0

    def register(self, ids: Iterable[int]) -> None:
        """初出の ID に、渡された順で次の枠を割り当てる(既知の ID は変えない)。"""
        for mid in ids:
            if mid not in self._slots:
                self._slots[mid] = self._next
                self._next += 1

    def slot(self, motor_id: int) -> int:
        """色の枠(0 始まり)。未登録なら、いま登録する。"""
        if motor_id not in self._slots:
            self.register([motor_id])
        return self._slots[motor_id]

    def rename(self, old: int, new: int) -> None:
        """CAN ID の付け替え。枠は引き継ぐ(新 ID が既知ならそのまま)。"""
        if old == new or old not in self._slots:
            return
        slot = self._slots.pop(old)
        self._slots.setdefault(new, slot)

    def reset(self) -> None:
        """新しいセッション(切断)。枠をすべて空ける。"""
        self._slots.clear()
        self._next = 0

    def known(self) -> dict[int, int]:
        return dict(self._slots)


__all__ = ["PALETTE_SIZE", "MotorColorRegistry", "fallback_index"]
