"""TelemetryHub: 名前付きチャネルの固定長リングバッファ(phase2.md §1.6, architecture.md §4.6)。

- 時刻は `core.clock.now()` 基準の絶対時刻(秒)。チャネル毎に非減少で追記されることを前提とする。
- 書込みは RX スレッド等から、読出し(`window` / `latest`)は UI スレッド等から。チャネル毎のロックで
  保護し、読出しは常にロック下でコピーを返す(半書込みを見せない)。
- `subscribe` で追記を購読できる(Recorder が使う)。
  コールバックは追記したスレッドで、ロック外で呼ばれる。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from edulite05.core import clock


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    name: str
    unit: str
    motor_id: int | None
    kind: str  # "feedback" / "param" / "command" / "derived" など
    label: str


Sink = Callable[[str, float, float], None]


class _Ring:
    """1 チャネル分のリングバッファ。"""

    __slots__ = ("info", "cap", "t", "v", "head", "count", "lock")

    def __init__(self, info: ChannelInfo, cap: int) -> None:
        self.info = info
        self.cap = cap
        self.t = np.zeros(cap, dtype=np.float64)
        self.v = np.zeros(cap, dtype=np.float64)
        self.head = 0  # 次に書く位置
        self.count = 0
        self.lock = threading.Lock()

    # 以下はロック保持下で呼ぶ
    def _t_at(self, i: int) -> float:
        """論理インデックス i(0 = 最古)の時刻。"""
        return float(self.t[(self.head - self.count + i) % self.cap])

    def _lower_bound(self, x: float) -> int:
        """t >= x となる最小の論理インデックス(無ければ count)。"""
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            if self._t_at(mid) < x:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _upper_bound(self, x: float) -> int:
        """t > x となる最小の論理インデックス(無ければ count)。"""
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            if self._t_at(mid) <= x:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _copy(self, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
        n = i1 - i0
        if n <= 0:
            return np.empty(0), np.empty(0)
        start = (self.head - self.count + i0) % self.cap
        end = start + n
        if end <= self.cap:
            return self.t[start:end].copy(), self.v[start:end].copy()
        return (
            np.concatenate((self.t[start:], self.t[: end - self.cap])),
            np.concatenate((self.v[start:], self.v[: end - self.cap])),
        )


class TelemetryHub:
    def __init__(self, capacity_s: float = 120.0, max_rate_hz: float = 1000.0) -> None:
        if capacity_s <= 0 or max_rate_hz <= 0:
            raise ValueError("capacity_s と max_rate_hz は正である必要があります")
        self.capacity_s = capacity_s
        self.max_rate_hz = max_rate_hz
        self.capacity = max(2, round(capacity_s * max_rate_hz))
        self._rings: dict[str, _Ring] = {}
        self._reg_lock = threading.Lock()
        self._sinks: tuple[Sink, ...] = ()

    # -- 登録 ---------------------------------------------------------------
    def register(self, name: str, unit: str, motor_id: int | None, kind: str, label: str) -> None:
        """チャネルを登録する。同一内容での再登録は何もしない。内容が異なれば `ValueError`。"""
        info = ChannelInfo(name, unit, motor_id, kind, label)
        with self._reg_lock:
            old = self._rings.get(name)
            if old is not None:
                if old.info != info:
                    raise ValueError(f"チャネル {name!r} は別の内容で登録済みです")
                return
            self._rings[name] = _Ring(info, self.capacity)

    def rename_motor(self, old_id: int, new_id: int) -> list[str]:
        """CAN ID 変更に伴い、`m{old}.` で始まるチャネルを `m{new}.` へ付け替える(履歴は保持)。

        付け替え先の名前が既にあるチャネルは付け替えず、古い方を捨てる。新しい名前の一覧を返す。
        """
        old_p, new_p = f"m{old_id}.", f"m{new_id}."
        renamed: list[str] = []
        with self._reg_lock:
            for name in [n for n, r in self._rings.items() if r.info.motor_id == old_id]:
                ring = self._rings.pop(name)
                if not name.startswith(old_p):
                    self._rings[name] = ring
                    continue
                target = new_p + name[len(old_p) :]
                if target in self._rings:
                    continue
                label = ring.info.label.replace(f"m{old_id} ", f"m{new_id} ", 1)
                ring.info = ChannelInfo(target, ring.info.unit, new_id, ring.info.kind, label)
                self._rings[target] = ring
                renamed.append(target)
        return renamed

    def channels(self) -> list[ChannelInfo]:
        with self._reg_lock:
            return [r.info for r in self._rings.values()]

    def has(self, name: str) -> bool:
        return name in self._rings

    def info(self, name: str) -> ChannelInfo:
        return self._ring(name).info

    def _ring(self, name: str) -> _Ring:
        try:
            return self._rings[name]
        except KeyError:
            raise KeyError(f"未登録のチャネル: {name!r}") from None

    # -- 購読 ---------------------------------------------------------------
    def subscribe(self, sink: Sink) -> Callable[[], None]:
        """追記の購読。戻り値は購読解除関数。"""
        with self._reg_lock:
            self._sinks = (*self._sinks, sink)

        def unsubscribe() -> None:
            with self._reg_lock:
                self._sinks = tuple(s for s in self._sinks if s is not sink)

        return unsubscribe

    # -- 書込み -------------------------------------------------------------
    def append(self, name: str, t: float, value: float) -> None:
        """1 サンプル追記。未登録なら `KeyError`。"""
        r = self._ring(name)
        t = float(t)
        value = float(value)
        with r.lock:
            h = r.head
            r.t[h] = t
            r.v[h] = value
            r.head = (h + 1) % r.cap
            if r.count < r.cap:
                r.count += 1
        for s in self._sinks:
            s(name, t, value)

    # -- 読出し -------------------------------------------------------------
    def window(
        self, names: Sequence[str], seconds: float, until: float | None = None
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """各チャネルの `[until - seconds, until]` のサンプルを `(t, v)` のコピーで返す。

        `until` が None なら `clock.now()`。未登録名は `KeyError`。
        """
        if until is None:
            until = clock.now()
        lo_t = until - seconds
        out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name in names:
            r = self._ring(name)
            with r.lock:
                i0 = r._lower_bound(lo_t)
                i1 = r._upper_bound(until)
                out[name] = r._copy(i0, i1)
        return out

    def latest(self, name: str) -> tuple[float, float] | None:
        r = self._ring(name)
        with r.lock:
            if r.count == 0:
                return None
            i = (r.head - 1) % r.cap
            return float(r.t[i]), float(r.v[i])

    def clear(self, names: Sequence[str] | None = None) -> None:
        """サンプルを破棄する(チャネル登録は残す)。"""
        targets = list(self._rings.values()) if names is None else [self._ring(n) for n in names]
        for r in targets:
            with r.lock:
                r.head = 0
                r.count = 0


# --- 標準チャネル(phase2.md §1.6) ------------------------------------------
# (サフィックス, 単位, kind, ラベル)
STANDARD_CHANNELS: tuple[tuple[str, str, str, str], ...] = (
    ("pos", "rad", "feedback", "position"),
    ("pos_multi", "rad", "feedback", "position (multi-turn)"),
    ("vel", "rad/s", "feedback", "velocity"),
    ("tq", "N·m", "feedback", "torque"),
    ("temp", "°C", "feedback", "temperature"),
    ("vbus", "V", "param", "bus voltage"),
    ("iqf", "A", "param", "q-axis current"),
    ("power_est", "W", "derived", "estimated input power"),
    ("cmd.pos", "rad", "command", "position command"),
    ("cmd.vel", "rad/s", "command", "velocity command"),
    ("cmd.tq", "N·m", "command", "torque command"),
    ("cmd.cur", "A", "command", "current command"),
)


def motor_channel(motor_id: int, suffix: str) -> str:
    """`m{id}.{suffix}`。"""
    return f"m{motor_id}.{suffix}"


# プラグインが足す、モータごとの追加チャネル(suffix, unit, kind, label)。
# 接続前(プラグイン有効化時)に
# 登録しておくと、モータが見つかったときに標準チャネルと一緒にハブへ登録される。
_EXTRA_CHANNELS: dict[str, tuple[str, str, str, str]] = {}


def register_extra_channel(suffix: str, unit: str, kind: str, label: str) -> None:
    """プラグイン用: モータごとの追加チャネル `m{id}.{suffix}` を足す。標準チャネルと重複不可。"""
    if any(suffix == s for s, *_ in STANDARD_CHANNELS):
        raise ValueError(f"標準チャネルと重複しています: {suffix!r}")
    spec = (suffix, unit, kind, label)
    old = _EXTRA_CHANNELS.get(suffix)
    if old is not None and old != spec:
        raise ValueError(f"チャネル {suffix!r} は別の内容で登録済みです")
    _EXTRA_CHANNELS[suffix] = spec


def unregister_extra_channel(suffix: str) -> None:
    _EXTRA_CHANNELS.pop(suffix, None)


def extra_channels() -> tuple[tuple[str, str, str, str], ...]:
    return tuple(_EXTRA_CHANNELS.values())


def register_motor_channels(hub: TelemetryHub, motor_id: int) -> list[str]:
    """モータ 1 台分の標準チャネル(+ プラグインの追加チャネル)を登録し、チャネル名を返す。"""
    names = []
    for suffix, unit, kind, label in (*STANDARD_CHANNELS, *_EXTRA_CHANNELS.values()):
        name = motor_channel(motor_id, suffix)
        hub.register(name, unit, motor_id, kind, f"m{motor_id} {label}")
        names.append(name)
    return names
