"""CLI(`python -m edulite05.cli`)を SimTransport に対してプロセス内で実行する統合テスト。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edulite05.cli import main as cli
from edulite05.cli.main import build_parser, main
from edulite05.core.protocol.types import MotorMode
from edulite05.core.transport.framelog import read_framelog
from edulite05.sim.transport import SimTransport, make_motors

UID_TEXT = "40 99 3B C0 00 20 30 06"
T = ["--timeout", "0.1"]


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def shared_sim(monkeypatch: pytest.MonkeyPatch) -> SimTransport:
    """複数回の main() で同じシミュレータ(= 同じモータ状態)を使う。"""
    tr = SimTransport(make_motors(1))
    monkeypatch.setattr(cli, "make_transport", lambda args: tr)
    return tr


# ---------------------------------------------------------------- scan
def test_scan_finds_bench_motor(capsys) -> None:
    code, out, _ = run(capsys, "scan", "--sim", "--ids", "100-127", *T)
    assert code == 0
    assert "127" in out and "0x7F" in out and UID_TEXT in out
    assert "1 台見つかりました" in out


def test_scan_default_range_finds_multiple_sims(capsys) -> None:
    code, out, _ = run(capsys, "scan", "--sim", "3", *T)
    assert code == 0
    assert "3 台見つかりました" in out
    for mid in (127, 1, 2):
        assert f"0x{mid:02X}" in out


def test_scan_nothing_found_is_exit_2(capsys) -> None:
    code, out, _ = run(capsys, "scan", "--sim", "--ids", "1-10", *T)
    assert code == 2
    assert "応答したモータはありません" in out


def test_scan_bad_ids_is_exit_1(capsys) -> None:
    code, _, err = run(capsys, "scan", "--sim", "--ids", "abc")
    assert code == 1 and "--ids" in err


# ---------------------------------------------------------------- info
def test_info_in_reset_reads_version_and_raw_bytes(capsys) -> None:
    code, out, _ = run(capsys, "info", "127", "--sim", *T)
    assert code == 0
    assert UID_TEXT in out
    assert "10.5.0.2" in out
    assert "00 C4 56 0A 05 00 02 07" in out
    assert "停止 (Reset)" in out
    assert "故障" in out and "なし" in out
    assert "24." in out  # 母線電圧


def test_info_while_running_skips_version(shared_sim, capsys) -> None:
    assert run(capsys, "enable", "127", "--sim", "--yes", *T)[0] == 0
    assert shared_sim.motors[0].mode is MotorMode.MOTOR
    code, out, _ = run(capsys, "info", "127", "--sim", *T)
    assert code == 0
    assert "(取得不可: 運転中)" in out
    assert "10.5.0.2" not in out
    # info は運転中のモータを止めない(type 4 は版数読出のみ。運転中は送らない)
    assert shared_sim.motors[0].mode is MotorMode.MOTOR


def test_info_missing_motor_is_exit_2(capsys) -> None:
    code, _, err = run(capsys, "info", "5", "--sim", "--timeout", "0.05")
    assert code == 2 and "応答がありません" in err


# ---------------------------------------------------------------- params
def test_params_table_and_unreadable(capsys) -> None:
    code, out, _ = run(capsys, "params", "127", "--sim", *T)
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("index")
    assert any("run_mode" in ln and "0x7005" in ln for ln in lines)
    assert any("limit_spd" in ln and "50" in ln for ln in lines)
    # 実機で読めない 0x702C / 0x702D / 0x702E
    for key in ("alveolous_open", "iq_test", "dcc_set"):
        assert any(key in ln and "読出不可" in ln for ln in lines), key
    assert "読出不可" in lines[-1]


def test_params_group_filter(capsys) -> None:
    code, out, _ = run(capsys, "params", "127", "--sim", "--group", "制限", *T)
    assert code == 0
    assert "limit_spd" in out and "run_mode" not in out and "mechPos" not in out


def test_params_json(capsys) -> None:
    code, out, _ = run(capsys, "params", "127", "--sim", "--json", *T)
    assert code == 0
    by_key = {r["key"]: r for r in json.loads(out)}
    assert by_key["limit_spd"]["ok"] is True
    assert by_key["limit_spd"]["value"] == pytest.approx(50.0)
    assert by_key["iq_test"]["ok"] is False and by_key["iq_test"]["value"] is None
    assert by_key["run_mode"]["index"] == "0x7005"


# ---------------------------------------------------------------- param get / set
def test_param_get_by_key_and_index(capsys) -> None:
    code, out, _ = run(capsys, "param", "get", "127", "limit_spd", "--sim", *T)
    assert code == 0 and "limit_spd" in out and "50" in out and "rad/s" in out
    code, out, _ = run(capsys, "param", "get", "127", "0x7017", "--sim", *T)
    assert code == 0 and "limit_spd" in out


def test_param_get_unreadable_and_unknown(capsys) -> None:
    code, _, err = run(capsys, "param", "get", "127", "iq_test", "--sim", *T)
    assert code == 1 and "読出不可" in err
    code, _, err = run(capsys, "param", "get", "127", "nosuchkey", "--sim", *T)
    assert code == 1 and "未定義" in err


def test_param_set_writes_and_reads_back(shared_sim, capsys) -> None:
    code, out, _ = run(capsys, "param", "set", "127", "cur_filt_gain", "0.25", "--sim", *T)
    assert code == 0
    assert "→ 0.25" in out and "読み戻し: 0.25" in out
    assert "0x70xx" in out and "電源を切ると工場値に戻ります" in out
    assert "RAM のみ" not in out
    assert shared_sim.motors[0].get_param("cur_filt_gain") == pytest.approx(0.25)


def test_param_set_integer_and_save(shared_sim, capsys) -> None:
    code, out, _ = run(capsys, "param", "set", "127", "zero_sta", "1", "--sim", "--save", *T)
    assert code == 0 and "モータへ保存しました" in out
    assert "0x70xx" in out
    m = shared_sim.motors[0]
    assert m.get_param("zero_sta") == 1
    assert m.saved_params[0x7029] != 1  # 0x70xx は保存されない(HW-6)


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("mechPos", "1.0", "読み取り専用"),  # access == r(状態グループでもある)
        ("VBUS", "24", "読み取り専用"),
        ("cur_filt_gain", "2", "上限"),  # 範囲外
        ("cur_filt_gain", "-1", "下限"),
        ("cur_filt_gain", "nan", "有限"),
        ("run_mode", "abc", "整数"),
        # 運転系(コアの MOTION_PARAM_KEYS と同じ拒否リスト + iq_test)。理由を説明する
        ("run_mode", "2", "運転系パラメータ"),
        ("iq_ref", "1.0", "運転系パラメータ"),
        ("spd_ref", "1.0", "運転系パラメータ"),
        ("loc_ref", "1.0", "運転系パラメータ"),
        ("limit_torque", "1.0", "運転系パラメータ"),
        ("limit_cur", "1.0", "運転系パラメータ"),
        ("limit_spd", "12.5", "運転系パラメータ"),  # 出力段階の制限そのもの
        ("iq_test", "1", "運転系パラメータ"),
    ],
)
def test_param_set_refusals(shared_sim, capsys, key: str, value: str, needle: str) -> None:
    before = dict(shared_sim.motors[0].params)
    code, _, err = run(capsys, "param", "set", "127", key, value, "--sim", *T)
    assert code == 1 and needle in err
    assert shared_sim.motors[0].params == before  # 何も書かれていない


def test_param_set_run_mode_is_refused(shared_sim, capsys) -> None:
    code, _, err = run(capsys, "param", "set", "127", "run_mode", "4", "--sim", *T)
    assert code == 1 and "run_mode" in err
    assert shared_sim.motors[0].run_mode == 0


def test_cli_refusal_list_is_the_cores() -> None:
    """CLI は拒否リストを複製せず、コアの MOTION_PARAM_KEYS を取り込む(iq_test だけ追加)。"""
    from edulite05.cli.main import MOTION_KEYS
    from edulite05.core.control.service import MOTION_PARAM_KEYS

    assert MOTION_PARAM_KEYS <= MOTION_KEYS
    assert MOTION_KEYS - MOTION_PARAM_KEYS == {"iq_test"}


def test_param_set_run_mode_refused_while_running(shared_sim, capsys) -> None:
    # 運転中でも停止中でも run_mode は param set では書けない(運転系の拒否リスト)
    assert run(capsys, "enable", "127", "--sim", "--yes", *T)[0] == 0
    code, _, err = run(capsys, "param", "set", "127", "run_mode", "2", "--sim", *T)
    assert code == 1 and "運転系パラメータ" in err
    assert shared_sim.motors[0].run_mode == 0


# ---------------------------------------------------------------- monitor
def test_monitor_turns_report_off_and_never_enables(shared_sim, capsys) -> None:
    code, out, _ = run(capsys, "monitor", "127", "--sim", "--seconds", "0.3", "--rate", "20", *T)
    assert code == 0
    assert "主動上報 ON" in out and "主動上報を OFF にしました" in out
    lines = [ln for ln in out.splitlines() if ln.startswith("位置")]
    assert len(lines) >= 3
    for token in ("rad", "多回転", "rad/s", "N·m", "℃", "停止 (Reset)", "故障 なし", "Hz"):
        assert token in lines[-1], token
    m = shared_sim.motors[0]
    assert m.mode is MotorMode.RESET  # 有効化していない
    assert m.active_report is False


def test_monitor_shows_decoded_faults(shared_sim, capsys) -> None:
    shared_sim.motors[0].inject_fault(2)  # 低電圧
    code, out, _ = run(capsys, "monitor", "127", "--sim", "--seconds", "0.2", "--rate", "20", *T)
    assert code == 0
    last = [ln for ln in out.splitlines() if ln.startswith("位置")][-1]
    assert "故障 なし" not in last and "低電圧" in last


def test_monitor_missing_motor_is_exit_2(capsys) -> None:
    code, _, err = run(capsys, "monitor", "5", "--sim", "--seconds", "0.1", "--timeout", "0.05")
    assert code == 2 and "応答がありません" in err


# ---------------------------------------------------------------- enable / disable
def test_enable_requires_yes(shared_sim, capsys) -> None:
    code, out, _ = run(capsys, "enable", "127", "--sim", *T)
    assert code == 1
    assert "警告" in out and "--yes" in out
    assert shared_sim.motors[0].mode is MotorMode.RESET


def test_enable_yes_then_disable(shared_sim, capsys) -> None:
    code, out, _ = run(capsys, "enable", "127", "--sim", "--yes", *T)
    assert code == 0 and "警告" in out and "運転中 (Motor)" in out
    assert shared_sim.motors[0].mode is MotorMode.MOTOR  # 終了後も有効のまま
    code, out, _ = run(capsys, "disable", "127", "--sim", *T)
    assert code == 0 and "停止 (Reset)" in out
    assert shared_sim.motors[0].mode is MotorMode.RESET


def test_disable_clear_fault(shared_sim, capsys) -> None:
    shared_sim.motors[0].inject_fault(2)
    code, out, _ = run(capsys, "disable", "127", "--sim", "--clear-fault", *T)
    assert code == 0 and "故障: なし" in out


# ---------------------------------------------------------------- raw
def test_raw_is_refused_by_default(shared_sim, capsys) -> None:
    code, out, err = run(capsys, "raw", "127", "0", "0", "-", "--sim", *T)
    assert code == 1 and "--i-know-what-im-doing" in err
    assert out == ""


def test_raw_get_uid_when_confirmed(capsys) -> None:
    code, out, _ = run(
        capsys, "raw", "127", "0", "0xFD", "0000000000000000", "--sim", "--i-know-what-im-doing", *T
    )
    assert code == 0
    assert "(TX)" in out and "(RX)" in out
    assert "#40993BC000203006" in out


def test_raw_refuses_motion_type(shared_sim, capsys) -> None:
    code, _, err = run(
        capsys, "raw", "127", "1", "0", "0000000000000000", "--sim", "--i-know-what-im-doing"
    )
    assert code == 1 and "type 1" in err


def test_raw_no_response_is_exit_2(capsys) -> None:
    code, out, _ = run(
        capsys, "raw", "5", "0", "0", "-", "--sim", "--i-know-what-im-doing", "--timeout", "0.1"
    )
    assert code == 2 and "応答はありません" in out


# ---------------------------------------------------------------- 共通
def test_connection_options_are_validated(capsys) -> None:
    code, _, err = run(capsys, "info", "127")
    assert code == 1 and "--port" in err
    code, _, err = run(capsys, "info", "127", "--sim", "--port", "COM99")
    assert code == 1 and "同時" in err


def test_usage_errors_are_exit_1(capsys) -> None:
    assert run(capsys, "info", "0", "--sim")[0] == 1  # ID 範囲外
    assert run(capsys, "nosuchcommand")[0] == 1


def test_log_records_tx_and_rx_frames(tmp_path: Path, capsys) -> None:
    log = tmp_path / "sub" / "frames.log"
    code, _, _ = run(capsys, "param", "get", "127", "limit_spd", "--sim", "--log", str(log), *T)
    assert code == 0
    frames = list(read_framelog(log))
    assert any(f.direction.value == "tx" for f in frames)
    assert any(f.direction.value == "rx" for f in frames)
    # 壁時計の epoch 秒で書かれている
    assert all(f.timestamp > 1_600_000_000 for f in frames)


def test_command_set() -> None:
    """Phase 1 のコマンド + Phase 2 の運動コマンド(運動系は --yes 必須。test_cli_motion_sim.py)。"""
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    phase1 = {"ports", "scan", "info", "params", "param", "enable", "disable", "monitor", "raw"}
    motion = {"jog", "velocity", "current", "mit", "csp", "sine", "estop"}
    assert set(sub.choices) == phase1 | motion | {"run-sequence"}  # phase5-M(test_sequence_sim.py)


def test_ports_lists_without_connection(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from edulite05.core.transport import at_serial

    monkeypatch.setattr(
        at_serial,
        "list_ports",
        lambda: [at_serial.PortInfo("COM7", "USB-SERIAL CH340 (COM7)", 0x1A86, 0x7523, True)],
    )
    code, out, _ = run(capsys, "ports")
    assert code == 0 and "★" in out and "COM7" in out and "1A86:7523" in out
