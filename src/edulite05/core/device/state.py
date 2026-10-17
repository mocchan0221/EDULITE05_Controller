"""モータ状態(phase1.md §1.8)。"""

from __future__ import annotations

from dataclasses import dataclass

from edulite05.core.protocol.types import MotorMode, RunMode


@dataclass(frozen=True, slots=True)
class MotorState:
    motor_id: int
    online: bool = False
    mode: MotorMode | None = None
    run_mode: RunMode | None = None
    fault_bits: int = 0
    position: float = 0.0  # type 2 の角度(±4π で周期ループ)
    position_multi: float = 0.0  # unwrap 済みの多回転角(multi_valid なら mechPos と同じ座標)
    # position_multi が mechPos(0x7019)で較正済みか。False の間は「セッション開始時の位置
    # を基準にした相対値」で、起動時に ±4π の外にいると k·8π ずれている(el05_protocol.md §8.0)。
    multi_valid: bool = False
    velocity: float = 0.0
    torque: float = 0.0
    temperature: float = 0.0
    last_rx: float | None = None  # このモータからの最後のフレーム(core.clock.now())
    rx_rate: float = 0.0  # 直近 1 s のフィードバック数
    fb_t: float | None = None  # 最後のフィードバック(type 2 / 24)の受信時刻(clock.now())


class PositionUnwrapper:
    """周期ループする角度を多回転角に展開する(el05_protocol.md §2.3)。

    type 2 の角度は ±4π(範囲幅 = `period`)で周期ループする。前回値との差が半周期を
    超えたら周期分を加減する。1 周期の半分を超える変化がサンプル間で起きないこと
    (= 速度 × 周期 < 4π)が前提。
    """

    __slots__ = ("period", "_last", "_offset")

    def __init__(self, period: float) -> None:
        self.period = period
        self._last: float | None = None
        self._offset = 0.0

    def update(self, raw: float) -> float:
        if self._last is not None:
            d = raw - self._last
            half = self.period / 2.0
            if d > half:
                self._offset -= self.period
            elif d < -half:
                self._offset += self.period
        self._last = raw
        return raw + self._offset

    @property
    def last_raw(self) -> float | None:
        """最後に `update` へ渡した折り返し位置(未受信なら None)。"""
        return self._last

    def seed(self, multi: float) -> float | None:
        """多回転角が `multi`(mechPos)になるよう、展開オフセットを k·period に設定する。

        k = round((multi - 最後の折り返し位置) / period)。折り返し位置と mechPos は同時に読めない
        (境界付近で一方だけ折り返していても)ので、k は丸めて決める。設定後の多回転角
        (= 折り返し位置 + k·period)を返す。折り返し位置が未受信なら何もせず None。
        """
        if self._last is None:
            return None
        k = round((multi - self._last) / self.period)
        self._offset = k * self.period
        return self._last + self._offset

    def reset(self) -> None:
        self._last = None
        self._offset = 0.0
