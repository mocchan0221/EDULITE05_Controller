"""i18n カタログ: ソース中の tr キーがすべてカタログにあること。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from edulite05.app.i18n import catalog, tr

SRC = Path(__file__).resolve().parents[1] / "src" / "edulite05"
APP = SRC / "app"


def _sources() -> list[Path]:
    # i18n/__init__.py の docstring にある tr("key") は例示なので除外
    return [
        p for p in SRC.rglob("*.py")
        if "__pycache__" not in p.parts and p != APP / "i18n" / "__init__.py"
    ]


def test_tr_returns_text_and_formats() -> None:
    assert tr("header.estop") == "非常停止"
    assert tr("ui.motor_id", id=1) == "ID1"
    assert tr("status.target", id=1, name="A") == "操作対象: ID 1  A"


def test_missing_key_returns_key(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert tr("no.such.key") == "no.such.key"
    assert any("no.such.key" in r.message for r in caplog.records)


def test_all_literal_keys_defined() -> None:
    cat = catalog()
    missing: set[str] = set()
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'\btr\(\s*"([a-z_0-9.]+)"', text):
            if m.group(1) not in cat:
                missing.add(f"{path.name}: {m.group(1)}")
    assert not missing, sorted(missing)


def test_keylike_literals_defined() -> None:
    """tr() の外で辞書/タプルに書かれたキー("section.name" 形式)も定義済みであること。"""
    cat = catalog()
    sections = {k.split(".")[0] for k in cat}
    pat = re.compile(r'"([a-z_]+(?:\.[a-z_0-9]+)+)"')
    missing: set[str] = set()
    for path in [*APP.rglob("*.py"), SRC / "core" / "control" / "params.py"]:
        for m in pat.finditer(path.read_text(encoding="utf-8")):
            key = m.group(1)
            if key.split(".")[0] in sections and key not in cat:
                missing.add(f"{path.name}: {key}")
    assert not missing, sorted(missing)


FAULTS = ("undervoltage", "overcurrent", "overtemp", "encoder", "overload", "uncalibrated")


@pytest.mark.parametrize(
    "prefix,names",
    [
        ("fault", FAULTS),
        ("fault.desc", FAULTS),
        ("mode", ("reset", "cali", "motor")),
        ("controller", ("mit", "velocity", "current", "csp", "pp", "jog", "sine", "step")),
        ("scope.axis", ("pos", "vel", "torque", "temp", "current", "power", "time")),
        ("status_bar", ("kind_mock", "kind_simulator", "kind_hardware")),
        ("tier.low", ("short",)),
        ("tier.high", ("short",)),
        ("motor_state", ("offline", "disabled", "enabled", "running", "fault")),
        ("fault.causes", FAULTS),
        ("fault.remedy", FAULTS),
        ("nav", ("operate", "params", "diagnostics", "maintenance", "settings")),
        ("banner", ("mock_title", "mock_text", "simulator_title", "simulator_text")),
        ("log", ("level_debug", "level_info", "level_warn", "level_error")),
    ],
)
def test_dynamic_key_families(prefix: str, names: tuple[str, ...]) -> None:
    cat = catalog()
    assert all(f"{prefix}.{n}" in cat for n in names)


def test_tab_tool_and_channel_keys() -> None:
    cat = catalog()
    for tab in ("mit", "velocity", "current", "csp", "pp", "jog", "test"):
        for part in ("tab", "title", "desc"):
            assert f"control.{tab}.{part}" in cat
    for tool in ("set_can_id", "set_zero", "active_report", "can_timeout", "set_baud",
                 "switch_protocol", "restore_protocol"):
        for part in ("title", "desc", "confirm", "warn"):
            assert f"tool.{tool}.{part}" in cat
    for kind in ("pos", "vel", "torque", "temp", "current", "power", "cmd_pos", "cmd_vel",
                 "cmd_torque", "cmd_current"):
        assert f"channel.{kind}" in cat
