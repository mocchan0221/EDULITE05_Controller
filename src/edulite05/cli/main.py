"""CLI(`python -m edulite05.cli`、phase1.md §1.11)。

Qt を import しない。実機(`--port COMx`)でもシミュレータ(`--sim [N]`)でも同じ流れで動く。
運動コマンド(jog / velocity / current / mit / csp / sine / estop)は `cli/motion.py`、
シーケンス(run-sequence)は `cli/sequence_cmd.py`。
このファイルの read/enable/disable 系コマンドは運動目標を送らない。

方針:
- 出力は日本語。終了コード: 成功 0 / タイムアウト(応答なし)2 / その他 1。
- CLI は暗黙の停止指令を送らない: Motor は `Session.motor()` に登録せず(= Session.close の
  全モータ stop の対象にしない)直接生成する。`enable` した状態は `disable` するまで保たれ、
  `monitor` や `info` が他の操作で有効化されたモータを止めることもない。
- `monitor` は絶対に有効化しない。Ctrl+C / 終了時は主動上報を OFF にする。
- 運転系パラメータ(`MOTION_PARAM_KEYS` + iq_test)と type 1 の生送信は拒否する(`param set` では
  書かず、運動コマンド = SafetyGuard 経由で動かす)。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from edulite05.core import clock
from edulite05.core.control.service import MOTION_PARAM_KEYS
from edulite05.core.device.motor import Motor
from edulite05.core.device.profile import GROUPS, MotorProfile, ParamDef
from edulite05.core.device.state import MotorState
from edulite05.core.errors import EduliteError
from edulite05.core.protocol.faults import describe_feedback_faults
from edulite05.core.protocol.ids import CommType, make_id
from edulite05.core.protocol.private import ParamResponse
from edulite05.core.protocol.types import MotorMode, RunMode
from edulite05.core.session import Session
from edulite05.core.transport import registry
from edulite05.core.transport.base import CanFrame, CanTransport
from edulite05.core.transport.framelog import format_line

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 2

# `param set` で書かせないキー: コアの拒否リスト(MotorService.write_param と同じ)+ iq_test
# (iq_test は iq_ref と同じく電流を出すテスト用)。運転は運動コマンド(SafetyGuard 経由)で行う
MOTION_KEYS = MOTION_PARAM_KEYS | {"iq_test"}
STATUS_GROUP = "状態"

MODE_NAMES = {
    MotorMode.RESET: "停止 (Reset)",
    MotorMode.CALI: "校正中 (Cali)",
    MotorMode.MOTOR: "運転中 (Motor)",
}
RUN_MODE_NAMES = {
    RunMode.OPERATION: "運控 (MIT)",
    RunMode.PP: "PP",
    RunMode.VELOCITY: "速度",
    RunMode.CURRENT: "電流",
    RunMode.CSP: "CSP",
}


class CliError(Exception):
    """利用者に見せるエラー(終了コード 1)。"""


# ----------------------------------------------------------------------
# 表示ユーティリティ
# ----------------------------------------------------------------------
def disp_width(s: str) -> int:
    """端末での表示幅(全角 = 2)。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int, align: str = "<") -> str:
    fill = " " * max(0, width - disp_width(s))
    return fill + s if align == ">" else s + fill


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: str = "") -> str:
    """全角を考慮して桁を揃えた表。`aligns` は列ごとに "<" / ">"(省略は左)。"""
    cols = len(headers)
    al = (aligns + "<" * cols)[:cols]
    widths = [disp_width(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], disp_width(c))

    def line(cells: Sequence[str]) -> str:
        return "  ".join(pad(c, widths[i], al[i]) for i, c in enumerate(cells)).rstrip()

    out = [line(headers), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def fmt_value(v: int | float) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def fmt_hex(data: bytes) -> str:
    return data.hex(" ").upper()


def fault_text(bits: int) -> str:
    infos = describe_feedback_faults(bits)
    return "・".join(f.name for f in infos) if infos else "なし"


def nz(x: float) -> float:
    """-0.0 を 0.0 にする(表示で "-0.000" を出さない)。"""
    return x + 0.0


def mode_text(mode: MotorMode | None) -> str:
    return "-" if mode is None else MODE_NAMES.get(mode, str(mode))


# ----------------------------------------------------------------------
# 引数の解釈
# ----------------------------------------------------------------------
class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # 使い方の誤りも終了コード 1
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"エラー: {message}\n")


