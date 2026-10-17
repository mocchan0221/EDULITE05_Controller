"""接続ポップオーバー(ui_design.md §5.6)と、未接続の空状態でも使う接続フォーム。

ヘッダーの接続ボタンから開くフライアウト(角丸 8・影あり):
- 未接続: アダプタ種別・COM ポート(CH340 は ★ 付きで先頭)+ 更新・
  `CAN 1 Mbps / ホスト 0xFD`(表示のみ)・[接続](強調)
- 接続中: 接続先とモータの検出状況・[モータを再スキャン]・[切断](通常ボタン)
シェルは `ConnectionPopover(bridge).show_below(anchor)` を呼ぶだけ。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.ports import list_serial_ports
from edulite05.app.ui.button import Button
from edulite05.app.ui.inputs import Combo, Switch
from edulite05.app.ui.label import Label
from edulite05.app.ui.popover import Popover
from edulite05.app.ui.style import make_font
from edulite05.app.ui.tokens import FS_CAPTION, SP_ICON, SP_RELATED
from edulite05.app.viewmodels import ConnectionInfo, MotorInfo
from edulite05.core.transport import registry

# UI の種別キー → registry の登録名。順序がコンボの並び(公式 / シミュレータ / 汎用 / ログ再生)
ADAPTER_KEYS: dict[str, str] = {
    "usbcan": "robstride_at",
    "simulator": "sim",
    "pycan": "pycan",
    "replay": "replay",
}
PYCAN_FALLBACK_INTERFACES = ("slcan", "gs_usb", "candle", "pcan", "socketcan", "virtual")
BITRATES = (1_000_000, 500_000, 250_000, 125_000)  # EL05 の CAN ボーレート(既定 1 Mbps)
REPLAY_SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 10.0)


def _bitrate_text(bps: int) -> str:
    return f"{bps // 1_000_000} Mbps" if bps >= 1_000_000 else f"{bps // 1000} kbps"


def _speed_text(v: float) -> str:
    return f"{v:g}×"


_WRAP_W = 300  # 折り返す説明文の幅(ポップオーバー 306 / 空状態 320 のどちらでも収まる値)


def _wrap_label(text: str, role: str) -> Label:
    """折り返す説明文。入れ子のレイアウトでは「幅から高さ」が効かず潰れるので、行数ぶんの高さを
    あらかじめ最小高さにする。"""
    lab = Label(text, role, wrap=True)
    flags = int(Qt.TextFlag.TextWordWrap)
    fm = QFontMetrics(make_font(FS_CAPTION))  # caption(12)。QSS が当たる前でも同じ大きさで測る
    lab.setMinimumHeight(fm.boundingRect(0, 0, _WRAP_W, 10_000, flags, text).height() + 2)
    return lab


class ConnectionForm(QWidget):
    """アダプタ種別 + 種別ごとの設定 + 固定設定の表示。ポップオーバーと空状態で共有する。

    種別は registry の登録済みトランスポート(公式 USB-CAN / シミュレータ / python-can / ログ再生)。
    依存が無く使えないものは無効にして理由を添える(`TransportInfo.unavailable_reason`)。
    """

    validity_changed = Signal(bool, str)  # 接続できる設定か, できない理由
    browsing = Signal(bool)  # ファイル選択ダイアログの前後(ポップオーバーが閉じるのを補うため)

    def __init__(
        self,
        bridge: Bridge,
        parent: QWidget | None = None,
        *,
        host_id: Callable[[], int] | None = None,
    ) -> None:
        """`host_id` は設定ページのホスト ID を返す関数。省略時は 0xFD(el05_protocol.md §1.3)。"""
        super().__init__(parent)
        self._host_id = host_id
        self._valid: tuple[bool, str] | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP_ICON)
        lay.addWidget(Label(tr("popover.adapter"), "caption"))
        self.adapter_combo = Combo()
        infos = {i.name: i for i in registry.available()}
        notes: list[str] = []
        for key, reg_name in ADAPTER_KEYS.items():
            info = infos.get(reg_name)
            if info is None:  # 登録が無い(sim の import 失敗など)ものは出さない
                continue
            name = tr(f"adapter.{key}")
            if info.available:
                self.adapter_combo.addItem(name, key)
                continue
            self.adapter_combo.addItem(tr("adapter.unavailable", name=name), key)
            row = self.adapter_combo.count() - 1
            item = self.adapter_combo.model().item(row)  # type: ignore[attr-defined]
            item.setEnabled(False)
            self.adapter_combo.setItemData(
                row, info.unavailable_reason, Qt.ItemDataRole.ToolTipRole
            )
            notes.append(tr("popover.unavailable_note", name=name, reason=info.unavailable_reason))
        first = "simulator" if bridge.backend_kind != "hardware" else "usbcan"
        self.adapter_combo.setCurrentIndex(max(0, self.adapter_combo.findData(first)))
        lay.addWidget(self.adapter_combo)
        # 使えない種別の理由(無効な項目はツールチップが出にくいので、常に見える所にも出す)
        self.unavailable_label = _wrap_label("\n".join(notes), "caption-tertiary")
        self.unavailable_label.setVisible(bool(notes))
        lay.addWidget(self.unavailable_label)
        lay.addSpacing(SP_ICON)

        # ---- 公式 USB-CAN: COM ポート ----
        self._usb_box = QWidget()
        ul = QVBoxLayout(self._usb_box)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(SP_ICON)
        ul.addWidget(Label(tr("popover.port"), "caption"))
        port_row = QHBoxLayout()
        port_row.setSpacing(SP_RELATED)
        self.port_combo = Combo()
        self.refresh_btn = Button(
            variant="subtle", icon="refresh-cw", tooltip=tr("popover.refresh")
        )
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.refresh_btn)
        ul.addLayout(port_row)
        lay.addWidget(self._usb_box)

        # ---- python-can: インタフェース・チャネル・ビットレート ----
        self._pycan_box = QWidget()
        pl = QVBoxLayout(self._pycan_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(SP_ICON)
        pl.addWidget(Label(tr("popover.interface"), "caption"))
        self.interface_combo = Combo()
        self.interface_combo.setEditable(True)  # 一覧に無い python-can の interface 名も使える
        pycan_info = infos.get("pycan")
        choices = pycan_info.options_schema.get("interface", {}).get("choices") \
            if pycan_info is not None else None
        for name in choices or PYCAN_FALLBACK_INTERFACES:
            self.interface_combo.addItem(str(name), str(name))
        pl.addWidget(self.interface_combo)
        pl.addWidget(Label(tr("popover.channel"), "caption"))
        self.channel_edit = QLineEdit()
        self.channel_edit.setPlaceholderText(tr("popover.channel_hint"))
        pl.addWidget(self.channel_edit)
        pl.addWidget(Label(tr("popover.bitrate"), "caption"))
        self.bitrate_combo = Combo()
        for bps in BITRATES:
            self.bitrate_combo.addItem(_bitrate_text(bps), bps)
        pl.addWidget(self.bitrate_combo)
        lay.addWidget(self._pycan_box)

        # ---- ログ再生: ファイル・速度・繰り返し ----
        self._replay_box = QWidget()
        rl = QVBoxLayout(self._replay_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(SP_ICON)
        rl.addWidget(Label(tr("popover.log_file"), "caption"))
        file_row = QHBoxLayout()
        file_row.setSpacing(SP_RELATED)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr("popover.log_placeholder"))
        self.browse_btn = Button(
            tr("popover.browse"), "standard", icon="folder-open", tooltip=tr("popover.browse_tip")
        )
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(self.browse_btn)
        rl.addLayout(file_row)
        rl.addWidget(Label(tr("popover.speed"), "caption"))
        self.speed_combo = Combo()
        for v in REPLAY_SPEEDS:
            self.speed_combo.addItem(_speed_text(v), v)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(1.0))
        rl.addWidget(self.speed_combo)
        loop_row = QHBoxLayout()
        loop_row.setSpacing(SP_RELATED)
        self.loop_switch = Switch(False)
        loop_row.addWidget(Label(tr("popover.loop"), "body"), 1)
        loop_row.addWidget(self.loop_switch)
        rl.addLayout(loop_row)
        rl.addWidget(_wrap_label(tr("popover.replay_note"), "caption-tertiary"))
        lay.addWidget(self._replay_box)

        lay.addSpacing(SP_ICON)
        self.info_label = Label("", "caption-tertiary")
        lay.addWidget(self.info_label)
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.browse_btn.clicked.connect(self.browse)
        self.adapter_combo.currentIndexChanged.connect(self._sync)
        self.interface_combo.editTextChanged.connect(self._sync)
        self.path_edit.textChanged.connect(self._sync)
        self.refresh_ports()

    def host_id(self) -> int:
        return int(self._host_id()) if self._host_id is not None else 0xFD

    def adapter(self) -> str:
        """選択中の種別キー(usbcan / simulator / pycan / replay)。"""
        return str(self.adapter_combo.currentData() or "")

    def config(self) -> dict[str, Any]:
        """`Bridge.connect_adapter` に渡す設定。"""
        self._update_info()
        kind = self.adapter()
        cfg: dict[str, Any] = {
            "adapter": kind,
            "port": self.port_combo.currentData() or "",
            "bitrate": 1_000_000,
            "host_id": self.host_id(),
        }
        if kind == "pycan":
            del cfg["port"]
            cfg.update(
                interface=self.interface_combo.currentText().strip(),
                channel=self.channel_edit.text().strip(),
                bitrate=int(self.bitrate_combo.currentData() or 1_000_000),
            )
        elif kind == "replay":
            del cfg["port"], cfg["bitrate"]
            cfg.update(
                path=self.path_edit.text().strip(),
                speed=float(self.speed_combo.currentData() or 1.0),
                loop=self.loop_switch.isChecked(),
            )
        return cfg

    def invalid_reason(self) -> str:
        """接続できない設定ならその理由(できるなら空)。"""
        kind = self.adapter()
        if kind == "replay" and not self.path_edit.text().strip():
            return tr("popover.need_log")
        if kind == "pycan" and not self.interface_combo.currentText().strip():
            return tr("popover.need_interface")
        return ""

    def refresh_ports(self) -> None:
        self.port_combo.clear()
        ports = list_serial_ports()
        for p in ports:
            mark = "★ " if p.preferred else ""
            self.port_combo.addItem(f"{mark}{p.device}  {p.description}", p.device)
        if not ports:
            self.port_combo.addItem(tr("connection.no_port"), "")
        self._sync()

    def browse(self) -> None:
        """フレームログ(candump -L 形式)を選ぶ。"""
        self.browsing.emit(True)
        try:
            path, _flt = QFileDialog.getOpenFileName(
                self.window(), tr("popover.pick_title"), self.path_edit.text(),
                tr("popover.log_filter"),
            )
        finally:
            self.browsing.emit(False)
        if path:
            self.path_edit.setText(path)

    def _update_info(self) -> None:
        key = "popover.fixed_info" if self.adapter() in ("usbcan", "simulator") \
            else "popover.host_info"
        self.info_label.setText(tr(key, host=self.host_id()))

    def _sync(self, *_a: object) -> None:
        kind = self.adapter()
        self._usb_box.setVisible(kind in ("usbcan", "simulator"))
        self._pycan_box.setVisible(kind == "pycan")
        self._replay_box.setVisible(kind == "replay")
        self.port_combo.setEnabled(kind == "usbcan")
        self.refresh_btn.setEnabled(kind == "usbcan")
        self._update_info()
        reason = self.invalid_reason()
        state = (not reason, reason)
        if state != self._valid:
            self._valid = state
            self.validity_changed.emit(*state)
        self.updateGeometry()


class ConnectionPopover(Popover):
    """未接続: フォーム + [接続]。接続中: 状態 + [モータを再スキャン] [切断]。"""

    def __init__(
        self,
        bridge: Bridge,
        parent: QWidget | None = None,
        *,
        host_id: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent, width=340)
        self._bridge = bridge
        self.body.addWidget(Label(tr("popover.title"), "heading"))

        # ---- 未接続の設定フォーム ----
        self._form_host = QWidget()
        host = QVBoxLayout(self._form_host)
        host.setContentsMargins(0, 0, 0, 0)
        host.setSpacing(SP_RELATED + 4)
        self.form = ConnectionForm(bridge, host_id=host_id)
        host.addWidget(self.form)
        self.connect_btn = Button(tr("popover.connect"), "accent", icon="plug")
        host.addWidget(self.connect_btn)
        self.body.addWidget(self._form_host)

        # ---- 接続中の要約 ----
        self._connected = QWidget()
        conn = QVBoxLayout(self._connected)
        conn.setContentsMargins(0, 0, 0, 0)
        conn.setSpacing(SP_RELATED)
        self.summary_label = Label("", "body", wrap=True)
        self.motors_label = Label("", "secondary", wrap=True)
        conn.addWidget(self.summary_label)
        conn.addWidget(self.motors_label)
        conn.addSpacing(SP_ICON)
        self.rescan_btn = Button(tr("popover.rescan"), "standard", icon="refresh-cw")
        self.disconnect_btn = Button(tr("popover.disconnect"), "standard", icon="unplug")
        conn.addWidget(self.rescan_btn)
        conn.addWidget(self.disconnect_btn)
        self.body.addWidget(self._connected)

        # 互換用の別名(シェル・テストから参照されうる)
        self.adapter_combo = self.form.adapter_combo
        self.port_combo = self.form.port_combo
        self.refresh_btn = self.form.refresh_btn

        self.connect_btn.clicked.connect(self._on_connect)
        self.form.validity_changed.connect(self._on_validity)
        self.form.adapter_combo.currentIndexChanged.connect(self._refit)
        self.form.browsing.connect(self._on_browsing)
        self.rescan_btn.clicked.connect(self._on_rescan)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        bridge.connection_changed.connect(self._on_connection)
        bridge.motors_changed.connect(self._on_motors)
        self._on_connection(bridge.connection_info())
        self._on_validity(not self.form.invalid_reason(), self.form.invalid_reason())

    def show_below(self, anchor: QWidget, *, align: str = "left") -> None:
        self._anchor = (anchor, align)
        self._fit()
        super().show_below(anchor, align=align)

    def _refit(self, *_a: object) -> None:
        """種別を切り替えると入力欄の数が変わるので、開いたまま高さを合わせる。"""
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """折り返す説明文があるので、幅から高さを決める(`adjustSize` だけだと数行ぶん足りない)。"""
        self.adjustSize()
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            self.resize(self.width(), max(self.height(), lay.heightForWidth(self.width())))

    def _on_validity(self, ok: bool, reason: str) -> None:
        """設定が足りないとき(ログ未選択など)は [接続] を押せなくして理由を言う。"""
        self.connect_btn.set_available(ok, reason)

    def _on_browsing(self, active: bool) -> None:
        """ファイル選択ダイアログを開くとポップアップが閉じるので、終わったら開き直す。"""
        if active:
            self._reopen = self.isVisible()
            return
        anchor = getattr(self, "_anchor", None)
        if getattr(self, "_reopen", False) and anchor is not None:
            self._reopen = False
            super().show_below(anchor[0], align=anchor[1])

    def config(self) -> dict[str, Any]:
        return self.form.config()

    def refresh_ports(self) -> None:
        self.form.refresh_ports()

    def _on_connect(self) -> None:
        if self.form.invalid_reason():
            return
        self._bridge.connect_adapter(self.config())
        self.hide()

    def _on_rescan(self) -> None:
        self._bridge.scan()
        self.motors_label.setText(tr("operate.conn.scanning"))
        self.hide()

    def _on_disconnect(self) -> None:
        self._bridge.disconnect_adapter()
        self.hide()

    def _on_connection(self, info: ConnectionInfo) -> None:
        self._form_host.setVisible(not info.connected)
        self._connected.setVisible(info.connected)
        if info.connected:
            detail = info.adapter + (f"  {info.port}" if info.port else "")
            self.summary_label.setText(tr("operate.conn.connected", detail=detail))
            self._on_motors(self._bridge.motors())
        self.adjustSize()

    def _on_motors(self, motors: list[MotorInfo]) -> None:
        if not self._bridge.connection_info().connected:
            return
        self.motors_label.setText(
            tr("operate.conn.motors", n=len(motors)) if motors else tr("operate.conn.no_motors")
        )
        self.adjustSize()
