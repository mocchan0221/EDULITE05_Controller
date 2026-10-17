"""グラフ解析の純粋な計算(トリガ・Δ・FFT・ステップ応答・状態の帯)。Qt 不要。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from edulite05.app.pages.operate import analysis as an


# ------------------------------------------------------------------ トリガ
def test_find_crossing_rising_and_falling_with_interpolation() -> None:
    t = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    y = np.array([0.0, 1.0, 3.0, 1.0, -1.0])
    assert an.find_crossing(t, y, 2.0, rising=True) == pytest.approx(1.5)
    assert an.find_crossing(t, y, 2.0, rising=False) == pytest.approx(2.5)
    assert an.find_crossing(t, y, 0.5, rising=False) == pytest.approx(3.25)
    assert an.find_crossing(t, y, 5.0, rising=True) is None


def test_find_crossing_after_skips_earlier_hits_and_ignores_nan() -> None:
    t = np.arange(8, dtype=float)
    y = np.array([0, 2, 0, 2, np.nan, 0, 2, 0], dtype=float)
    first = an.find_crossing(t, y, 1.0, True)
    assert first == pytest.approx(0.5)
    second = an.find_crossing(t, y, 1.0, True, after=first)
    assert second == pytest.approx(2.5)
    # NaN をまたいだ判定(3→5 は 2 → 0 なので立上りではない)。その次の立上りは 5→6
    third = an.find_crossing(t, y, 1.0, True, after=second)
    assert third == pytest.approx(5.5)


# ------------------------------------------------------------------ Δ カーソル
def test_delta_between_and_sample_at() -> None:
    d = an.delta_between(1.0, 2.0, 3.0, 8.0)
    assert (d.dt, d.dv, d.slope) == (2.0, 6.0, 3.0)
    assert an.delta_between(1.0, 2.0, 1.0, 5.0).slope is None
    assert an.delta_between(3.0, 8.0, 1.0, 2.0).slope == pytest.approx(3.0)  # 順序を逆にしても同じ
    t = np.array([0.0, 1.0, 2.0])
    y = np.array([5.0, np.nan, 7.0])
    assert an.sample_at(t, y, 0.1) == 5.0
    assert an.sample_at(t, y, 1.1) is None  # 最も近いサンプルが欠測
    assert an.sample_at(t, y, 9.0) == 7.0
    assert an.sample_at(np.array([]), np.array([]), 0.0) is None


# ------------------------------------------------------------------ FFT
def test_spectrum_finds_sine_frequency_and_amplitude() -> None:
    fs, f0, a0 = 200.0, 7.0, 0.8
    t = np.arange(0, 10, 1 / fs)
    y = a0 * np.sin(2 * np.pi * f0 * t) + 3.0 + 0.5 * t  # DC とドリフト付き
    s = an.spectrum(t, y)
    assert s is not None
    assert s.fs == pytest.approx(fs)
    assert s.peak_freq == pytest.approx(f0, abs=0.15)
    assert s.peak_amp == pytest.approx(a0, rel=0.1)
    assert s.freq[-1] == pytest.approx(fs / 2, rel=0.01)
    assert s.amp[0] < 0.05  # 直線を除いてあるので DC に漏れない


def test_spectrum_resamples_irregular_times_and_rejects_short_data() -> None:
    rng = np.random.default_rng(0)
    t = np.cumsum(rng.uniform(0.004, 0.006, 2000))
    y = np.sin(2 * np.pi * 12.0 * t)
    s = an.spectrum(t, y)
    assert s is not None and s.peak_freq == pytest.approx(12.0, abs=0.3)
    assert an.spectrum(t[:5], y[:5]) is None
    assert an.spectrum(np.array([1.0, 1.0, 1.0] * 10), np.zeros(30)) is None


# ------------------------------------------------------------------ ステップ応答
def _first_order(tau: float, t: np.ndarray, t0: float, y0: float, y1: float) -> np.ndarray:
    return np.where(t < t0, y0, y1 + (y0 - y1) * np.exp(-(t - t0) / tau))


def _cmd_step(t: np.ndarray, t0: float, y0: float, y1: float) -> np.ndarray:
    return np.where(t < t0, y0, y1)


def test_first_order_step_metrics_match_theory() -> None:
    t = np.arange(0, 3.0, 0.005)
    tau = 0.1
    y = _first_order(tau, t, 0.5, 0.0, 2.0)
    m = an.step_metrics(t, y, t, _cmd_step(t, 0.5, 0.0, 2.0))
    assert m is not None
    assert m.t0 == pytest.approx(0.5, abs=0.01)
    assert m.initial == pytest.approx(0.0, abs=1e-6)
    assert m.final == pytest.approx(2.0, abs=0.01)
    assert m.rise_time == pytest.approx(2.197 * tau, abs=0.006)
    assert m.overshoot_pct == pytest.approx(0.0, abs=0.1)
    assert m.settling_time == pytest.approx(3.912 * tau, abs=0.01)  # ±2 %
    assert m.t_settle == pytest.approx(0.5 + 3.912 * tau, abs=0.01)


def test_second_order_overshoot_matches_theory() -> None:
    zeta, wn = 0.5, 20.0
    wd = wn * math.sqrt(1 - zeta**2)
    t = np.arange(0, 3.0, 0.002)
    tt = np.clip(t - 0.3, 0, None)
    resp = 1 - np.exp(-zeta * wn * tt) * (
        np.cos(wd * tt) + zeta / math.sqrt(1 - zeta**2) * np.sin(wd * tt)
    )
    y = 5.0 * resp  # 0 → 5
    m = an.step_metrics(t, y, t, _cmd_step(t, 0.3, 0.0, 5.0))
    assert m is not None
    expect = math.exp(-math.pi * zeta / math.sqrt(1 - zeta**2)) * 100.0  # 16.3 %
    assert m.overshoot_pct == pytest.approx(expect, abs=0.5)
    assert m.peak == pytest.approx(5.0 * (1 + expect / 100), rel=0.01)
    assert m.rise_time is not None and 0.07 < m.rise_time < 0.095
    assert m.settling_time is not None and 0.3 < m.settling_time < 0.45


def test_falling_step_is_symmetric() -> None:
    t = np.arange(0, 2.0, 0.005)
    y = _first_order(0.1, t, 0.4, 3.0, -1.0)
    m = an.step_metrics(t, y, t, _cmd_step(t, 0.4, 3.0, -1.0))
    assert m is not None
    assert m.amplitude == pytest.approx(-4.0, abs=0.05)
    assert m.rise_time == pytest.approx(2.197 * 0.1, abs=0.006)
    assert m.overshoot_pct == pytest.approx(0.0, abs=0.1)


def test_latest_complete_step_is_used_while_next_is_running() -> None:
    """実行中は、終わっていない最後のステップではなく直前の完了したものを評価する。"""
    t = np.arange(0, 3.05, 0.005)
    cmd = np.where(t < 1.0, 0.0, np.where(t < 2.0, 1.0, 0.0))
    y = np.where(t < 1.0, 0.0, np.where(t < 2.0, 1 - np.exp(-(t - 1.0) / 0.05),
                                         np.exp(-(t - 2.0) / 0.05)))
    cut = t <= 2.03  # 2 s の立下りが始まって 30 ms だけ
    m = an.step_metrics(t[cut], y[cut], t[cut], cmd[cut])
    assert m is not None and m.t0 == pytest.approx(1.0, abs=0.01)
    assert m.amplitude > 0
    full = an.step_metrics(t, y, t, cmd)
    assert full is not None and full.t0 == pytest.approx(2.0, abs=0.01) and full.amplitude < 0


def test_no_metrics_without_step_or_response() -> None:
    t = np.arange(0, 2.0, 0.005)
    assert an.step_metrics(t, np.zeros_like(t), t, np.sin(2 * np.pi * 1.0 * t)) is None
    flat_cmd = _cmd_step(t, 0.5, 0.0, 1.0)
    assert an.step_metrics(t, np.zeros_like(t), t, flat_cmd) is None  # 動かなかった
    assert an.step_metrics(t[:4], t[:4], t[:4], flat_cmd[:4]) is None


def test_find_steps_ignores_sine_and_small_wiggles() -> None:
    t = np.arange(0, 3.0, 0.005)
    ts = t[t < 2.62]  # 山の手前まで(最後のゼロ交差の直後にデータが続く)
    assert an.find_steps(ts, np.sin(2 * np.pi * 2.0 * ts)) == []
    cmd = _cmd_step(t, 1.0, 0.0, 2.0)
    (edge,) = an.find_steps(t, cmd)
    assert edge.before == 0.0 and edge.after == 2.0 and edge.amplitude == 2.0
    assert edge.t_start == pytest.approx(1.0, abs=0.01)
    # 加速度制限で 20 ms かけて立ち上がる指令もステップ(ゆっくりした 0.5 s の傾きは違う)
    t2 = np.arange(0, 3.0, 0.001)
    ramp = np.clip((t2 - 1.0) / 0.02, 0, 1) * 2.0
    (edge2,) = an.find_steps(t2, ramp)
    assert edge2.amplitude == pytest.approx(2.0) and edge2.t_end - edge2.t_start < 0.03
    assert an.find_steps(t2, np.clip((t2 - 1.0) / 0.5, 0, 1) * 2.0) == []


# ------------------------------------------------------------------ 状態の帯
def test_lane_timeline_segments_and_visibility() -> None:
    lane = an.LaneTimeline(keep_s=10.0)
    lane.push(0.0, "disabled")
    lane.push(1.0, "disabled")  # 変化なし
    lane.push(2.0, "enabled")
    lane.push(4.0, "running")
    assert [(s.start, s.end, s.key) for s in lane.segments] == [
        (0.0, 2.0, "disabled"), (2.0, 4.0, "enabled"), (4.0, None, "running"),
    ]
    assert [s.key for s in lane.visible(3.0, 5.0)] == ["enabled", "running"]
    lane.push(5.0, None)  # 帯なし(クランプ解除など)
    assert lane.segments[-1].end == 5.0
    lane.push(6.0, None)
    assert len(lane.segments) == 3
    lane.push(30.0, "fault")  # 古い区間は捨てる
    assert [s.key for s in lane.segments] == ["fault"]
    lane.clear()
    assert lane.segments == []