def parse_int(text: str) -> int:
    try:
        return int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"整数ではありません: {text!r}") from None


def parse_motor_id(text: str) -> int:
    v = parse_int(text)
    if not 1 <= v <= 127:
        raise argparse.ArgumentTypeError(f"モータ ID は 1–127 です: {v}")
    return v


def parse_ids(text: str) -> list[int]:
    """`1-127` / `1,5,10-20` / `0x7F` → 昇順の ID 一覧。"""
    out: set[int] = set()
    try:
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a, 0), int(b, 0)
                if lo > hi:
                    raise ValueError
                out.update(range(lo, hi + 1))
            else:
                out.add(int(part, 0))
    except ValueError:
        raise CliError(f"--ids を解釈できません: {text!r}(例: 1-127, 1,5,10-20)") from None
    if not out or min(out) < 0 or max(out) > 0xFF:
        raise CliError(f"--ids は 0–255 の範囲で指定してください: {text!r}")
    return sorted(out)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("接続")
    g.add_argument("--port", metavar="COMx", help="公式 USB-CAN のシリアルポート(例: COM7)")
    g.add_argument(
        "--sim",
        nargs="?",
        const=1,
        type=int,
        default=None,
        metavar="N",
        help="シミュレータ N 台(既定 1。ID は 127, 1, 2, ...)。ID を先に書くこと: info 127 --sim",
    )
    g.add_argument("--host", type=parse_int, default=0xFD, help="ホスト ID(既定 0xFD)")
    g.add_argument("--log", metavar="PATH", help="全 TX/RX フレームを candump 形式で記録する")
    g.add_argument("--timeout", type=float, default=0.2, help="要求ごとの応答待ち秒(既定 0.2)")

    p = _Parser(
        prog="python -m edulite05.cli",
        description="RobStride EL05 コマンドラインツール(読み出し・有効化/無効化・運動コマンド)",
    )
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser, metavar="コマンド")

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_, description=help_)

    add("ports", "シリアルポート一覧(★ = 公式 USB-CAN)")
    sp = add("scan", "モータ ID をスキャンして UID を表示")
    sp.add_argument("--ids", default="1-127", help="スキャン範囲(既定 1-127)")

    sp = add("info", "UID・バージョン・モード・故障を表示")
    sp.add_argument("id", type=parse_motor_id)

    sp = add("params", "全 0x70xx パラメータを表示")
    sp.add_argument("id", type=parse_motor_id)
    sp.add_argument("--group", choices=GROUPS, help="表示するグループ")
    sp.add_argument("--json", action="store_true", help="JSON で出力")

    sp = add("param", "パラメータの読み書き")
    psub = sp.add_subparsers(dest="action", required=True, parser_class=_Parser, metavar="操作")
    for act, h in (("get", "1 件読む"), ("set", "1 件書く(書込可能・範囲内のみ)")):
        a = psub.add_parser(act, parents=[common], help=h, description=h)
        a.add_argument("id", type=parse_motor_id)
        a.add_argument("key", help="キー(例: limit_spd)または index(例: 0x7017)")
        if act == "set":
            a.add_argument("value", help="書き込む値")
            a.add_argument(
                "--save",
                action="store_true",
                help="書込後にモータへ保存(type 22。0x70xx は保存されず電源を切ると戻る)",
            )

    sp = add("enable", "モータを有効化する(要 --yes)")
    sp.add_argument("id", type=parse_motor_id)
    sp.add_argument("--yes", action="store_true", help="確認(有効化を実行する)")

    sp = add("disable", "モータを停止(無効化)する")
    sp.add_argument("id", type=parse_motor_id)
    sp.add_argument("--clear-fault", action="store_true", help="故障をクリアする")

    sp = add("monitor", "主動上報を ON にして 1 行表示(有効化はしない)")
    sp.add_argument("id", type=parse_motor_id)
    sp.add_argument("--rate", type=float, default=10.0, help="表示更新 Hz(既定 10)")
    sp.add_argument("--seconds", type=float, default=None, help="表示する秒数(省略は Ctrl+C まで)")

    sp = add("raw", "生フレーム送信(デバッグ用。--i-know-what-im-doing が必要)")
    sp.add_argument("id", type=parse_motor_id)
    sp.add_argument("type", type=parse_int, help="通信タイプ 0–31")
    sp.add_argument("data2", type=parse_int, help="ID の bit23..8(0–0xFFFF)")
    sp.add_argument("hexdata", help="データ 0–8 バイトの 16 進(例: 00C4000000000000、無しは -)")
    sp.add_argument("--i-know-what-im-doing", dest="confirmed", action="store_true")
    from edulite05.cli import motion  # 遅延 import(motion は main の部品を使う)

    motion.add_parsers(add)
    from edulite05.cli import sequence_cmd

    sequence_cmd.add_parsers(add)
    return p


