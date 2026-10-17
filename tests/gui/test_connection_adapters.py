"""接続ポップオーバーの全アダプタ(公式 / シミュレータ / python-can / ログ再生)と、
ログ再生の読み出し専用(offscreen)。

- フォーム: registry に登録された種別を出し、使えないもの(python-can 未導入など)は理由つきで無効
- ログ再生: 小さなフレームログを CoreBridge で再生 → モータとテレメトリが出る / 何も送らない
  (指令・書込は拒否、画面は操作を無効にして「ログ再生中(送信しません)」を出す)
- python-can: interface "virtual"(導入済みのときだけ。無ければスキップ)
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from edulite05.app.core_bridge import CoreBridge
from edulite05.app.demo_bridge import DemoBridge
from edulite05.app.i18n import tr
from edulite05.app.main_window import MainWindow
from edulite05.app.pages.operate import connection_popover as cp
from edulite05.app.pages.operate.connection_popover import ConnectionForm, ConnectionPopover
from edulite05.app.ui.style import theme_manager
from edulite05.core.device.profile import MotorProfile
from edulite05.core.protocol.types import MotorMode
from edulite05.core.transport import registry
from edulite05.core.transport.base import Direction
from edulite05.core.transport.framelog import FrameLogWriter
from edulite05.sim.motor_model import SimMotor

WAIT = 8000  # ms
WALL = 1_696_000_000.0


# ---------------------------------------------------------------- フレームログの fixture
def _write_feedback_log(path: Path, ids: tuple[int, ...] = (1, 127), n: int = 40) -> None:
    """モータ ID ごとに type 2 のフィードバックを n 件(5 ms 間隔)。速度は ID に比例。"""
    profile = MotorProfile.load_builtin("el05")
    motors = {i: SimMotor(i, profile) for i in ids}
    with FrameLogWriter(path, wall_offset=WALL) as w:
        for k in range(n):
            for i, m in motors.items():
                m.mode = MotorMode.MOTOR
                m.position = 0.01 * k
                m.velocity = 2.0 if i == 1 else 4.0
                f = m.feedback_frame()
                w.write(replace(f, timestamp=100.0 + 0.005 * k, direction=Direction.RX))
        # 再生されない TX も混ぜる(読み出し専用の確認)
        w.write(replace(motors[ids[0]].feedback_frame(), timestamp=100.0, direction=Direction.TX))


@pytest.fixture
def framelog(tmp_path: Path) -> Path:
    p = tmp_path / "sample.log"
    _write_feedback_log(p)
    return p


@pytest.fixture
def no_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "list_serial_ports", lambda: [])


def _demo() -> DemoBridge:
    return DemoBridge(kind="simulator", demo_motion=False, seed=0)


# ---------------------------------------------------------------- フォーム
def test_form_offers_every_registered_adapter(qtbot, no_ports) -> None:
    form = ConnectionForm(_demo())
    qtbot.addWidget(form)
    keys = [form.adapter_combo.itemData(i) for i in range(form.adapter_combo.count())]
    assert keys == ["usbcan", "simulator", "pycan", "replay"]
    row = form.adapter_combo.findData("replay")
    assert form.adapter_combo.itemText(row) == tr("adapter.replay")
    assert form.adapter() == "simulator"  # 非実機の既定


def test_form_replay_fields_and_config(qtbot, no_ports, framelog: Path) -> None:
    form = ConnectionForm(_demo(), host_id=lambda: 0xFC)
    qtbot.addWidget(form)
    form.show()
    form.adapter_combo.setCurrentIndex(form.adapter_combo.findData("replay"))
    assert form._replay_box.isVisible() and not form._pycan_box.isVisible()  # noqa: SLF001
    assert not form._usb_box.isVisible()  # noqa: SLF001
    # ログ未選択では接続できない(理由つき)
    assert form.invalid_reason() == tr("popover.need_log")
    form.path_edit.setText(str(framelog))
    form.speed_combo.setCurrentIndex(form.speed_combo.findData(2.0))
    form.loop_switch.setChecked(True)
    assert form.invalid_reason() == ""
    assert form.config() == {
        "adapter": "replay", "path": str(framelog), "speed": 2.0, "loop": True, "host_id": 0xFC,
    }


def test_form_replay_browse_picks_a_file(qtbot, no_ports, framelog: Path, monkeypatch) -> None:
    form = ConnectionForm(_demo())
    qtbot.addWidget(form)
    form.adapter_combo.setCurrentIndex(form.adapter_combo.findData("replay"))
    seen: list[bool] = []
    form.browsing.connect(seen.append)
    monkeypatch.setattr(
        cp.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(framelog), ""))
    )
    form.browse_btn.click()
    assert form.path_edit.text() == str(framelog) and seen == [True, False]


def test_form_pycan_fields_and_config(qtbot, no_ports) -> None:
    form = ConnectionForm(_demo())
    qtbot.addWidget(form)
    form.show()
    form.adapter_combo.setCurrentIndex(form.adapter_combo.findData("pycan"))
    assert form._pycan_box.isVisible() and not form._replay_box.isVisible()  # noqa: SLF001
    assert form.interface_combo.findText("virtual") >= 0
    assert form.interface_combo.findText("slcan") >= 0
    form.interface_combo.setCurrentText("slcan")
    form.channel_edit.setText("COM5")
    form.bitrate_combo.setCurrentIndex(form.bitrate_combo.findData(500_000))
    assert form.config() == {
        "adapter": "pycan", "interface": "slcan", "channel": "COM5", "bitrate": 500_000,
        "host_id": 0xFD,
    }
    form.interface_combo.setCurrentText("")
    assert form.invalid_reason() == tr("popover.need_interface")


def test_form_unavailable_adapter_is_disabled_with_reason(qtbot, no_ports, monkeypatch) -> None:
    real = registry.available()
    reason = "python-can が未導入です(テスト)"
    fake = [
        replace(i, available=False, unavailable_reason=reason) if i.name == "pycan" else i
        for i in real
    ]
    monkeypatch.setattr(cp.registry, "available", lambda: fake)
    form = ConnectionForm(_demo())
    qtbot.addWidget(form)
    row = form.adapter_combo.findData("pycan")
    item = form.adapter_combo.model().item(row)
    assert not item.isEnabled()  # 選べない
    assert tr("adapter.unavailable", name=tr("adapter.pycan")) == form.adapter_combo.itemText(row)
    assert form.adapter_combo.itemData(row, Qt.ItemDataRole.ToolTipRole) == reason
    assert reason in form.unavailable_label.text() and form.unavailable_label.isVisibleTo(form)
    # 他の種別は有効
    for key in ("usbcan", "simulator", "replay"):
        assert form.adapter_combo.model().item(form.adapter_combo.findData(key)).isEnabled()


def test_popover_connect_needs_a_log_for_replay(qtbot, no_ports, framelog: Path) -> None:
    bridge = _demo()
    bridge.start()
    pop = ConnectionPopover(bridge)
    qtbot.addWidget(pop)
    bridge.disconnect_adapter()
    pop.adapter_combo.setCurrentIndex(pop.adapter_combo.findData("replay"))
    assert not pop.connect_btn.isEnabled()
    assert pop.connect_btn.toolTip() == tr("popover.need_log")
    pop.form.path_edit.setText(str(framelog))
    assert pop.connect_btn.isEnabled()
    pop.adapter_combo.setCurrentIndex(pop.adapter_combo.findData("simulator"))
    assert pop.connect_btn.isEnabled()
    bridge.shutdown()


# ---------------------------------------------------------------- ログ再生(CoreBridge)
@pytest.fixture
def replay_bridge(qtbot, framelog: Path):
    b = CoreBridge(kind="simulator", start_offline=True)
    logs: list = []
    b.log_message.connect(logs.append)
    b.start()
    b.connect_adapter({"adapter": "replay", "path": str(framelog), "speed": 0.2, "loop": True})
    qtbot.waitUntil(lambda: len(b.motors()) == 2, timeout=WAIT)
    yield b, logs
    b.shutdown()


def test_replay_shows_motors_and_telemetry(qtbot, replay_bridge) -> None:
    b, _logs = replay_bridge
    info = b.connection_info()
    assert info.connected and info.read_only and not info.simulated
    assert info.adapter == tr("adapter.replay") and info.port == "sample.log"
    assert [m.id for m in b.motors()] == [1, 127]
    # テレメトリ: ログの速度がそのまま状態・グラフ用の時系列に出る
    qtbot.waitUntil(
        lambda: 1 in b.states() and 127 in b.states() and b.states()[127].velocity > 3.0,
        timeout=WAIT,
    )
    assert b.states()[1].velocity == pytest.approx(2.0, abs=0.1)
    assert b.states()[127].velocity == pytest.approx(4.0, abs=0.1)
    assert b.states()[1].ui_state == "disabled"  # 運転中に見せない(有効化の応答は無い)
    qtbot.waitUntil(lambda: b.scope_data(["m1.vel"], 5.0).get("m1.vel", ([], []))[0].size > 3,
                    timeout=WAIT)


def test_replay_refuses_motion_and_param_writes(qtbot, replay_bridge) -> None:
    b, logs = replay_bridge
    qtbot.waitUntil(lambda: 1 in b.states(), timeout=WAIT)
    refused = tr("core.read_only_refused")
    b.enable([1], 2)
    b.write_param(1, 0x7014, 0.5)
    b.run_tool("set_zero", 1, {})
    assert [e.text for e in logs].count(refused) >= 3
    qtbot.wait(150)
    assert b.states()[1].ui_state == "disabled"  # 有効化されない
    # スキャンは再生側が何も返さないので、ログのモータをそのまま見せる(消さない)
    b.scan()
    qtbot.wait(100)
    assert [m.id for m in b.motors()] == [1, 127]


def test_replay_ui_is_read_only_with_banner(qtbot, framelog: Path) -> None:
    theme_manager().apply("dark")
    bridge = CoreBridge(kind="simulator", start_offline=True)
    win = MainWindow(bridge, settings_path=None, enable_3d=False)
    qtbot.addWidget(win)
    win.show()
    bridge.start()
    try:
        assert not win.banners.bar("replay").isVisibleTo(win)
        bridge.connect_adapter({"adapter": "replay", "path": str(framelog), "speed": 0.2,
                                "loop": True})
        qtbot.waitUntil(lambda: len(bridge.motors()) == 2, timeout=WAIT)
        qtbot.waitUntil(lambda: 1 in bridge.states(), timeout=WAIT)
        bar = win.banners.bar("replay")
        assert bar.isVisibleTo(win) and "replay" in win.banners.active_keys()
        assert tr("banner.replay_title") == "ログ再生中(送信しません)"
        assert win.kind_label.text() == tr("status_bar.kind_replay")
        insp = win.page("operate").inspector
        insp.set_motor(1)
        insp.refresh()
        # 運転系の操作は押せない(理由つき)。モード選択も無効
        assert not insp.primary.isEnabled()
        assert insp.primary.toolTip() == tr("operate.action.readonly")
        assert not insp.mode_seg.button("velocity").isEnabled()
        assert not insp.trigger_primary()
        # パラメータの書込・保守操作も不可
        params = win.page("params")
        params.set_pending(0x7014, 0.5)
        assert params.lock_reason() == tr("param_page.lock_replay")
        assert not params.write_btn.isEnabled()
        maint = win.page("maintenance")
        assert maint.lock_reason() == tr("maint.reason_replay")
        # 切断するとバナーは消える
        bridge.disconnect_adapter()
        assert not win.banners.bar("replay").isVisibleTo(win)
    finally:
        win.close()
        bridge.shutdown()


def test_replay_missing_or_empty_log_fails_to_connect(qtbot, tmp_path: Path) -> None:
    b = CoreBridge(kind="simulator", start_offline=True)
    logs: list = []
    b.log_message.connect(logs.append)
    b.start()
    try:
        b.connect_adapter({"adapter": "replay", "path": ""})
        assert any(tr("connection.no_log") in e.text for e in logs)
        b.connect_adapter({"adapter": "replay", "path": str(tmp_path / "none.log")})
        qtbot.waitUntil(lambda: any("none.log" in e.text and "接続できませんでした" in e.text
                                    for e in logs), timeout=WAIT)
        assert not b.connection_info().connected
    finally:
        b.shutdown()


# ---------------------------------------------------------------- python-can(virtual)
def test_pycan_virtual_connects_and_finds_motors(qtbot) -> None:
    can = pytest.importorskip("can")
    from edulite05.core.transport.pycan import from_message, to_message

    channel = f"edulite05_test_{time.monotonic_ns()}"
    profile = MotorProfile.load_builtin("el05")
    motors = [SimMotor(3, profile), SimMotor(5, profile)]
    peer = can.Bus(interface="virtual", channel=channel)
    stop = threading.Event()

    def respond() -> None:  # 反対側にいるモータ役(要求に応答する)
        while not stop.is_set():
            msg = peer.recv(0.05)
            if msg is None:
                continue
            frame = from_message(msg, time.monotonic())
            if frame is None:
                continue
            for m in motors:
                for r in m.handle(frame):
                    peer.send(to_message(can, r))

    th = threading.Thread(target=respond, daemon=True)
    th.start()
    b = CoreBridge(kind="simulator", start_offline=True)
    b.start()
    try:
        b.connect_adapter({"adapter": "pycan", "interface": "virtual", "channel": channel,
                           "bitrate": 1_000_000})
        qtbot.waitUntil(lambda: [m.id for m in b.motors()] == [3, 5], timeout=WAIT)
        info = b.connection_info()
        assert info.connected and not info.read_only and not info.simulated
        assert info.adapter == tr("adapter.pycan") and info.port == f"virtual:{channel}"
    finally:
        b.shutdown()
        stop.set()
        th.join(2.0)
        peer.shutdown()


def test_pycan_without_interface_is_refused(qtbot) -> None:
    b = CoreBridge(kind="simulator", start_offline=True)
    logs: list = []
    b.log_message.connect(logs.append)
    b.start()
    try:
        b.connect_adapter({"adapter": "pycan", "interface": ""})
        assert any(tr("connection.no_interface") in e.text for e in logs)
        assert not b.connection_info().connected
    finally:
        b.shutdown()
