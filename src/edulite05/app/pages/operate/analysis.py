"""グラフの解析(phase4_5.md §K)。純粋な計算だけ。Qt を import しない(単体テストできる)。

- トリガ: `find_crossing`(立上り/立下りの交差時刻を線形補間で返す)
- Δ カーソル: `delta_between`(Δt・Δ値・傾き)
- FFT: `spectrum`(一様に再標本 → 直線を除去 → Hann 窓 → 振幅スペクトル)
- ステップ応答: `find_steps` / `step_metrics`(立上り時間 10–90 %・オーバーシュート・整定時間 ±2 %)
- 状態の時間帯: `LaneTimeline`(ui_state / モード / クランプの区間を溜める)

時刻はすべて秒(呼び出し側が決めた同じ基準)。値は SI(rad, rad/s, N·m, ...)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

RISE_LO = 0.10  # 立上り時間の下側(最終値の割合)
RISE_HI = 0.90
SETTLE_BAND = 0.02  # 整定時間: 最終値 ± 2 %(ステップ幅に対する割合)
MAX_FFT_SAMPLES = 1 << 17
MAX_EDGE_S = 0.1  # ステップとみなす指令変化の最長の所要時間
PLATEAU_S = 0.1  # 変化の後、新しい値にとどまっているべき時間


# ======================================================================== トリガ
def find_crossing(
    t: np.ndarray, y: np.ndarray, level: float, rising: bool, after: float = -math.inf
) -> float | None:
    """`after` より後で、最初に `level` を横切った時刻(線形補間)。なければ None。

    立上り = 直前のサンプルが level 未満で、次が level 以上。立下りはその逆。
    NaN のサンプルは飛ばして、前後の有限値どうしで判定する(欠測をまたぐ交差も拾う)。
    """
    if t.size < 2:
        return None
    ok = np.isfinite(t) & np.isfinite(y)
    tt, yy = t[ok], y[ok]
    if tt.size < 2:
        return None
    prev, cur = yy[:-1], yy[1:]
    hit = (prev < level) & (cur >= level) if rising else (prev > level) & (cur <= level)
    idx = np.flatnonzero(hit)
    for i in idx:
        t0, t1, y0, y1 = float(tt[i]), float(tt[i + 1]), float(prev[i]), float(cur[i])
        tc = t1 if y1 == y0 else t0 + (level - y0) * (t1 - t0) / (y1 - y0)
        if tc > after:
            return tc
    return None


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    channel: str  # ChannelInfo.name(例: "m1.vel")
    level: float
    rising: bool = True  # False = 立下り
    span_s: float = 2.0  # 成立の前後に残す秒数


class SingleShotTrigger:
    """単発トリガ(状態: idle → armed → fired → captured)。時刻は呼び出し側の絶対時刻 [s]。

    armed: 待機を始めた後に条件を満たした最初の時刻を探す(過去のデータでは成立させない)。
    fired: 成立した。成立後 `span_s` 経ったら `due()` が真になり、呼び出し側が表示を止める。
    captured: 止めた(再開・解除で idle)。
    """

    def __init__(self) -> None:
        self.state = "idle"
        self.spec: TriggerSpec | None = None
        self.t_trigger: float | None = None
        self._armed_at = 0.0

    def arm(self, spec: TriggerSpec, now: float) -> None:
        self.spec, self.state, self.t_trigger, self._armed_at = spec, "armed", None, now

    def reset(self) -> None:
        self.state, self.spec, self.t_trigger = "idle", None, None

    def feed(self, now: float, t_rel: np.ndarray, y: np.ndarray) -> bool:
        """`now + t_rel` を絶対時刻として条件を探す。成立したら True。"""
        if self.state != "armed" or self.spec is None:
            return False
        sp = self.spec
        tc = find_crossing(t_rel + now, y, sp.level, sp.rising, after=self._armed_at)
        if tc is None:
            return False
        self.t_trigger, self.state = tc, "fired"
        return True

    def due(self, now: float) -> bool:
        return (
            self.state == "fired" and self.spec is not None and self.t_trigger is not None
            and now >= self.t_trigger + self.spec.span_s
        )

    def capture(self) -> None:
        if self.state == "fired":
            self.state = "captured"


# ======================================================================== Δ カーソル
@dataclass(frozen=True, slots=True)
class DeltaResult:
    dt: float  # t_b - t_a
    dv: float  # v_b - v_a
    slope: float | None  # dv / dt(dt = 0 なら None)


def delta_between(t_a: float, v_a: float, t_b: float, v_b: float) -> DeltaResult:
    dt, dv = t_b - t_a, v_b - v_a
    return DeltaResult(dt, dv, None if dt == 0 else dv / dt)


def sample_at(t: np.ndarray, y: np.ndarray, at: float) -> float | None:
    """時刻 `at` に最も近い有限なサンプルの値(データ範囲の外なら端)。"""
    if t.size == 0:
        return None
    i = int(np.searchsorted(t, at))
    i = min(max(i, 0), t.size - 1)
    if i > 0 and abs(t[i - 1] - at) < abs(t[i] - at):
        i -= 1
    v = float(y[i])
    return v if math.isfinite(v) else None


# ======================================================================== FFT
@dataclass(frozen=True, slots=True)
class Spectrum:
    freq: np.ndarray  # Hz(0 〜 ナイキスト)
    amp: np.ndarray  # 振幅(元の単位。正弦波 A·sin なら A に近い値)
    fs: float  # 再標本のサンプリング周波数 [Hz]
    peak_freq: float  # DC を除く最大振幅の周波数 [Hz]
    peak_amp: float


def spectrum(t: np.ndarray, y: np.ndarray, *, min_samples: int = 16) -> Spectrum | None:
    """Hann 窓の振幅スペクトル。不揃いな時刻は中央値の間隔で一様に再標本する。

    直線(平均 + 傾き)を引いてから窓を掛ける: 位置のように単調に増える量でも DC 付近に
    漏れが出ない。振幅は 2/Σw で正規化(窓のコヒーレントゲイン補正)。
    """
    ok = np.isfinite(t) & np.isfinite(y)
    tt, yy = t[ok], y[ok]
    if tt.size < min_samples:
        return None
    dts = np.diff(tt)
    dts = dts[dts > 0]
    if dts.size == 0:
        return None
    dt = float(np.median(dts))
    span = float(tt[-1] - tt[0])
    n = int(span / dt) + 1
    if n < min_samples:
        return None
    n = min(n, MAX_FFT_SAMPLES)
    tu = tt[0] + dt * np.arange(n)
    yu = np.interp(tu, tt, yy)
    x = np.arange(n, dtype=float)
    slope, icpt = np.polyfit(x, yu, 1)
    yu = yu - (slope * x + icpt)
    w = np.hanning(n)
    amp = np.abs(np.fft.rfft(yu * w)) * 2.0 / float(np.sum(w))
    freq = np.fft.rfftfreq(n, dt)
    if amp.size < 3:
        return None
    k = int(np.argmax(amp[1:])) + 1
    return Spectrum(freq, amp, 1.0 / dt, float(freq[k]), float(amp[k]))


# ======================================================================== ステップ応答
@dataclass(frozen=True, slots=True)
class StepEdge:
    t_start: float  # 指令が変わった最初のサンプルの時刻
    t_end: float  # 指令が新しい値に着いた時刻
    before: float
    after: float

    @property
    def amplitude(self) -> float:
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class StepMetrics:
    t0: float  # ステップの時刻(入力と同じ時間基準)
    initial: float  # ステップ前の実測値
    final: float  # 整定後の実測値(末尾の平均)
    amplitude: float  # final - initial(符号付き)
    command_amplitude: float  # 指令のステップ幅
    rise_time: float | None  # 10 % → 90 % [s]。90 % に届かなければ None
    overshoot_pct: float  # 最終値を超えた分 / ステップ幅 [%](0 以上)
    peak: float  # 最終値を超えた側の最大値(オーバーシュートが無ければ final 近傍の最大)
    settling_time: float | None  # t0 から ±2 % 帯に入ったままになるまで [s]
    t_settle: float | None  # 上の時刻(入力と同じ時間基準)


def find_steps(
    t: np.ndarray, cmd: np.ndarray, *, min_amp: float | None = None
) -> list[StepEdge]:
    """指令値のステップ(急な一段の変化)を時刻順に返す。

    1 サンプルで指令の全幅の 2 % を超えて動いた区間(3 サンプル以内の隙間は同じ変化とみなす)
    を 1 つの変化とし、次の両方を満たすものだけをステップとする:
    幅が `min_amp`(既定は全幅の 10 %)以上、変化に要した時間が `MAX_EDGE_S` 以下、
    かつ変化の後 `PLATEAU_S` のあいだ新しい値(±幅の 10 %)にとどまる(データが無ければ可)。
    加速度制限で数十 ms かけて立ち上がる指令もステップとみなす。正弦のように動き続ける
    指令は、ゼロ交差の急な区間があっても直後にとどまらないのでステップにならない。
    """
    ok = np.isfinite(t) & np.isfinite(cmd)
    tt, c = t[ok], cmd[ok]
    if tt.size < 3:
        return []
    rng = float(np.max(c) - np.min(c))
    if rng <= 0:
        return []
    amp_min = 0.1 * rng if min_amp is None else min_amp
    # 「急な変化」は 1 サンプルあたりでなく時間あたりで見る(5 ms 周期で全幅の 2 %)。サンプルが
    # 速い(DemoBridge は 1 kHz)と、加速度制限でゆっくり立ち上がる指令が 1 サンプルあたりでは閾値を
    # 下回って、ステップとして検出されなかった
    dt = float(np.median(np.diff(tt)))
    step_thr = 0.02 * rng * min(1.0, dt / 0.005) if dt > 0 else 0.02 * rng
    moving = np.flatnonzero(np.abs(np.diff(c)) > step_thr)
    if moving.size == 0:
        return []
    groups: list[list[int]] = [[int(moving[0])]]
    for i in moving[1:]:
        if int(i) - groups[-1][-1] <= 3:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    edges: list[StepEdge] = []
    for g in groups:
        a, b = g[0], g[-1] + 1
        edge = StepEdge(float(tt[a + 1]), float(tt[b]), float(c[a]), float(c[b]))
        if abs(edge.amplitude) < amp_min or edge.t_end - edge.t_start > MAX_EDGE_S:
            continue
        after = (tt > edge.t_end) & (tt <= edge.t_end + PLATEAU_S)
        if np.any(after) and np.max(np.abs(c[after] - edge.after)) > 0.1 * abs(edge.amplitude):
            continue
        edges.append(edge)
    return edges


def analyze_step(
    t: np.ndarray, y: np.ndarray, edge: StepEdge, t_next: float | None = None
) -> StepMetrics | None:
    """1 つのステップについて、実測 `y` から立上り・オーバーシュート・整定を求める。

    応答が終わっていない(末尾がまだ動いている・データが短い・動かなかった)ときは None。
    """
    ok = np.isfinite(t) & np.isfinite(y)
    tt, yy = t[ok], y[ok]
    if tt.size < 8:
        return None
    t_end = float(tt[-1]) if t_next is None else min(float(tt[-1]), t_next)
    seg = (tt >= edge.t_start) & (tt <= t_end)
    ts, ys = tt[seg], yy[seg]
    if ts.size < 6 or ts[-1] - ts[0] < 0.1:
        return None
    pre = (tt < edge.t_start) & (tt >= edge.t_start - 0.2)
    initial = float(np.median(yy[pre])) if np.any(pre) else float(ys[0])
    n_tail = max(3, ts.size // 5)
    tail = ys[-n_tail:]
    final = float(np.mean(tail))
    amp = final - initial
    if abs(amp) < 0.2 * abs(edge.amplitude) or amp == 0.0:  # 動かなかった(制限・無効など)
        return None
    if float(np.max(tail) - np.min(tail)) > 0.05 * abs(amp):  # 末尾がまだ動いている
        return None
    f = (ys - initial) / amp  # 0 → 1 に正規化(下りステップも同じ式で扱える)
    t10 = _first_crossing(ts, f, RISE_LO)
    t90 = _first_crossing(ts, f, RISE_HI, after=t10)
    rise = None if t10 is None or t90 is None else t90 - t10
    peak_f = float(np.max(f))
    overshoot = max(0.0, peak_f - 1.0) * 100.0
    out = np.flatnonzero(np.abs(f - 1.0) > SETTLE_BAND)
    if out.size == 0:
        settle: float | None = 0.0
        t_settle: float | None = float(ts[0])
    elif out[-1] >= ts.size - 1:
        settle, t_settle = None, None
    else:
        k = int(out[-1])  # 最後に帯の外にいたサンプル → 次のサンプルで帯に戻っている
        i0, i1 = k, k + 1
        edge_v = 1.0 + SETTLE_BAND * math.copysign(1.0, f[i0] - 1.0)
        if f[i1] != f[i0]:
            frac = (edge_v - f[i0]) / (f[i1] - f[i0])
            t_settle = float(ts[i0] + min(max(frac, 0.0), 1.0) * (ts[i1] - ts[i0]))
        else:
            t_settle = float(ts[i1])
        settle = t_settle - edge.t_start
    peak = initial + peak_f * amp
    return StepMetrics(
        t0=edge.t_start, initial=initial, final=final, amplitude=amp,
        command_amplitude=edge.amplitude, rise_time=rise, overshoot_pct=overshoot, peak=peak,
        settling_time=settle, t_settle=t_settle,
    )


def _first_crossing(
    t: np.ndarray, f: np.ndarray, level: float, after: float | None = None
) -> float | None:
    """f が最初に level 以上になった時刻(線形補間)。`after` 以降だけを見る。"""
    start = 0 if after is None else int(np.searchsorted(t, after))
    idx = np.flatnonzero(f[start:] >= level)
    if idx.size == 0:
        return None
    i = start + int(idx[0])
    if i == 0 or f[i] == f[i - 1]:
        return float(t[i])
    frac = (level - f[i - 1]) / (f[i] - f[i - 1])
    return float(t[i - 1] + min(max(frac, 0.0), 1.0) * (t[i] - t[i - 1]))


def step_metrics(
    t: np.ndarray, y: np.ndarray, tc: np.ndarray, cmd: np.ndarray
) -> StepMetrics | None:
    """最後に応答が完了したステップの評価。実行中なら直前の完了したステップを返す。

    (t, y) = 実測、(tc, cmd) = 指令(別の時刻列でもよい)。
    """
    edges = find_steps(tc, cmd)
    for i in range(len(edges) - 1, -1, -1):
        nxt = edges[i + 1].t_start if i + 1 < len(edges) else None
        m = analyze_step(t, y, edges[i], nxt)
        if m is not None:
            return m
    return None


# ======================================================================== 状態の時間帯
@dataclass(slots=True)
class Segment:
    start: float
    end: float | None  # None = 継続中
    key: str


@dataclass(slots=True)
class LaneTimeline:
    """1 本の帯(ui_state / モード / クランプ)。`push(t, key)` で値が変わるたびに区間を切る。

    key が None のあいだは帯を出さない(クランプしていない、モード未設定など)。
    """

    keep_s: float = 150.0
    segments: list[Segment] = field(default_factory=list)

    def push(self, t: float, key: str | None) -> None:
        cur = self.segments[-1] if self.segments and self.segments[-1].end is None else None
        if cur is not None and cur.key == key:
            return
        if cur is None and key is None:
            return
        if cur is not None:
            cur.end = t
        if key is not None:
            self.segments.append(Segment(t, None, key))
        cutoff = t - self.keep_s
        while self.segments and self.segments[0].end is not None and self.segments[0].end < cutoff:
            self.segments.pop(0)

    def visible(self, t0: float, t1: float) -> list[Segment]:
        """[t0, t1] と重なる区間(継続中は t1 まで)。"""
        return [
            s for s in self.segments
            if s.start <= t1 and (s.end is None or s.end >= t0)
        ]

    def clear(self) -> None:
        self.segments.clear()