# ----------------------------------------------------------------------
# 接続
# ----------------------------------------------------------------------
def make_transport(args: argparse.Namespace) -> CanTransport:
    """--port / --sim から CanTransport を作る(テストが差し替える)。"""
    if args.sim is not None and args.port:
        raise CliError("--port と --sim は同時に指定できません")
    if args.sim is not None:
        if args.sim < 1:
            raise CliError("--sim の台数は 1 以上です")
        return registry.create("sim", n=args.sim)
    if args.port:
        return registry.create("robstride_at", port=args.port)
    raise CliError("接続先を指定してください: --port COM7 または --sim")


@contextlib.contextmanager
def open_session(args: argparse.Namespace) -> Iterator[Session]:
    transport = make_transport(args)
    log_path: str | None = None
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        log_path = args.log
    session = Session(
        transport, MotorProfile.load_builtin("el05"), host_id=args.host, framelog_path=log_path
    )
    session.open()
    try:
        yield session
    finally:
        session.close()


@contextlib.contextmanager
def detached_motor(session: Session, args: argparse.Namespace) -> Iterator[Motor]:
    """`Session.motor()` に登録しない Motor(終了時に暗黙の stop を送らせない)。"""
    motor = Motor(
        session.bus, args.id, session.profile, session.host_id, request_timeout=args.timeout
    )
    try:
        yield motor
    finally:
        motor.close()


def wait(fut: Future[Any], args: argparse.Namespace, extra: float = 3.0) -> Any:
    """Future の結果を待つ。RequestTimeout / TimeoutError は main() で終了コード 2 にする。"""
    return fut.result(timeout=max(args.timeout * 10, 1.0) + extra)


def probe_mode(motor: Motor, args: argparse.Namespace) -> MotorMode | None:
    """現在の mode を得る。主動上報 OFF の応答(type 2)で state.mode が更新される。"""
    try:
        wait(motor.set_active_report(False), args)
    except TimeoutError:
        return None
    return motor.state.mode


# ----------------------------------------------------------------------
# コマンド
# ----------------------------------------------------------------------
def cmd_ports(args: argparse.Namespace) -> int:
    from edulite05.core.transport.at_serial import list_ports

    ports = list_ports()
    if not ports:
        print("シリアルポートが見つかりません。")
        return EXIT_OK
    rows = [
        [
            "★" if p.preferred else "",
            p.device,
            f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None and p.pid is not None else "-",
            p.description,
        ]
        for p in ports
    ]
    print(format_table(["", "ポート", "VID:PID", "説明"], rows))
    print("★ = 公式 USB-CAN (CH340, 1A86:7523)")
    return EXIT_OK


