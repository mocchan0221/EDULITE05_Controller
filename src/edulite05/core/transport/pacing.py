"""送信ペーシング(連続するフレーム書込みの最小間隔の強制)。

公式 USB-CAN + EL05 の実測(docs/research/hw_bench.md「送信間隔と取りこぼし」、
el05_protocol.md §8.0)で、フレーム間隔が約 0.5 ms 未満だと取りこぼし、1 ms 以上なら
取りこぼさないことが分かっている(1 write に連結しても同じ)。そのため送信側で最小間隔を空ける。

`FramePacer` 自体はロックを持たない。呼び出し側が TX ロックの下で `wait()` → 書込み →
`mark()` の順に使う(スレッド安全性は呼び出し側のロックが担保する)。
"""

from __future__ import annotations

import time

from edulite05.core import clock

# 待ち時間のうち、この長さは sleep せずスピンで待つ(Windows の sleep 分解能対策)
_SPIN_S = 0.0015


class FramePacer:
    """直前の書込み完了時刻から `gap_s` 秒が経つまで `wait()` で待たせる。"""

    __slots__ = ("gap_s", "_last")

    def __init__(self, gap_s: float) -> None:
        if gap_s < 0:
            raise ValueError(f"最小フレーム間隔は 0 以上です: {gap_s}")
        self.gap_s = float(gap_s)
        self._last: float | None = None

    def reset(self) -> None:
        self._last = None

    def wait(self) -> None:
        """最小間隔が満たされるまで待つ(短い sleep の後、残りはスピン)。"""
        last = self._last
        if last is None or self.gap_s <= 0:
            return
        target = last + self.gap_s
        while True:
            rem = target - clock.now()
            if rem <= 0:
                return
            if rem > _SPIN_S:
                time.sleep(rem - _SPIN_S)
            else:
                time.sleep(0)  # GIL を手放しながらスピン(RX スレッドを止めない)

    def mark(self) -> None:
        """書込みを終えた時刻を記録する。"""
        self._last = clock.now()
