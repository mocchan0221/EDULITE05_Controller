"""CLI の運動コマンド(phase2.md §1.7): jog / velocity / current / mit / csp / sine / estop。

すべて `MotorService` + 組込みコントローラ経由で動かす(SafetyGuard の制限を必ず通る)。

- **`--yes` 必須**(無いと何も送らず終了コード 1)。出力段階は **LOW のみ**(`--tier high` は不可)。
- 終了時(正常終了・Ctrl+C・例外のいずれでも)に必ず **運転停止 → 減速を待つ → 無効化** を行う
  (速度 0 指令の直後に無効化すると約 0.06 rad 惰走した実測のため、|速度| が小さくなるまで
  最大 `SETTLE_TIMEOUT_S` 待つ。Ctrl+C の再入・運転停止の失敗時は待たずに無効化)。さらに
  `MotorService.close()` と `Session.close()` も全モータへ stop を送る(三重の保険)。
- 実行中は `monitor` と同じ 1 行表示にクランプ(上限による制限)の注記を添える。
- `estop`: 範囲内の全 ID へ type 4 を一括送信する(スキャン不要・即時)。

main.py から `add_parsers` / `COMMANDS` を取り込む(循環 import を避けるため main は遅延 import)。
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from edulite05.cli.main import (
    EXIT_ERROR,
    EXIT_OK,
    CliError,
    disp_width,
    fault_text,
    mode_text,
    monitor_line,
    open_session,
    pad,
    parse_ids,
    parse_motor_id,
    wait,
)
from edulite05.core import clock
from edulite05.core.control.controller import Controller
from edulite05.core.control.controllers import (
    Csp,
    Current,
    Jog,
    MitDirect,
    SineTest,
    Velocity,
)
from edulite05.core.control.params import (
    CspParams,
    CurrentParams,
    JogParams,
    MitParams,
    SineParams,
    VelocityParams,
)
from edulite05.core.control.service import SERVICE_CLAMPED, MotorService, UiState
from edulite05.core.device.state import MotorState
from edulite05.core.protocol import private
from edulite05.core.protocol.private import Feedback
from edulite05.core.protocol.types import RunMode
from edulite05.core.safety import ClampNote, PowerTier

DISPLAY_HZ = 10.0
REACHED_TOL_RAD = 0.01  # csp: 目標到達とみなす誤差
REACHED_HOLD_S = 0.5
SETTLE_TIMEOUT_S = 0.5  # 終了時: 減速を待つ上限
SETTLE_SPEED_EPS = 0.05  # 終了時: この |速度| [rad/s] 未満で無効化へ進む


def _sleep(seconds: float) -> None:
    """表示ループの待ち(テストが Ctrl+C・例外を注入するために差し替える)。"""
    time.sleep(seconds)


@dataclass(slots=True)
class Plan:
    """1 回の運転の計画。`build` は有効化後の状態から Controller を作る(相対位置など)。"""

    title: str
    run_mode: RunMode
    seconds: float
    build: Callable[[MotorState], Controller]
    done: Callable[[MotorState], bool] | None = None  # True が REACHED_HOLD_S 続いたら終了
    on_tick: Callable[[Controller, float], None] | None = None  # (controller, 経過秒)


# ----------------------------------------------------------------------
# 引数
# ----------------------------------------------------------------------
def add_parsers(add: Callable[[str, str], argparse.ArgumentParser]) -> None:
    def common(sp: argparse.ArgumentParser, seconds: float) -> None:
        sp.add_argument("id", type=parse_motor_id)
        sp.add_argument(
            "--seconds", type=float, default=seconds, help=f"運転秒数(既定 {seconds:g})"
        )
        sp.add_argument("--yes", action="store_true", help="確認(モータが動くことを了承する)")
        sp.add_argument(
            "--tier",
            choices=["low"],
            default="low",
            help="出力段階(CLI は low のみ。high は受け付けません)",
        )

    sp = add("jog", "JOG: 一定速度で回す(速度モード。--reverse で逆転)")
    common(sp, 2.0)
    sp.add_argument("--speed", type=float, default=1.0, help="速度 rad/s(既定 1)")
    sp.add_argument("--reverse", action="store_true", help="逆方向")

    sp = add("velocity", "速度モード: 指定速度で回す")
    common(sp, 3.0)
    sp.add_argument("--speed", type=float, default=1.0, help="速度 rad/s(符号付き、既定 1)")
    sp.add_argument("--accel", type=float, default=20.0, help="加速度 rad/s²(既定 20)")
    sp.add_argument("--current-limit", type=float, default=1.0, help="電流制限 A(既定 1)")

    sp = add("current", "電流モード: 指定電流を流す")
    common(sp, 2.0)
    sp.add_argument("--iq", type=float, required=True, help="電流 A(符号付き)")

    sp = add("mit", "運控(MIT)モード: Kp/Kd/位置/トルクを指令。--damping で手回しの減衰を体感")
    common(sp, 3.0)
    sp.add_argument("--kp", type=float, default=5.0, help="Kp(既定 5。--damping では 0)")
    sp.add_argument("--kd", type=float, default=0.3, help="Kd(既定 0.3)")
    sp.add_argument("--position", type=float, default=None, help="目標位置 rad(省略は現在位置)")
    sp.add_argument("--velocity", type=float, default=0.0, help="目標速度 rad/s(既定 0)")
    sp.add_argument("--torque", type=float, default=0.0, help="フィードフォワードトルク N·m")
    sp.add_argument("--damping", action="store_true", help="Kp=0 の粘性ダンパ(Kd のみ)")

    sp = add("csp", "位置モード(CSP): 目標位置へ移動(速度制限付き)")
    common(sp, 5.0)
    sp.add_argument("--position", type=float, required=True, help="目標位置 rad")
    sp.add_argument("--speed-limit", type=float, default=1.0, help="速度制限 rad/s(既定 1)")
    sp.add_argument("--relative", action="store_true", help="現在位置からの相対位置にする")

    sp = add("sine", "正弦波の試験信号(対象量に出す)")
    common(sp, 6.0)
    sp.add_argument(
        "--target",
        choices=["velocity", "position", "current", "torque"],
        default="velocity",
        help="対象(position は現在位置からの相対、torque は MIT の t_ff)",
    )
    sp.add_argument("--amplitude", type=float, default=1.0, help="振幅(対象の単位)")
    sp.add_argument("--frequency", type=float, default=0.5, help="周波数 Hz")
    sp.add_argument("--offset", type=float, default=0.0, help="オフセット")

    sp = add("estop", "非常停止: 範囲内の全 ID へ stop(type 4)を一括送信する")
    sp.add_argument("--ids", default="1-127", help="対象 ID(既定 1-127。スキャンはしない)")


def _positive(name: str, v: float) -> None:
    if not math.isfinite(v) or v < 0:
        raise CliError(f"{name} は 0 以上の有限の数にしてください: {v}")


def _finite(name: str, v: float) -> None:
    if not math.isfinite(v):
        raise CliError(f"{name} は有限の数にしてください: {v}")


def plan_for(args: argparse.Namespace) -> Plan:
    _positive("--seconds", args.seconds)
    cmd = args.command
    if cmd == "jog":
        _positive("--speed", args.speed)
        direction = -1 if args.reverse else 1
        params = JogParams(speed=args.speed)

        def build_jog(_s: MotorState) -> Controller:
            jog = Jog(params)
            jog.set_direction(direction)
            return jog

        return Plan(
            f"JOG {'逆' if args.reverse else '正'}転 {args.speed:g} rad/s",
            RunMode.VELOCITY,
            args.seconds,
            build_jog,
        )
    if cmd == "velocity":
        _finite("--speed", args.speed)
        _positive("--accel", args.accel)
        _positive("--current-limit", args.current_limit)
        vp = VelocityParams(velocity=args.speed, accel=args.accel, current_limit=args.current_limit)
        return Plan(
            f"速度モード {args.speed:+g} rad/s",
            RunMode.VELOCITY,
            args.seconds,
            lambda _s: Velocity(vp),
        )
    if cmd == "current":
        _finite("--iq", args.iq)
        cp = CurrentParams(iq=args.iq)
        return Plan(
            f"電流モード {args.iq:+g} A", RunMode.CURRENT, args.seconds, lambda _s: Current(cp)
        )
    if cmd == "mit":
        for n, v in (("--kp", args.kp), ("--kd", args.kd), ("--velocity", args.velocity)):
            _finite(n, v)
        _finite("--torque", args.torque)
        if args.position is not None:
            _finite("--position", args.position)
        kp = 0.0 if args.damping else args.kp
        if args.damping and args.kd <= 0:
            raise CliError("--damping には --kd(> 0)を指定してください")
        if kp < 0 or args.kd < 0:
            raise CliError("--kp / --kd は 0 以上です")
        title = (
            f"MIT ダンピング Kd={args.kd:g}" if args.damping else f"MIT Kp={kp:g} Kd={args.kd:g}"
        )

        def build_mit(s: MotorState) -> Controller:
            pos = s.position_multi if args.position is None else args.position
            return MitDirect(
                MitParams(
                    position=pos if not args.damping else 0.0,
                    velocity=0.0 if args.damping else args.velocity,
                    kp=kp,
                    kd=args.kd,
                    torque_ff=args.torque,
                )
            )

        return Plan(title, RunMode.OPERATION, args.seconds, build_mit)
    if cmd == "csp":
        _finite("--position", args.position)
        _positive("--speed-limit", args.speed_limit)
        target_box: list[float] = []

        def build_csp(s: MotorState) -> Controller:
            target = args.position + (s.position_multi if args.relative else 0.0)
            target_box[:] = [target]
            return Csp(CspParams(position=target, speed_limit=args.speed_limit))

        def reached(s: MotorState) -> bool:
            return bool(target_box) and abs(s.position_multi - target_box[0]) < REACHED_TOL_RAD

        kind = "相対 " if args.relative else ""
        return Plan(
            f"CSP {kind}{args.position:+g} rad(速度制限 {args.speed_limit:g} rad/s)",
            RunMode.CSP,
            args.seconds,
            build_csp,
            reached,
        )
    if cmd == "sine":
        for n, v in (
            ("--amplitude", args.amplitude),
            ("--frequency", args.frequency),
            ("--offset", args.offset),
        ):
            _finite(n, v)
        if args.frequency <= 0:
            raise CliError("--frequency は正の数です")
        sp = SineParams(
            target=args.target,
            amplitude=abs(args.amplitude),
            frequency=args.frequency,
            offset=args.offset,
        )
        mode = SineTest(sp).run_mode
        return Plan(
            f"正弦波 {args.target} 振幅 {args.amplitude:g} {args.frequency:g} Hz",
            mode,
            args.seconds,
            lambda _s: SineTest(sp),
        )
    raise CliError(f"未対応のコマンド: {cmd}")  # pragma: no cover


# ----------------------------------------------------------------------
# 実行
# ----------------------------------------------------------------------
_FIELD_NAMES = {"value": "指令値"}


def _notes_text(notes: list[ClampNote]) -> str:
    return ", ".join(
        f"{_FIELD_NAMES.get(n.field, n.field)} {n.requested:g}→{n.applied:g}" for n in notes
    )


def _guarded(fn: Callable[[], Any], what: str, problems: list[str], tries: int = 3) -> None:
    """後始末の 1 手。Ctrl+C で中断されても再試行し、他の例外は記録して次の手へ進む。"""
    for _ in range(tries):
        try:
            fn()
            return
        except KeyboardInterrupt:
            continue
        except Exception as e:
            problems.append(f"{what}: {e!r}")
            return
    problems.append(f"{what}: 中断されました")


def _finish(svc: MotorService, motor_id: int, args: argparse.Namespace) -> list[str]:
    """必ず 運転停止 → 減速を待つ → 無効化。失敗しても次の手(close 時の stop)へ進む。

    運転停止に失敗した場合は減速を待たず(待っても止まらない)すぐ無効化する。"""
    problems: list[str] = []
    _guarded(lambda: svc.stop_motion([motor_id]).result(2.0), "運転停止", problems)

    def disable_now() -> None:
        svc.disable([motor_id]).result(wait_s(args))

    def settle_and_disable() -> None:
        try:
            svc.settle_and_disable([motor_id], SETTLE_TIMEOUT_S, SETTLE_SPEED_EPS).result(
                wait_s(args) + SETTLE_TIMEOUT_S
            )
        except KeyboardInterrupt:  # 待っている間の Ctrl+C: 待たずに無効化
            disable_now()

    _guarded(disable_now if problems else settle_and_disable, "無効化", problems)
    return problems


def wait_s(args: argparse.Namespace) -> float:
    return max(args.timeout * 10, 1.0) + 3.0


def run_motion(args: argparse.Namespace, plan: Plan) -> int:
    if getattr(args, "tier", "low") != "low":  # argparse の choices が先に弾くが念のため
        raise CliError("CLI の運動コマンドは --tier low のみです")
    print(
        f"警告: ID {args.id} を回します({plan.title}、{plan.seconds:g} 秒)。"
        "モータが固定され、可動範囲に人や物がないことを確認してください。"
    )
    if not args.yes:
        print("実行するには --yes を付けてください。何も送信しませんでした。")
        return EXIT_ERROR
    tty = sys.stdout.isatty()
    code = EXIT_OK
    with open_session(args) as session:
        svc = MotorService(session, tier=PowerTier.LOW)
        notes_now: list[ClampNote] = []
        notes_seen: list[str] = []
        motor = None

        def on_clamped(p: Any) -> None:
            if p["motor_id"] == args.id:
                notes_now[:] = p["notes"]

        unsub = session.events.subscribe(SERVICE_CLAMPED, on_clamped)
        try:
            lim = svc.guard.limits
            print(
                f"出力段階 LOW: トルク ≤ {lim.torque:g} N·m, 電流 ≤ {lim.current:g} A, "
                f"速度 ≤ {lim.speed:g} rad/s"
            )
            svc.open()
            wait(svc.enable(args.id, plan.run_mode), args, extra=5.0)
            motor = svc.motor(args.id)
            controller = plan.build(motor.state)
            svc.start([args.id], controller)
            print(f"{plan.title}: 運転開始。Ctrl+C で停止します。")
            code = _watch(svc, args, plan, controller, notes_now, notes_seen, tty)
        except KeyboardInterrupt:
            if tty:
                sys.stdout.write("\n")
            print("Ctrl+C: 運転を中断します。")
        finally:
            unsub()
            problems = _finish(svc, args.id, args)
            if tty:
                sys.stdout.write("\n")
            if problems:
                for p in problems:
                    print(
                        f"警告: 後始末に失敗: {p}(終了時に全モータへ stop を送ります)",
                        file=sys.stderr,
                    )
                code = code or EXIT_ERROR
            else:
                st = motor.state if motor is not None else None
                tail = (
                    f" モード: {mode_text(st.mode)}  故障: {fault_text(st.fault_bits)}"
                    if st
                    else ""
                )
                print(f"運転停止 → 無効化しました。{tail}")
            svc.close()
    return code


def _watch(
    svc: MotorService,
    args: argparse.Namespace,
    plan: Plan,
    controller: Controller,
    notes_now: list[ClampNote],
    notes_seen: list[str],
    tty: bool,
) -> int:
    motor = svc.motor(args.id)
    t0 = clock.now()
    held_since: float | None = None
    width = 0
    last_notes = ""
    interval = 1.0 / DISPLAY_HZ
    while True:
        now = clock.now()
        el = now - t0
        if el >= plan.seconds:
            return EXIT_OK
        if plan.on_tick is not None:
            plan.on_tick(controller, el)
        ui = svc.ui_state(args.id)
        st = motor.state
        notes = _notes_text(list(notes_now))
        line = monitor_line(st)
        if notes:
            line += f" | 制限中: {notes}"
        if tty:
            width = max(width, disp_width(line))
            sys.stdout.write("\r" + pad(line, width))
        else:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()
        if notes and notes != last_notes:
            notes_seen.append(notes)
            if tty:
                sys.stdout.write("\n")
            print(f"注意: 出力段階 LOW の上限で制限しました: {notes}")
            width = 0
        last_notes = notes
        if ui is not UiState.RUNNING:
            if tty:
                sys.stdout.write("\n")
            names = ", ".join(f.name for f in svc.fault_details(args.id))
            print(
                f"運転が止まりました(状態: {ui.value}{'、故障: ' + names if names else ''})。",
                file=sys.stderr,
            )
            return EXIT_ERROR
        if plan.done is not None:
            if plan.done(st):
                held_since = held_since or now
                if now - held_since >= REACHED_HOLD_S:
                    if tty:
                        sys.stdout.write("\n")
                    print("目標位置に到達しました。")
                    return EXIT_OK
            else:
                held_since = None
        _sleep(interval)


# ----------------------------------------------------------------------
# estop
# ----------------------------------------------------------------------
def cmd_estop(args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids)
    seen: dict[int, Feedback] = {}
    with open_session(args) as session:
        unsub = session.bus.subscribe(
            lambda m: seen.__setitem__(m.motor_id, m) if isinstance(m, Feedback) else None
        )
        try:
            frames = [private.stop(i, session.host_id) for i in ids]
            session.bus.send_now(frames)
            time.sleep(max(args.timeout, 0.05) + 0.1)
            session.bus.send_now(frames)  # 取りこぼし対策にもう一度
            time.sleep(max(args.timeout, 0.05))
        finally:
            unsub()
    print(f"ID {ids[0]}–{ids[-1]}({len(ids)} 件)へ stop(type 4)を送信しました。")
    for mid in sorted(seen):
        fb = seen[mid]
        print(f"  ID {mid}: モード {mode_text(fb.mode)}  故障 {fault_text(fb.fault_bits)}")
    if not seen:
        print("応答したモータはありませんでした(送信は完了しています)。")
    return EXIT_OK


def cmd_motion(args: argparse.Namespace) -> int:
    return run_motion(args, plan_for(args))


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "jog": cmd_motion,
    "velocity": cmd_motion,
    "current": cmd_motion,
    "mit": cmd_motion,
    "csp": cmd_motion,
    "sine": cmd_motion,
    "estop": cmd_estop,
}

__all__ = ["COMMANDS", "add_parsers"]