def cmd_scan(args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids)
    with open_session(args) as session:
        print(f"スキャン中: ID {ids[0]}–{ids[-1]}({len(ids)} 件)...")
        settle = max(args.timeout, 0.3)
        # Session.scan は見つけたモータを登録する(= 終了時に stop が飛ぶ)ので bus を直接使う
        found = wait(session.bus.scan(ids, settle_s=settle), args, extra=len(ids) * 0.01 + 5.0)
    if not found:
        print("応答したモータはありません。")
        return EXIT_TIMEOUT
    rows = [[str(r.motor_id), f"0x{r.motor_id:02X}", fmt_hex(r.uid)] for r in found]
    print(format_table(["ID", "ID(16進)", "UID"], rows, ">>"))
    print(f"{len(found)} 台見つかりました。")
    return EXIT_OK


def cmd_info(args: argparse.Namespace) -> int:
    with open_session(args) as session, detached_motor(session, args) as motor:
        uid = wait(motor.get_uid(), args).uid
        mode = probe_mode(motor, args)
        run_mode_txt = "-"
        vbus_txt = "-"
        r = wait(motor.read_param("run_mode"), args)
        if r.ok and r.value is not None:
            try:
                run_mode_txt = RUN_MODE_NAMES[RunMode(int(r.value))]
            except ValueError:
                run_mode_txt = f"不明 ({r.value})"
        r = wait(motor.read_param("VBUS"), args)
        if r.ok and r.value is not None:
            vbus_txt = f"{float(r.value):.2f} V"
        # バージョンは type 4(= 停止指令)で読む。type 26 は EL05 では版数を返さない。
        # 運転中に type 4 を送ると止めてしまうので、Reset と確認できたときだけ読む。
        raw_txt = ""
        if mode == MotorMode.RESET:
            try:
                vi = wait(motor.read_version(), args)
                version_txt = vi.version
                raw_txt = fmt_hex(vi.raw)
            except TimeoutError:
                version_txt = "(取得不可: 応答なし)"
        elif mode is None:
            version_txt = "(取得不可: モード不明)"
        else:
            version_txt = "(取得不可: 運転中)"
        s = motor.state

    known = mode is not None
    rows = [
        ("ID", f"{args.id} (0x{args.id:02X})"),
        ("UID", fmt_hex(uid)),
        ("バージョン", version_txt),
    ]
    if raw_txt:
        rows.append(("  生バイト", f"{raw_txt}   ※10 進表記との対応は未確認"))
    pos_txt = f"{nz(s.position):+.3f} rad ({nz(math.degrees(s.position)):+.1f}°)"
    rows += [
        ("モード", mode_text(mode)),
        ("動作モード", run_mode_txt),
        ("故障", fault_text(s.fault_bits) if known else "-"),
        ("位置", pos_txt if known else "-"),
        ("温度", f"{s.temperature:.1f} ℃" if known else "-"),
        ("母線電圧", vbus_txt),
    ]
    w = max(disp_width(k) for k, _ in rows)
    for k, v in rows:
        print(f"{pad(k, w)} : {v}")
    return EXIT_OK


def cmd_params(args: argparse.Namespace) -> int:
    with open_session(args) as session, detached_motor(session, args) as motor:
        results: dict[int, ParamResponse] = wait(motor.read_all_params(), args, extra=10.0)
    defs = [p for p in session.profile.params if args.group in (None, p.group)]
    if args.json:
        out = []
        for p in defs:
            r = results.get(p.index)
            out.append(
                {
                    "index": f"0x{p.index:04X}",
                    "key": p.key,
                    "label": p.label,
                    "group": p.group,
                    "unit": p.unit,
                    "access": p.access,
                    "ok": bool(r is not None and r.ok and r.value is not None),
                    "value": r.value if r is not None and r.ok else None,
                }
            )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return EXIT_OK
    rows: list[list[str]] = []
    failed = 0
    for p in defs:
        r = results.get(p.index)
        if r is None:
            val = "応答なし"
            failed += 1
        elif not r.ok or r.value is None:
            val = "読出不可"
            failed += 1
        else:
            val = fmt_value(r.value)
        rows.append([f"0x{p.index:04X}", p.key, p.label, p.group, val, p.unit, p.access])
    print(format_table(["index", "key", "名称", "グループ", "値", "単位", "権限"], rows, "<<<<>"))
    ok = len(defs) - failed
    print(f"{len(defs)} 件中 {ok} 件を読み出しました(読出不可・応答なし {failed} 件)。")
    return EXIT_OK


