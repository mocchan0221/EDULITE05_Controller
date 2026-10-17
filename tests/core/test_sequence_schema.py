"""シーケンス YAML の読込と検証(Qt・モータなし)。行番号つきの日本語エラー、順序・run_mode。"""

from __future__ import annotations

from pathlib import Path

import pytest

from edulite05.core.protocol.types import RunMode
from edulite05.core.sequence import (
    SequenceError,
    list_sequences,
    load_sequence,
    parse_sequence,
)
from edulite05.core.sequence.schema import (
    DisableStep,
    EnableStep,
    LogStep,
    RecordStep,
    RunStep,
    SetParamsStep,
    StartStep,
    StopStep,
    WaitStep,
)

ROOT = Path(__file__).resolve().parents[2]

OK = """\
name: テスト
description: 説明
motor: 127
steps:
  - enable: velocity
  - run: {controller: velocity, params: {velocity: 1.0}, seconds: 2}
  - start: {controller: velocity, params: {velocity: 0.5}}
  - set_params: {velocity: 1}
  - wait: 1.5
  - stop
  - record: start
  - record: stop
  - log: こんにちは
  - disable
"""


def problems(text: str) -> list[tuple[int | None, str]]:
    with pytest.raises(SequenceError) as ei:
        parse_sequence(text, source="t.yaml")
    return [(p.line, p.message) for p in ei.value.problems if not p.warning]


def test_parse_ok_structure() -> None:
    s = parse_sequence(OK, source="t.yaml")
    assert (s.name, s.description, s.motor) == ("テスト", "説明", 127)
    kinds = [type(x) for x in s.steps]
    assert kinds == [
        EnableStep, RunStep, StartStep, SetParamsStep, WaitStep, StopStep, RecordStep,
        RecordStep, LogStep, DisableStep,
    ]
    assert s.steps[0].mode is RunMode.VELOCITY
    assert s.steps[1].params.velocity == 1.0 and s.steps[1].duration == 2.0
    assert s.steps[3].changes == {"velocity": 1.0}
    assert s.total_seconds == pytest.approx(3.5)
    assert [x.line for x in s.steps][:2] == [5, 6]  # 行番号が残る
    assert s.steps[0].describe() == "有効化(速度)"


def test_defaults_name_from_file(tmp_path: Path) -> None:
    f = tmp_path / "abc.yaml"
    f.write_text("steps:\n  - wait: 1\n", encoding="utf-8")
    s = load_sequence(f)
    assert s.name == "abc" and s.motor is None


def test_sample_velocity_steps_valid_and_within_low() -> None:
    s = load_sequence(ROOT / "sequences" / "velocity_steps.yaml")
    assert s.warnings == ()  # 出力段階 LOW の上限内
    assert len(s.steps) >= 10 and s.total_seconds < 30
    assert isinstance(s.steps[0], EnableStep) and isinstance(s.steps[-1], DisableStep)


def test_list_sequences() -> None:
    names = [p.name for p in list_sequences(ROOT / "sequences")]
    assert "velocity_steps.yaml" in names
    assert list_sequences(ROOT / "no_such_dir") == []


def test_yaml_syntax_error_has_line() -> None:
    ps = problems("steps:\n  - enable: velocity\n   - wait: 1\n")
    assert ps[0][0] is not None and "YAML" in ps[0][1]


def test_tab_indent_hint() -> None:
    ps = problems("steps:\n\t- wait: 1\n")
    assert "タブ" in ps[0][1]


def test_empty_and_not_mapping() -> None:
    assert "空" in problems("")[0][1]
    assert "キー" in problems("- a\n- b\n")[0][1]


def test_missing_and_empty_steps() -> None:
    assert "steps" in problems("name: x\n")[0][1]
    assert "空" in problems("steps: []\n")[0][1]
    assert "一覧" in problems("steps: 3\n")[0][1]


def test_unknown_key_suggestion_and_line() -> None:
    ps = problems("name: x\nstepz:\n  - wait: 1\nsteps:\n  - wait: 1\n")
    assert ps[0][0] == 2 and "steps" in ps[0][1]
    ps = problems("steps:\n  - waitt: 1\n")
    assert ps[0][0] == 2 and "wait" in ps[0][1]


def test_step_needs_single_key_and_args() -> None:
    ps = problems("steps:\n  - wait: 1\n    stop: 1\n")
    assert ps[0][0] == 2 and "1 つ" in ps[0][1]
    ps = problems("steps:\n  - enable\n")
    assert "内容が必要" in ps[0][1]
    ps = problems("steps:\n  - stop: 3\n")
    assert "内容を書きません" in ps[0][1]


def test_enable_mode_validation() -> None:
    ps = problems("steps:\n  - enable: speed\n")
    assert ps[0][0] == 2 and "velocity" in ps[0][1] and "speed" in ps[0][1]


