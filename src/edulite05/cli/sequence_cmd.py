"""CLI: `run-sequence FILE`(シーケンス YAML の実行。phase4_5.md M)。

`cli/motion.py` と同じ約束:

- **`--yes` 必須**(無いと何も送らず終了コード 1)。出力段階は **LOW のみ**(`--tier high` は不可)。
- 実行前に YAML を**すべて検証**する(問題は行番号つきで表示して終了コード 1。モータには触れない)。
- 終了時(完了・Ctrl+C・エラー・非常停止のいずれでも)に必ず **記録停止 → 運転停止 → 減速を待つ →
  無効化**(`SequenceRunner` の後始末)。さらに `MotorService.close()` と `Session.close()` が
  全モータへ stop を送る(三重の保険)。
- Ctrl+C は中断(後始末してから終了コード 1)。2 回目の Ctrl+C でも後始末を待つ。

指令は `MotorService`(SafetyGuard)経由で、`cli/motion.py` の運動コマンドと同じ経路。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from edulite05.cli.main import (
    EXIT_ERROR,
    EXIT_OK,
    CliError,
    open_session,
    parse_motor_id,
)
from edulite05.core.control.service import MotorService
from edulite05.core.safety import PowerTier
from edulite05.core.sequence import (
    Sequence,
    SequenceError,
    SequenceEvent,
    SequenceRunner,
    SequenceState,
    load_sequence,
)

JOIN_POLL_S = 0.1


def add_parsers(add: Callable[[str, str], argparse.ArgumentParser]) -> None:
    sp = add("run-sequence", "シーケンス(YAML)を実行する: 有効化 → 運転 → 待機 → 記録 → 無効化")
    sp.add_argument("file", help="シーケンスのファイル(例: sequences/velocity_steps.yaml)")
    sp.add_argument(
        "--id", type=parse_motor_id, default=None,
        help="モータ ID(省略時は YAML の motor)",
    )
    sp.add_argument("--yes", action="store_true", help="確認(モータが動くことを了承する)")
    sp.add_argument(
        "--tier", choices=["low"], default="low",
        help="出力段階(CLI は low のみ。high は受け付けません)",
    )
    sp.add_argument(
        "--records-dir", default=None, metavar="DIR",
        help="record の保存先(既定 user_data/records)",
    )


def cmd_run_sequence(args: argparse.Namespace) -> int:
    if getattr(args, "tier", "low") != "low":
        raise CliError("CLI のシーケンスは --tier low のみです")
    try:
        seq = load_sequence(args.file)
    except SequenceError as e:
        for p in e.problems:
            print(f"エラー: {p.format(Path(args.file).name)}")
        print(f"{Path(args.file).name}: {len([p for p in e.problems if not p.warning])} 件の問題が"
              "あるため実行しません。何も送信しませんでした。")
        return EXIT_ERROR
    motor_id = args.id if args.id is not None else seq.motor
    if motor_id is None:
        raise CliError("モータ ID を --id か YAML の motor で指定してください")
    for w in seq.warnings:
        print(f"注意: {w.format(Path(args.file).name)}")
    print(
        f"警告: ID {motor_id} を回します(「{seq.name}」{len(seq.steps)} ステップ、"
        f"待ち時間の合計 約 {seq.total_seconds:g} 秒)。"
        "モータが固定され、可動範囲に人や物がないことを確認してください。"
    )
    if not args.yes:
        print("実行するには --yes を付けてください。何も送信しませんでした。")
        return EXIT_ERROR

    code = EXIT_OK
    with open_session(args) as session:
        svc = MotorService(session, tier=PowerTier.LOW)
        try:
            lim = svc.guard.limits
            print(
                f"出力段階 LOW: トルク ≤ {lim.torque:g} N·m, 電流 ≤ {lim.current:g} A, "
                f"速度 ≤ {lim.speed:g} rad/s"
            )
            svc.open()
            code = _run(svc, seq, motor_id, args)
        finally:
            svc.close()
    return code


def _run(svc: MotorService, seq: Sequence, motor_id: int, args: argparse.Namespace) -> int:
    def on_event(ev: SequenceEvent) -> None:
        if ev.kind == "step":
            print(f"[{ev.index + 1}/{ev.total}] {ev.text}")
        elif ev.kind == "log":
            mark = "注意: " if ev.level != "info" else ""
            print(f"      {mark}{ev.text}")

    runner = SequenceRunner(
        svc, seq, motor_id, on_event=on_event,
        recordings_dir=args.records_dir if args.records_dir else _default_records_dir(),
    )
    runner.start()
    while True:
        try:
            if runner.join(JOIN_POLL_S):
                break
        except KeyboardInterrupt:  # 中断。後始末(運転停止・無効化)は Runner のワーカーが続ける
            print("Ctrl+C: シーケンスを中断します。")
            runner.abort("Ctrl+C")
    return _report(runner)


def _default_records_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "user_data" / "records"


def _report(runner: SequenceRunner) -> int:
    res = runner.result
    assert res is not None
    for p in res.problems:
        print(f"警告: 後始末に失敗: {p}(終了時に全モータへ stop を送ります)", file=sys.stderr)
    if res.recording is not None and res.recording.exists():
        print(f"記録: {res.recording}")
    tail = "運転停止 → 無効化しました。" if res.stopped and not res.problems else ""
    if res.ok:
        print(f"完了しました({res.steps_done} ステップ)。{tail}")
        return EXIT_OK
    label = {
        SequenceState.ABORTED: "中断しました",
        SequenceState.ESTOP: "非常停止で中断しました",
        SequenceState.FAILED: "失敗しました",
        SequenceState.COMPLETED: "完了しました",
    }[res.state]
    print(f"{label}: {res.message}(完了したステップ {res.steps_done} 件)。{tail}", file=sys.stderr)
    return EXIT_ERROR


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "run-sequence": cmd_run_sequence,
}

__all__ = ["COMMANDS", "add_parsers"]