def lookup_param(profile: MotorProfile, text: str) -> ParamDef:
    key: int | str = text
    with contextlib.suppress(ValueError):
        key = int(text, 0)
    try:
        return profile.param(key)
    except KeyError:
        raise CliError(
            f"未定義のパラメータです: {text}(`params` で一覧を確認してください)"
        ) from None


def cmd_param_get(args: argparse.Namespace) -> int:
    pd = lookup_param(MotorProfile.load_builtin("el05"), args.key)
    with open_session(args) as session, detached_motor(session, args) as motor:
        r = wait(motor.read_param(pd.index), args)
    if not r.ok or r.value is None:
        raise CliError(f"{pd.key} (0x{pd.index:04X}) は読み出せません(読出不可)")
    unit = f" {pd.unit}" if pd.unit else ""
    print(f"{pd.key} (0x{pd.index:04X}, {pd.label}) = {fmt_value(r.value)}{unit}")
    print(f"生データ: {fmt_hex(r.raw)}")
    return EXIT_OK


def parse_value(pd: ParamDef, text: str) -> int | float:
    try:
        if pd.dtype.is_integer:
            return int(text, 0)
        v = float(text)
    except ValueError:
        kind = "整数" if pd.dtype.is_integer else "数値"
        raise CliError(f"{pd.key}: {kind}として解釈できません: {text!r}") from None
    if not math.isfinite(v):
        raise CliError(f"{pd.key}: 有限の数値を指定してください: {text!r}")
    return v


def check_settable(pd: ParamDef, value: int | float) -> None:
    """書込の前提(読み取り専用・状態グループ・運動目標・範囲)を検査し、違反は CliError。"""
    if not pd.writable:
        raise CliError(f"{pd.key} (0x{pd.index:04X}) は読み取り専用のため書き込めません")
    if pd.group == STATUS_GROUP:
        raise CliError(f"{pd.key} は「{STATUS_GROUP}」グループのため書き込めません")
    if pd.key in MOTION_KEYS:
        raise CliError(
            f"{pd.key} は運転系パラメータのため param set では書き込めません"
            "(出力段階の制限を迂回できてしまうため。モータを動かすには velocity / current / "
            "mit / csp などの運動コマンドを使ってください)"
        )
    if pd.min is not None and value < pd.min:
        raise CliError(f"{pd.key}: {fmt_value(value)} は下限 {fmt_value(pd.min)} 未満です")
    if pd.max is not None and value > pd.max:
        raise CliError(f"{pd.key}: {fmt_value(value)} は上限 {fmt_value(pd.max)} を超えています")