def test_order_checks() -> None:
    ps = problems(
        "steps:\n"
        "  - run: {controller: velocity, seconds: 1}\n"  # 2: enable 前
        "  - enable: velocity\n"
        "  - enable: current\n"  # 4: 二重
        "  - set_params: {velocity: 1}\n"  # 5: 運転中でない
        "  - start: {controller: velocity}\n"
        "  - start: {controller: velocity}\n"  # 7: すでに運転中
        "  - record: stop\n"  # 8: 記録していない
        "  - record: start\n"
        "  - record: start\n"  # 10: 二重
    )
    assert [p[0] for p in ps] == [2, 4, 5, 7, 8, 10]


def test_run_mode_mismatch() -> None:
    ps = problems("steps:\n  - enable: velocity\n  - run: {controller: current, seconds: 1}\n")
    assert ps[0][0] == 3 and "run_mode" in ps[0][1]


def test_sine_run_mode_follows_target() -> None:
    parse_sequence(
        "steps:\n  - enable: csp\n"
        "  - run: {controller: sine, params: {target: position, amplitude: 0.1}, seconds: 1}\n",
        source="t",
    )
    ps = problems(
        "steps:\n  - enable: velocity\n"
        "  - run: {controller: sine, params: {target: position}, seconds: 1}\n"
    )
    assert "run_mode" in ps[0][1]


def test_controller_validation() -> None:
    assert "veloctiy" in problems(
        "steps:\n  - enable: velocity\n  - run: {controller: veloctiy, seconds: 1}\n"
    )[0][1]
    assert "jog" in problems(
        "steps:\n  - enable: velocity\n  - run: {controller: jog, seconds: 1}\n"
    )[0][1]
    assert "1 台用" in problems(
        "steps:\n  - enable: mit\n  - run: {controller: bilateral, seconds: 1}\n"
    )[0][1]
    assert "controller" in problems("steps:\n  - enable: velocity\n  - run: {seconds: 1}\n")[0][1]


def test_params_validation_lines() -> None:
    ps = problems(
        "steps:\n"
        "  - enable: velocity\n"
        "  - run:\n"
        "      controller: velocity\n"
        "      params: {velocity: 99, accel: abc, bogus: 1}\n"
        "      seconds: 2\n"
    )
    assert {p[0] for p in ps} == {5}
    text = " ".join(p[1] for p in ps)
    assert "範囲" in text and "数値" in text and "bogus" in text


def test_seconds_validation() -> None:
    assert "0 より大きい" in problems(
        "steps:\n  - enable: velocity\n  - run: {controller: velocity, seconds: 0}\n"
    )[0][1]
    assert "秒数" in problems("steps:\n  - wait: abc\n")[0][1]
    assert "以下" in problems("steps:\n  - wait: 99999\n")[0][1]
    assert "0 以上" in problems("steps:\n  - wait: -1\n")[0][1]
    assert "seconds" in problems(
        "steps:\n  - enable: velocity\n  - run: {controller: velocity}\n"
    )[0][1]


def test_set_params_validation() -> None:
    base = "steps:\n  - enable: velocity\n  - start: {controller: velocity}\n"
    assert "bogus" in problems(base + "  - set_params: {bogus: 1}\n")[0][1]
    s = parse_sequence(base + "  - set_params: {accel: 5}\n", source="t")
    assert s.steps[-1].changes == {"accel": 5.0}


def test_target_cannot_change_while_running() -> None:
    ps = problems(
        "steps:\n  - enable: velocity\n"
        "  - start: {controller: sine, params: {target: velocity}}\n"
        "  - set_params: {target: current}\n"
    )
    assert "target" in ps[0][1]


def test_record_validation() -> None:
    assert "start か stop" in problems("steps:\n  - record: pause\n")[0][1]
    assert "path" in problems(
        "steps:\n  - record: start\n  - record: {action: stop, path: a}\n"
    )[0][1]
    s = parse_sequence("steps:\n  - record: {action: start, path: a.csv}\n", source="t")
    assert s.steps[0].path == "a.csv"
    # 記録を止め忘れ → 警告(エラーではない)
    assert [w.line for w in s.warnings] == [2]
    assert "record: stop" in s.warnings[0].message


def test_low_limit_warning_not_error() -> None:
    s = parse_sequence(
        "steps:\n  - enable: velocity\n"
        "  - run: {controller: velocity, params: {velocity: 8}, seconds: 1}\n",
        source="t",
    )
    assert len(s.warnings) == 1 and s.warnings[0].line == 3 and s.warnings[0].warning
    assert "LOW" in s.warnings[0].message


def test_motor_type() -> None:
    assert "motor" in problems("motor: 0\nsteps:\n  - wait: 1\n")[0][1]
    assert "motor" in problems("motor: abc\nsteps:\n  - wait: 1\n")[0][1]


def test_multiple_problems_reported_together() -> None:
    ps = problems("steps:\n  - enable: x\n  - waitt: 1\n  - wait: abc\n")
    assert [p[0] for p in ps] == [2, 3, 4]


def test_error_str_contains_source_and_line() -> None:
    with pytest.raises(SequenceError) as ei:
        parse_sequence("steps:\n  - enable: x\n", source="foo.yaml")
    assert "foo.yaml:行 2" in str(ei.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SequenceError) as ei:
        load_sequence(tmp_path / "nope.yaml")
    assert "読めません" in str(ei.value)
