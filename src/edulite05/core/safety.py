"""出力段階と SafetyGuard(phase2.md §1.1, architecture.md §4.3.1)。

全モータ指令はここを通す。クランプは「段階の上限 → レンジ → ソフトリミット → MIT 最悪トルク」の順。
NaN / inf は `SafetyError`。ガードの状態は段階とソフトリミット、および MIT の暴走ガードの
モータごとのラッチだけ(`clamp_mit` を呼ぶのは制御ループの 1 スレッド)。段階変更は単一の参照差替えで
原子的なので、UI など別スレッドから `set_tier` / `limits` を使ってよい。

MIT の p_set の座標系(位置の扱い。el05_protocol.md §8.0、open_questions.md Q-P12):

- `MitCmd.position` は **多回転座標**(`MotorState.position_multi`、mechPos と同じ)で受け取る。
  type 2 の `state.position` は ±4π で折り返し、MIT の p_set をモータが何と比べるかは未確認
  (`profile.position.mit_frame` = "unknown" / "wrapped" / "multi")。
- 最悪トルクの位置誤差は、"unknown" なら折り返し位置・多回転位置の両方に対する大きい方、
  "wrapped" / "multi" なら該当する方だけで見積もる。"wrapped" ではコマンド座標へ
  `p_w + (p_set - p_multi)` と変換して出す。
- "unknown" / "multi" で |多回転位置| > 4π - wrap_margin のときは位置目標を表現できない(p_set の
  レンジが ±4π)/ 曖昧なので kp = 0(減衰と t_ff のみ。ClampNote reason = "position_wrap")。

MIT のホスト側項(負の kd・摩擦補償。architecture.md §4.3.1):
フレームの kd は 0–5 で負を送れない。そこで `MitCmd.kd < 0` は `kd_fw = 0` を送り、
`t_ff += kd_host·(v_set - v)`(kd_host = min(kd, 0))を毎周期、最新のフィードバック速度 v で
計算して足す。`MitCmd.friction_comp = c` は `t_ff += c·tanh(v / v_eps)`。これらを足した
t_ff を最悪トルク `|kp·Δp| + |kd_fw·Δv| + |t_ff| <= tier.torque` の見積りに含める
(クランプ順は同じ)。
**暴走ガード**(ヒステリシス): ホスト側項が有効なとき、|v| > runaway_on × 段階の速度上限で
ホスト側の負の減衰と摩擦補償を捨て、kd_fw を guard_kd 以上にし(ClampNote reason =
"runaway_guard")、|v| < runaway_off × 段階の速度上限になるまで保つ。ガードの状態(モータごとの
ラッチ)だけは SafetyGuard が持つ(`clamp_mit` を呼ぶのは制御ループの 1 スレッド)。

state が未受信(online=False)かどうかは検査しない(呼出側=MotorService が判断)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from edulite05.core.control.commands import MitCmd, SetpointCmd
from edulite05.core.device.profile import MotorProfile, TierLimits
from edulite05.core.device.state import MotorState
from edulite05.core.errors import SafetyError

_KINDS = ("velocity", "current", "position")


class PowerTier(Enum):
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class SoftLimits:
    """ユーザー設定の位置ソフトリミット。None は無制限(段階の上限のみ)。"""

    position_min: float | None = None
    position_max: float | None = None

    def __post_init__(self) -> None:
        for v in (self.position_min, self.position_max):
            if v is not None and not math.isfinite(v):
                raise SafetyError(f"ソフトリミットが有限値ではありません: {v!r}")
        if (
            self.position_min is not None
            and self.position_max is not None
            and self.position_min > self.position_max
        ):
            raise SafetyError("position_min > position_max")


@dataclass(frozen=True, slots=True)
class ClampNote:
    """クランプの記録。UI の「低出力上限で制限中」表示に使う。

    reason: "tier"(出力段階の上限)/ "range"(プロファイルのレンジ・符号)/
    "soft_limit"(位置ソフトリミット)/ "worst_case"(MIT 最悪トルク制限)/
    "position_wrap"(±4π 付近で位置目標を使えず kp = 0 にした)/
    "runaway_guard"(速度が上限の 8 割を超えた: MIT では負の減衰・摩擦補償を捨てて kd を上げた /
    電流モードでは iq_ref を制動方向に置き換えた)。
    同一フィールドが複数の理由で変更された場合は "tier,worst_case" のように連結する。
    """

    field: str
    requested: float
    applied: float
    reason: str


class _Notes:
    """フィールド毎に 1 件へ畳み込む ClampNote 収集器。"""

    def __init__(self) -> None:
        self._d: dict[str, ClampNote] = {}

    def add(self, field: str, before: float, after: float, reason: str) -> None:
        if after == before:
            return
        old = self._d.get(field)
        if old is None:
            self._d[field] = ClampNote(field, before, after, reason)
        else:
            reasons = old.reason.split(",")
            if reason not in reasons:
                reasons.append(reason)
            self._d[field] = ClampNote(field, old.requested, after, ",".join(reasons))

    def result(self) -> list[ClampNote]:
        # 最終的に元の値へ戻ったものは通知しない
        return [n for n in self._d.values() if n.requested != n.applied]


def _finite(name: str, v: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SafetyError(f"{name} が数値ではありません: {v!r}") from None
    if not math.isfinite(f):
        raise SafetyError(f"{name} が有限値ではありません: {v!r}")
    return f


def _clip(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class SafetyGuard:
    """出力段階の上限とソフトリミットで指令をクランプする。"""

    def __init__(
        self,
        profile: MotorProfile,
        tier: PowerTier = PowerTier.LOW,
        soft: SoftLimits | None = None,
    ) -> None:
        self.profile = profile
        self.soft: SoftLimits = soft if soft is not None else SoftLimits()
        # (tier, limits) を 1 つの参照で持ち、別スレッドから読んでも不整合にならないようにする
        self._cur: tuple[PowerTier, TierLimits] = (tier, profile.tier(tier.value))
        # 暴走ガード中のモータ(ヒステリシスのラッチ)
        self._runaway: set[int] = set()

    # -- 段階 ---------------------------------------------------------------
    @property
    def tier(self) -> PowerTier:
        return self._cur[0]

    @property
    def limits(self) -> TierLimits:
        """現在段階の上限(`profile.tier(tier.value)`)。"""
        return self._cur[1]

    def set_tier(self, tier: PowerTier) -> None:
        """段階を切り替える(HIGH への許可判定は MotorService の責務)。"""
        self._cur = (tier, self.profile.tier(tier.value))

    def set_soft_limits(self, soft: SoftLimits) -> None:
        self.soft = soft

    # -- 暴走ガード ----------------------------------------------------------
    def runaway_active(self, motor_id: int) -> bool:
        """MIT のホスト側項の暴走ガードが働いているか。"""
        return motor_id in self._runaway

    def reset_runaway(self, motor_id: int | None = None) -> None:
        """暴走ガードのラッチを解除する(コントローラの開始・停止時に制御ループが呼ぶ)。"""
        if motor_id is None:
            self._runaway.clear()
        else:
            self._runaway.discard(motor_id)

    # -- 上限(プロファイルの物理レンジとの小さい方)----------------------------
    def _effective(self, t: TierLimits) -> tuple[float, float, float, float, float]:
        """(torque, current, speed, kp, kd) の実効上限。"""
        r = self.profile.ranges
        torque = min(t.torque, r.torque[1])
        speed = min(t.speed, r.velocity[1])
        kp = min(t.kp, r.kp[1])
        kd = min(t.kd, r.kd[1])
        current = min(t.current, self.profile.limits.current)
        return torque, current, speed, kp, kd

    def _soft_clip(self, v: float) -> float:
        s = self.soft
        if s.position_min is not None and v < s.position_min:
            v = s.position_min
        if s.position_max is not None and v > s.position_max:
            v = s.position_max
        return v

    # -- MIT ----------------------------------------------------------------
    def clamp_mit(self, cmd: MitCmd, state: MotorState) -> tuple[MitCmd, list[ClampNote]]:
        """MIT 指令をクランプする。`cmd.position` は多回転座標、戻り値の position はコマンド座標。

        1. 各値を finite 検査し、kp/kd/|t_ff|/|v_set| を段階の上限(とレンジ)へ、
           p_set をレンジ + ソフトリミットへ(座標系は上の説明)。
        2. 位置の折り返し(`profile.position`): "unknown" / "multi" で |多回転位置| が
           4π - wrap_margin を超えていたら kp = 0。
        3. ホスト側項: kd < 0 → フレームの kd = 0 と `t_ff += kd·(v_set-v)`、friction_comp →
           `t_ff += c·tanh(v/v_eps)`。暴走ガード中(|v| のヒステリシス)はこれらを捨てて
           kd ≥ guard_kd。合算した t_ff を段階のトルク上限に切る(モジュールの説明)。
        4. 最悪トルク `|kp*err| + |kd_fw*(v_set-v)| + |t_ff 合計| <= tier.torque`
           (err は参照位置ごとの位置誤差の最大)を満たすよう、まず t_ff を縮め、
           次に p_set を参照位置寄りに、
           次に v_set を v 側へ寄せる。参照位置が食い違って p_set で満たせないときは kp = 0。

        戻り値の `MitCmd` は kd = フレームの kd(0 以上)、torque = 合算後の t_ff、friction_comp = 0。
        """
        p_w = _finite("state.position", state.position)
        p_m = _finite("state.position_multi", state.position_multi)
        v = _finite("state.velocity", state.velocity)
        p_set = _finite("position", cmd.position)
        v_set = _finite("velocity", cmd.velocity)
        kp = _finite("kp", cmd.kp)
        kd = _finite("kd", cmd.kd)
        t_ff = _finite("torque", cmd.torque)

        limits = self._cur[1]
        t_max, _, v_max, kp_max, kd_max = self._effective(limits)
        r = self.profile.ranges
        pos = self.profile.position
        mh = self.profile.mit_host
        frame = pos.mit_frame
        refs = (p_w,) if frame == "wrapped" else (p_m,) if frame == "multi" else (p_w, p_m)
        notes = _Notes()

        def apply(field: str, before: float, after: float, reason: str) -> float:
            notes.add(field, before, after, reason)
            return after

        friction = _finite("friction_comp", cmd.friction_comp)
        kd_neg_max = max(0.0, limits.kd_negative_max)
        fc_max = max(0.0, min(limits.friction_comp_max, t_max))
        kp = apply("kp", kp, _clip(kp, r.kp[0], kp_max), "tier" if kp > kp_max else "range")
        kd_req = kd
        kd = apply("kd", kd, _clip(kd, -kd_neg_max, kd_max), "tier")
        friction = apply("friction_comp", friction, _clip(friction, 0.0, fc_max), "tier")
        t_ff = apply("torque", t_ff, _clip(t_ff, -t_max, t_max), "tier")
        v_set = apply("velocity", v_set, _clip(v_set, -v_max, v_max), "tier")
        if frame == "wrapped":
            # ソフトリミットは多回転座標。コマンド座標(折り返し位置基準)へ変換してからレンジで切る
            p_set = apply("position", p_set, self._soft_clip(p_set), "soft_limit")
            p_conv = p_w + (p_set - p_m)
            p_set = apply("position", p_conv, _clip(p_conv, *r.position), "range")
        else:
            p_set = apply("position", p_set, _clip(p_set, *r.position), "range")
            p_set = apply("position", p_set, self._soft_clip(p_set), "soft_limit")

        # --- ホスト側項(負の kd・摩擦補償)と暴走ガード ---
        kd_fw = max(kd, 0.0)
        kd_host = min(kd, 0.0)
        host_wanted = kd_host < 0.0 or friction > 0.0
        mid = state.motor_id
        if host_wanted:
            if mid in self._runaway:
                if abs(v) < mh.runaway_off * v_max:
                    self._runaway.discard(mid)
            elif abs(v) > mh.runaway_on * v_max:
                self._runaway.add(mid)
        else:
            self._runaway.discard(mid)
        guard = mid in self._runaway
        if guard:
            # 項を捨て、フレームの kd を guard_kd 以上へ(段階の kd 上限まで)
            kd_fw = max(kd_fw, min(mh.guard_kd, kd_max))
            notes.add("kd", kd_req, kd_fw, "runaway_guard")
            notes.add("friction_comp", friction, 0.0, "runaway_guard")
            kd_host = 0.0
            friction = 0.0
        t_host = kd_host * (v_set - v) + friction * math.tanh(v / mh.friction_v_eps)
        if t_host != 0.0:
            t_before = t_ff
            t_ff = _clip(t_ff + t_host, -t_max, t_max)
            if t_ff != t_before + t_host:  # 合計が段階のトルク上限を超えた
                notes.add("torque", t_before + t_host, t_ff, "tier")
        kd = kd_fw  # 以降の最悪トルク見積りとフレームはフレームの kd で行う

        # --- 折り返し付近は位置目標を使わない ---
        if frame != "wrapped" and abs(p_m) > pos.wrap_limit and kp > 0.0:
            kp = apply("kp", kp, 0.0, "position_wrap")

        # --- 最悪トルク制限 ---
        def perr(p: float) -> float:
            return max(abs(p - ref) for ref in refs)

        def worst() -> float:
            return abs(kp * perr(p_set)) + abs(kd * (v_set - v)) + abs(t_ff)

        if worst() > t_max:
            # (1) t_ff を、PD 項の残り予算まで縮める(符号は保つ)
            pd = abs(kp * perr(p_set)) + abs(kd * (v_set - v))
            allowed_t = max(0.0, t_max - pd)
            if abs(t_ff) > allowed_t:
                t_ff = apply("torque", t_ff, math.copysign(allowed_t, t_ff), "worst_case")
        if worst() > t_max:
            # (2) p_set を参照位置寄りへ寄せる(すべての参照位置から budget/kp 以内)
            budget_p = max(0.0, t_max - abs(kd * (v_set - v)) - abs(t_ff))
            if kp > 0.0 and abs(kp) * perr(p_set) > budget_p:
                reach = budget_p / kp
                lo = max(ref - reach for ref in refs)
                hi = min(ref + reach for ref in refs)
                if lo <= hi:
                    p_set = apply("position", p_set, _clip(p_set, lo, hi), "worst_case")
                else:  # 参照位置どうしが離れていて、どの p_set でも予算に収まらない
                    kp = apply("kp", kp, 0.0, "position_wrap")
        if worst() > t_max:
            # (3) v_set を v 側へ寄せる
            budget_v = max(0.0, t_max - abs(kp * perr(p_set)) - abs(t_ff))
            dv = v_set - v
            if kd > 0.0 and abs(kd * dv) > budget_v:
                v_set = apply("velocity", v_set, v + math.copysign(budget_v / kd, dv), "worst_case")

        out = MitCmd(position=p_set, velocity=v_set, kp=kp, kd=kd, torque=t_ff)  # friction_comp = 0
        return out, notes.result()

    # -- 速度 / 電流 / 位置 -----------------------------------------------------
    def clamp_setpoint(
        self, cmd: SetpointCmd, state: MotorState | None = None
    ) -> tuple[SetpointCmd, list[ClampNote]]:
        """速度 / 電流 / 位置の目標をクランプする。

        電流モード(kind="current")だけは、`state`(最新のフィードバック)があれば暴走ガードを通す:
        ファームの limit_spd は電流モードを制限しない(実機: LOW でも 23.9 rad/s まで加速した)ので、
        |v| > runaway_on × 段階の速度上限で iq_ref を制動方向 `-sign(v)·min(|iq|, i_max)` に
        置き換え、
        |v| < runaway_off × 段階の速度上限になるまで保つ(ヒステリシス。ClampNote reason =
        "runaway_guard"、field = "value")。ラッチは MIT のホスト側項と同じ(モータごと)。
        """
        if cmd.kind not in _KINDS:
            raise SafetyError(f"未知の指令種別: {cmd.kind!r}")
        value = _finite("value", cmd.value)
        _, i_max, v_max, _, _ = self._effective(self._cur[1])
        notes = _Notes()

        if cmd.kind == "velocity":
            new_value = _clip(value, -v_max, v_max)
            notes.add("value", value, new_value, "tier")
        elif cmd.kind == "current":
            new_value = _clip(value, -i_max, i_max)
            notes.add("value", value, new_value, "tier")
            if state is not None:
                v = _finite("state.velocity", state.velocity)
                mh = self.profile.mit_host
                mid = state.motor_id
                if mid in self._runaway:
                    if abs(v) < mh.runaway_off * v_max:
                        self._runaway.discard(mid)
                elif abs(v) > mh.runaway_on * v_max:
                    self._runaway.add(mid)
                if mid in self._runaway:
                    brake = -math.copysign(min(abs(new_value), i_max), v)
                    notes.add("value", value, brake, "runaway_guard")
                    new_value = brake
        else:
            new_value = self._soft_clip(value)
            notes.add("value", value, new_value, "soft_limit")

        speed_limit = self._limit_field("speed_limit", cmd.speed_limit, v_max, notes)
        current_limit = self._limit_field("current_limit", cmd.current_limit, i_max, notes)
        accel = cmd.accel
        if accel is not None:
            accel = _finite("accel", accel)
            if accel < 0.0:
                raise SafetyError(f"accel が負です: {accel!r}")

        out = SetpointCmd(
            kind=cmd.kind,
            value=new_value,
            speed_limit=speed_limit,
            current_limit=current_limit,
            accel=accel,
        )
        return out, notes.result()

    @staticmethod
    def _limit_field(name: str, v: float | None, cap: float, notes: _Notes) -> float | None:
        if v is None:
            return None
        f = _finite(name, v)
        if f < 0.0:
            raise SafetyError(f"{name} が負です: {f!r}")
        new = min(f, cap)
        notes.add(name, f, new, "tier")
        return new

    # -- モータ側の二重制限 ------------------------------------------------------
    def motor_side_limits(self) -> dict[str, float]:
        """有効化時・段階変更時にモータへ書く値(0x700B / 0x7018 / 0x7017)。"""
        t, i, s, _, _ = self._effective(self._cur[1])
        return {"limit_torque": t, "limit_cur": i, "limit_spd": s}