def cmd_param_set(args: argparse.Namespace) -> int:
    pd = lookup_param(MotorProfile.load_builtin("el05"), args.key)
    value = parse_value(pd, args.value)
    check_settable(pd, value)  # 接続前に検査(拒否のときは何も送らない)
    unit = f" {pd.unit}" if pd.unit else ""
    with open_session(args) as session, detached_motor(session, args) as motor:
        old = wait(motor.read_param(pd.index), args)
        wait(motor.write_param(pd.index, value), args)
        new = wait(motor.read_param(pd.index), args)
        if args.save:
            wait(motor.save_params(), args)
    old_txt = fmt_value(old.value) if old.ok and old.value is not None else "読出不可"
    new_ok = new.ok and new.value is not None
    new_txt = fmt_value(new.value) if new_ok else "読出不可"  # type: ignore[arg-type]
    print(f"{pd.key} (0x{pd.index:04X}, {pd.label}): {old_txt} → {fmt_value(value)}{unit}")
    print(f"読み戻し: {new_txt}{unit}")
    if new_ok and not math.isclose(float(new.value), float(value), rel_tol=1e-5, abs_tol=1e-6):  # type: ignore[arg-type]
        print("警告: 読み戻した値が書込値と一致しません。")
    if args.save:
        print("モータへ保存しました(type 22)。")
    print(
        "注意: この EL05 では 0x70xx の運転パラメータは電源を切ると工場値に戻ります"
        "(type 22 の保存でも残りません。実測)。"
    )
    return EXIT_OK


def cmd_enable(args: argparse.Namespace) -> int:
    print(
        f"警告: ID {args.id} を有効化します(type 3)。有効化しただけでは回りませんが、"
        "出力段が通電します。モータが固定され、周囲に危険がないことを確認してください。"
    )
    print("      有効のまま終了します。停止は `disable` で行ってください。")
    if not args.yes:
        print("実行するには --yes を付けてください。何も送信しませんでした。")
        return EXIT_ERROR
    with open_session(args) as session, detached_motor(session, args) as motor:
        fb = wait(motor.enable(), args)
    print(f"有効化しました。モード: {mode_text(fb.mode)}  故障: {fault_text(fb.fault_bits)}")
    return EXIT_OK


def cmd_disable(args: argparse.Namespace) -> int:
    with open_session(args) as session, detached_motor(session, args) as motor:
        fb = wait(motor.disable(clear_fault=args.clear_fault), args)
    print(f"停止しました。モード: {mode_text(fb.mode)}  故障: {fault_text(fb.fault_bits)}")
    return EXIT_OK


def monitor_line(s: MotorState) -> str:
    return (
        f"位置 {nz(s.position):+8.3f} rad ({nz(math.degrees(s.position)):+7.1f}°) "
        f"多回転 {nz(s.position_multi):+9.3f} rad | "
        f"速度 {nz(s.velocity):+7.2f} rad/s | トルク {nz(s.torque):+6.2f} N·m | "
        f"温度 {s.temperature:5.1f} ℃ | {mode_text(s.mode)} | "
        f"故障 {fault_text(s.fault_bits)} | 受信 {s.rx_rate:4.0f} Hz"
    )


def report_off(motor: Motor, args: argparse.Namespace) -> bool:
    """主動上報を OFF にし、フレームが止まったことを確認する(最大 3 回再送)。"""
    for _ in range(3):
        try:
            wait(motor.set_active_report(False), args)
        except TimeoutError:
            pass
        except EduliteError:
            return False
        time.sleep(0.08)
        a = motor.state.last_rx
        time.sleep(0.12)
        if motor.state.last_rx == a:
            return True
    return False


def cmd_monitor(args: argparse.Namespace) -> int:
    if args.rate <= 0:
        raise CliError("--rate は正の数です")
    if args.seconds is not None and args.seconds < 0:
        raise CliError("--seconds は 0 以上です")
    interval = 1.0 / args.rate
    tty = sys.stdout.isatty()
    code = EXIT_OK
    with open_session(args) as session, detached_motor(session, args) as motor:
        width = 0
        try:
            # EPScan_time は書かない(N=0 → 10 ms 周期。周期 = 10 + 5·N ms、§8.0)。有効化もしない
            wait(motor.set_active_report(True), args)
            print("主動上報 ON(10 ms 周期)。Ctrl+C で終了します。モータは有効化しません。")
            try:  # 多回転を mechPos(0x7019)に合わせる(±4π の外で始めても多回転が正しい)
                wait(motor.sync_multi(), args)
            except (TimeoutError, EduliteError):
                print(
                    "警告: mechPos を読めないため、多回転はこの接続を始めた位置からの相対値です。",
                    file=sys.stderr,
                )
            t_end = None if args.seconds is None else clock.now() + args.seconds
            while t_end is None or clock.now() < t_end:
                line = monitor_line(motor.state)
                if tty:
                    width = max(width, disp_width(line))
                    sys.stdout.write("\r" + pad(line, width))
                else:
                    sys.stdout.write(line + "\n")
                sys.stdout.flush()
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        except TimeoutError:
            print("応答がありません(主動上報を開始できませんでした)。", file=sys.stderr)
            code = EXIT_TIMEOUT
        finally:
            try:
                ok = report_off(motor, args)
            except KeyboardInterrupt:
                ok = report_off(motor, args)
            if tty:
                sys.stdout.write("\n")
            if ok:
                print("主動上報を OFF にしました。")
            else:
                print("警告: 主動上報の停止を確認できませんでした。", file=sys.stderr)
                code = code or EXIT_ERROR
            sys.stdout.flush()
    return code


def cmd_raw(args: argparse.Namespace) -> int:
    if not args.confirmed:
        raise CliError(
            "raw はデバッグ用です。実行するには --i-know-what-im-doing を付けてください。"
        )
    if not 0 <= args.type <= 31:
        raise CliError("通信タイプは 0–31 です")
    if not 0 <= args.data2 <= 0xFFFF:
        raise CliError("data2 は 0–0xFFFF です")
    if args.type == CommType.MIT_CTRL:
        raise CliError(
            "type 1(運動制御)は raw では送信できません(運動コマンドを使ってください)"
        )
    hexs = "" if args.hexdata == "-" else args.hexdata.replace(" ", "")
    try:
        data = bytes.fromhex(hexs)
    except ValueError:
        raise CliError(f"HEXDATA を解釈できません: {args.hexdata!r}") from None
    if len(data) > 8:
        raise CliError("データは 8 バイトまでです")
    frame = CanFrame(make_id(args.type, args.data2, args.id), data)
    got: list[CanFrame] = []
    with open_session(args) as session:
        unsub = session.bus.subscribe(lambda m: got.append(m.frame))
        try:
            t0 = clock.now()
            session.bus.send(frame)
            time.sleep(max(args.timeout, 0.05))
        finally:
            unsub()
    wall = time.time() - clock.now()
    print(format_line(frame, t0 + wall) + "  (TX)")
    for f in got:
        print(format_line(f, f.timestamp + wall) + "  (RX)")
    if not got:
        print("応答はありませんでした。")
        return EXIT_TIMEOUT
    return EXIT_OK


# ----------------------------------------------------------------------
# エントリポイント
# ----------------------------------------------------------------------
def _dispatch(args: argparse.Namespace) -> Callable[[argparse.Namespace], int]:
    from edulite05.cli import motion, sequence_cmd

    if args.command in motion.COMMANDS:
        return motion.COMMANDS[args.command]
    if args.command in sequence_cmd.COMMANDS:
        return sequence_cmd.COMMANDS[args.command]
    if args.command == "param":
        return cmd_param_get if args.action == "get" else cmd_param_set
    return {
        "ports": cmd_ports,
        "scan": cmd_scan,
        "info": cmd_info,
        "params": cmd_params,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "monitor": cmd_monitor,
        "raw": cmd_raw,
    }[args.command]


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):  # 端末の文字コードで表せない文字は ? にする
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:  # --help(0)・使い方の誤り(1)。プロセス内呼び出しでも戻り値にする
        return e.code if isinstance(e.code, int) else EXIT_ERROR
    try:
        return _dispatch(args)(args)
    except TimeoutError as e:  # RequestTimeout を含む
        detail = f": {e}" if str(e) else ""
        print(f"エラー: 応答がありません(タイムアウト){detail}", file=sys.stderr)
        return EXIT_TIMEOUT
    except (CliError, EduliteError) as e:  # ProtocolError / TransportError などを含む
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
