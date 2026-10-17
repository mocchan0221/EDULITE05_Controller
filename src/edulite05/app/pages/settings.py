"""設定ページ(ui_design.md §5.5)。

テーマ(ダーク / ライト / システム)・角度の表示単位・グラフの既定(時間幅・プリセット)・
接続の既定値(ホスト ID)・プラグイン(一覧・有効/無効・読込エラー)・言語(日本語 / English)・
レイアウトの初期化。

プラグインの有効/無効は `PluginManager.set_enabled`(`user_data/ui_state.ini` の `plugins/` 以下)へ
保存し、次回の起動から反映する(その旨をページに書く)。プラグインが `add_settings_section` で
登録したグループは、プラグインの一覧の下に出す。

永続化は既存の設定ファイル(`user_data/ui_state.ini` の QSettings。シェルの `settings()`)を使い、
キーは `prefs/…` に置く。他のページは `SettingsPage.prefs`(`Prefs`)から値を読み、
`prefs.changed(key, value)` で変更に追従する。キーと型は `PREF_DEFAULTS`:

    prefs/theme_mode     "dark" | "light" | "system"(実際の配色は MainWindow が `theme` に保存)
    prefs/angle_unit     "rad" | "deg"
    prefs/graph_window_s 5 | 10 | 30 | 60 | 120 (秒)
    prefs/graph_preset   "standard" | "mit" | "env" | "all"
    prefs/host_id        0..255(既定 0xFD)
    prefs/language       "ja" | "en"(起動時に `app/main.py` が読む。切り替えは次回の起動から反映)

レイアウトの初期化は、ウィンドウの大きさ(`MainWindow.reset_layout`)と、QSettings の
`shell/` と `layout/` グループ(ページが区画の幅などを保存する場所)を消し、
`reset_layout()` を持つページを呼ぶ。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QValidator
from PySide6.QtWidgets import QSpinBox, QVBoxLayout, QWidget

from edulite05.app.bridge import Bridge
from edulite05.app.i18n import DEFAULT_LANG, LANGUAGE_NAMES, SUPPORTED, current_language, tr
from edulite05.app.pages.base import PageBase, ShellApi
from edulite05.app.pages.boxed_list import BoxedList, GroupHeader, ListRow, centered_scroll
from edulite05.app.ui.button import Button
from edulite05.app.ui.dialogs.base import ActionDialog
from edulite05.app.ui.infobar import InfoBar
from edulite05.app.ui.inputs import Combo, Switch
from edulite05.app.ui.label import Label
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.style import resolve_system_theme, theme_manager
from edulite05.app.ui.tokens import SP_GROUP, SP_RELATED, SP_SECTION
from edulite05.plugin_api import PluginManager, PluginRecord, PluginStatus
from edulite05.plugin_api.manager import SOURCE_BUILTIN, SOURCE_DROPIN

PREF_DEFAULTS: dict[str, Any] = {
    "theme_mode": "dark",
    "angle_unit": "rad",
    "graph_window_s": 10,
    "graph_preset": "standard",
    "host_id": 0xFD,  # el05_protocol.md §1.3(GUI 既定は 0xFD)
    "language": DEFAULT_LANG,
}
GRAPH_WINDOWS: tuple[int, ...] = (5, 10, 30, 60, 120)
GRAPH_PRESETS: tuple[tuple[str, str], ...] = (
    ("standard", "settings.preset_standard"),
    ("mit", "settings.preset_mit"),
    ("env", "settings.preset_temp"),
    ("all", "settings.preset_all"),
)
LAYOUT_GROUPS: tuple[str, ...] = ("shell", "layout")  # レイアウト初期化で消す QSettings のグループ


class Prefs(QObject):
    """設定値の読み書き。`source` は ini のパス、または `QSettings | None` を返す関数
    (シェルの `MainWindow.settings`)。None のときはメモリ上だけ(テスト・設定ファイル無し)。

    値は `PREF_DEFAULTS` の型に直して返す(ini には文字列で入るため)。
    """

    changed = Signal(str, object)  # key, value

    def __init__(
        self,
        source: Path | Callable[[], QSettings | None] | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._memory: dict[str, Any] = {}

    def _settings(self) -> QSettings | None:
        src = self._source
        if src is None:
            return None
        if callable(src):
            return src()
        src.parent.mkdir(parents=True, exist_ok=True)
        return QSettings(str(src), QSettings.Format.IniFormat)

    def get(self, key: str) -> Any:
        default = PREF_DEFAULTS[key]
        s = self._settings()
        raw = self._memory.get(key, default) if s is None else s.value(f"prefs/{key}", default)
        try:
            return type(default)(raw)
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any) -> None:
        if key not in PREF_DEFAULTS:
            raise KeyError(key)
        if self.get(key) == value and self.has(key):
            return
        s = self._settings()
        if s is None:
            self._memory[key] = value
        else:
            s.setValue(f"prefs/{key}", value)
            s.sync()
        self.changed.emit(key, value)

    def has(self, key: str) -> bool:
        s = self._settings()
        return key in self._memory if s is None else s.contains(f"prefs/{key}")

    def clear_layout(self) -> None:
        """レイアウトに関する保存値(`shell/`・`layout/`)を消す。"""
        s = self._settings()
        if s is None:
            return
        for group in LAYOUT_GROUPS:
            s.remove(group)
        s.sync()


class HostIdSpin(QSpinBox):
    """ホスト ID(0x00〜0xFF)。16 進(`0xFD`)で表示・入力する。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 255)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setProperty("mono", True)
        self.setFixedWidth(120)

    def textFromValue(self, value: int) -> str:  # noqa: N802
        return f"0x{value:02X}"

    def valueFromText(self, text: str) -> int:  # noqa: N802
        t = text.strip().lower().removeprefix("0x")
        try:
            return int(t, 16)
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):  # noqa: ANN201
        t = text.strip().lower().removeprefix("0x")
        if t == "":
            return QValidator.State.Intermediate, text, pos
        try:
            ok = 0 <= int(t, 16) <= 255
        except ValueError:
            return QValidator.State.Invalid, text, pos
        return (QValidator.State.Acceptable if ok else QValidator.State.Invalid), text, pos


class SettingsPage(PageBase):
    """設定ページ。変更はすぐ反映・保存する(「適用」ボタンは持たない)。"""

    def __init__(
        self, bridge: Bridge, shell: ShellApi | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(bridge, shell, parent)
        self._shut = False
        source = getattr(shell, "settings", None)  # MainWindow.settings(保存しない設定なら None)
        self.prefs = Prefs(source if callable(source) else None, self)

        root = QVBoxLayout(self)
        root.setContentsMargins(SP_SECTION, SP_SECTION, SP_SECTION, 0)
        root.setSpacing(SP_GROUP)
        self._scroll, self._column = centered_scroll()
        root.addWidget(self._scroll, 1)
        self.heading = Label(tr("nav.settings"), "title")
        self._column.addWidget(self.heading)

        self._build_appearance()
        self._build_graph()
        self._build_connection()
        self.plugin_rows: dict[str, ListRow] = {}
        self.plugin_switches: dict[str, Switch] = {}
        self.plugin_alerts: list[InfoBar] = []
        self._build_plugins()
        self._column.addStretch(1)

        theme_manager().changed.connect(self._on_theme_changed)
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_system_scheme)
        # シェルが保存済みの配色を復元した後(構築の最後)に、「システム」なら OS に合わせる
        QTimer.singleShot(0, self._apply_theme_mode_on_start)

    def shutdown(self) -> None:
        """ウィンドウを閉じるとき、配色の変化の購読を外す(閉じた後に設定を書き換えないため)。"""
        if self._shut:
            return
        self._shut = True
        hints = QGuiApplication.styleHints()
        try:
            theme_manager().changed.disconnect(self._on_theme_changed)
            if hints is not None:
                hints.colorSchemeChanged.disconnect(self._on_system_scheme)
        except (TypeError, RuntimeError):  # 二重に閉じたとき
            pass

    # ================================================================ 構築
    def _group(self, title: str) -> BoxedList:
        self._column.addSpacing(SP_SECTION - SP_RELATED)  # グループ間 > グループ内
        box = BoxedList()
        self._last_header = GroupHeader(title)
        self._column.addWidget(self._last_header)
        self._column.addWidget(box)
        return box

    def _build_appearance(self) -> None:
        mode = self.prefs.get("theme_mode") if self.prefs.has("theme_mode") else (
            theme_manager().name
        )
        self.theme_seg = Segmented(
            [
                ("dark", tr("settings.theme_dark"), "moon"),
                ("light", tr("settings.theme_light"), "sun"),
                ("system", tr("settings.theme_system")),
            ],
            current=mode if mode in ("dark", "light", "system") else "dark",
        )
        self.theme_seg.selected.connect(self.set_theme_mode)
        self.angle_seg = Segmented(
            [("rad", tr("settings.angle_rad")), ("deg", tr("settings.angle_deg"))],
            current=self.prefs.get("angle_unit"),
        )
        self.angle_seg.selected.connect(lambda k: self.prefs.set("angle_unit", k))
        box = self._group(tr("settings.group_appearance"))
        self.layout_button = Button(tr("settings.layout_button"), "standard")
        self.layout_button.clicked.connect(self.reset_layout)
        box.add_row(
            ListRow(tr("settings.theme"), tr("settings.theme_desc"), trailing=self.theme_seg)
        )
        box.add_row(
            ListRow(tr("settings.angle"), tr("settings.angle_desc"), trailing=self.angle_seg)
        )
        box.add_row(self._language_row())
        box.add_row(
            ListRow(tr("settings.layout"), tr("settings.layout_desc"), trailing=self.layout_button)
        )

    def _build_graph(self) -> None:
        self.window_combo = Combo()
        self.window_combo.setMinimumWidth(120)
        for sec in GRAPH_WINDOWS:
            self.window_combo.addItem(f"{sec} s", sec)
        self._select_data(self.window_combo, self.prefs.get("graph_window_s"))
        self.window_combo.activated.connect(
            lambda _i: self.prefs.set("graph_window_s", self.window_combo.currentData())
        )
        self.preset_combo = Combo()
        self.preset_combo.setMinimumWidth(240)
        for key, name_key in GRAPH_PRESETS:
            self.preset_combo.addItem(tr(name_key), key)
        self._select_data(self.preset_combo, self.prefs.get("graph_preset"))
        self.preset_combo.activated.connect(
            lambda _i: self.prefs.set("graph_preset", self.preset_combo.currentData())
        )
        box = self._group(tr("settings.group_graph"))
        box.add_row(
            ListRow(tr("settings.graph_window"), tr("settings.graph_window_desc"),
                    trailing=self.window_combo)
        )
        box.add_row(
            ListRow(tr("settings.graph_preset"), tr("settings.graph_preset_desc"),
                    trailing=self.preset_combo)
        )

    def _build_connection(self) -> None:
        self.host_spin = HostIdSpin()
        self.host_spin.setValue(self.prefs.get("host_id"))
        self.host_spin.valueChanged.connect(lambda v: self.prefs.set("host_id", v))
        box = self._group(tr("settings.group_connection"))
        box.add_row(
            ListRow(tr("settings.host_id"), tr("settings.host_id_desc"), trailing=self.host_spin)
        )

    def _build_plugins(self) -> None:
        """プラグインの一覧(boxed list)・読込エラー・有効/無効。変更は次回の起動から反映する。"""
        mgr: PluginManager | None = getattr(self.shell, "plugins", None)
        self._plugin_mgr = mgr
        if mgr is None:
            return
        failed = [r for r in mgr.records if r.status is PluginStatus.FAILED]
        box = self._group(tr("settings.group_plugins"))
        # 読込に失敗したプラグインは、一覧の上にバナー(注意)で出す(アプリは続行している)
        pos = self._column.indexOf(self._last_header) + 1
        self.plugins_note = Label(tr("settings.plugins_note"), "secondary", wrap=True)
        self._column.insertWidget(pos, self.plugins_note)
        for k, rec in enumerate(failed):
            bar = InfoBar(
                "caution", tr("settings.plugin_failed_title", name=tr(rec.name)), rec.error
            )
            bar.setToolTip(rec.detail or rec.error)
            self.plugin_alerts.append(bar)
            self._column.insertWidget(pos + 1 + k, bar)
        for rec in mgr.records:
            row = self._plugin_row(mgr, rec)
            self.plugin_rows[rec.id] = row
            box.add_row(row)
        for reg in mgr.ui.settings_sections() if mgr.ui is not None else ():
            try:
                widget = reg.factory(self.bridge, self.shell)
            except Exception as exc:  # noqa: BLE001 - そのプラグインだけ諦める
                mgr.report_failure(reg.owner, exc, "設定項目の表示")
                continue
            self._column.addSpacing(SP_SECTION - SP_RELATED)
            self._column.addWidget(GroupHeader(tr(reg.title)))
            self._column.addWidget(widget)

    def _plugin_row(self, mgr: PluginManager, rec: PluginRecord) -> ListRow:
        source = {
            SOURCE_BUILTIN: "settings.plugin_builtin",
            SOURCE_DROPIN: "settings.plugin_dropin",
        }.get(rec.source, "settings.plugin_entry_point")
        tags = [tr(source)]
        if rec.status is PluginStatus.FAILED:
            tags.append(tr("settings.plugin_failed_tag"))
        elif rec.status is PluginStatus.SKIPPED:
            tags.append(tr("settings.plugin_skipped_tag"))
        elif rec.status is PluginStatus.DISABLED:
            tags.append(tr("settings.plugin_off_tag"))
        tags.append(tr("settings.plugin_restart_tag"))  # 保存した設定と実行中が違うときだけ表示
        if rec.status is PluginStatus.FAILED or rec.status is PluginStatus.SKIPPED:
            desc = rec.error
        else:
            desc = tr(
                "settings.plugin_row_desc",
                description=tr(rec.description) if rec.description else rec.origin,
                version=tr("settings.plugin_version", version=rec.version or "—"),
            )
        trailing: QWidget
        if rec.builtin:
            trailing = Label(tr("settings.plugin_always_on"), "caption")
        else:
            sw = Switch(mgr.is_enabled(rec.id))
            sw.setAccessibleName(tr("settings.plugin_switch_name", name=tr(rec.name)))
            sw.toggled.connect(lambda on, pid=rec.id: self.set_plugin_enabled(pid, on))
            self.plugin_switches[rec.id] = sw
            trailing = sw
        row = ListRow((tr(rec.name) if rec.name else rec.id), desc, tags=tags, trailing=trailing)
        row.setObjectName(f"plugin_{rec.id}")
        if rec.detail:
            row.setToolTip(rec.detail)
        self._refresh_plugin_row(row, mgr, rec)
        return row

    def _refresh_plugin_row(self, row: ListRow, mgr: PluginManager, rec: PluginRecord) -> None:
        pending = (not rec.builtin) and (
            mgr.is_enabled(rec.id) != (rec.status is not PluginStatus.DISABLED)
        )
        row.tags[-1].setVisible(pending)

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> None:
        """有効/無効を保存する(次回の起動から反映)。スイッチ・テストの共通の入口。"""
        mgr = self._plugin_mgr
        if mgr is None:
            return
        try:
            mgr.set_enabled(plugin_id, enabled)
        except ValueError:  # 組込み
            return
        sw = self.plugin_switches.get(plugin_id)
        if sw is not None and sw.isChecked() != enabled:
            sw.setChecked(enabled)
        rec = mgr.record(plugin_id)
        row = self.plugin_rows.get(plugin_id)
        if rec is not None and row is not None:
            self._refresh_plugin_row(row, mgr, rec)

    def _language_row(self) -> ListRow:
        """言語(日本語 / English)。保存して次回の起動から反映する(実行中は切り替えない)。"""
        self.language_combo = Combo()
        self.language_combo.setMinimumWidth(160)
        for code in SUPPORTED:
            self.language_combo.addItem(LANGUAGE_NAMES[code], code)  # 各言語の自称(訳さない)
        # 保存した値が無いときは、実行中の言語(--lang・環境変数・既定)を見せる
        saved = self.prefs.get("language") if self.prefs.has("language") else current_language()
        self._select_data(self.language_combo, saved if saved in SUPPORTED else DEFAULT_LANG)
        self.language_combo.activated.connect(
            lambda _i: self.set_language(str(self.language_combo.currentData()))
        )
        row = ListRow(
            tr("settings.language"), tr("settings.language_desc"),
            tags=[tr("settings.plugin_restart_tag")], trailing=self.language_combo,
        )
        row.setObjectName("language_row")
        self.language_row = row
        self._refresh_language_tag()
        return row

    def set_language(self, code: str) -> None:
        """言語を保存する(次回の起動から反映)。コンボ・テストの共通の入口。"""
        if code not in SUPPORTED:
            return
        self.prefs.set("language", code)
        if self.language_combo.currentData() != code:
            self._select_data(self.language_combo, code)
        self._refresh_language_tag()

    def _refresh_language_tag(self) -> None:
        """保存した言語と実行中の言語が違うときだけ「再起動で反映」を出す。"""
        pending = self.language_combo.currentData() != current_language()
        self.language_row.tags[0].setVisible(pending)

    @staticmethod
    def _select_data(combo: Combo, data: object) -> None:
        i = combo.findData(data)
        combo.setCurrentIndex(i if i >= 0 else 0)

    # ================================================================ テーマ
    def theme_mode(self) -> str:
        return self.theme_seg.current() or "dark"

    def set_theme_mode(self, mode: str) -> None:
        """`mode`: dark / light / system。配色はシェルの `set_theme`(保存もシェル)で切り替える。"""
        self.prefs.set("theme_mode", mode)
        if self.theme_seg.current() != mode:
            self.theme_seg.set_current(mode)
        self._apply_theme(resolve_system_theme() if mode == "system" else mode)

    def _apply_theme(self, name: str) -> None:
        if theme_manager().name == name:
            return
        set_theme = getattr(self.shell, "set_theme", None)
        if callable(set_theme):
            set_theme(name)
        else:
            theme_manager().apply(name)

    def _apply_theme_mode_on_start(self) -> None:
        if self.prefs.has("theme_mode") and self.prefs.get("theme_mode") == "system":
            self._apply_theme(resolve_system_theme())

    def _on_system_scheme(self, _scheme: object = None) -> None:
        if self.theme_mode() == "system":
            self._apply_theme(resolve_system_theme())

    def _on_theme_changed(self, name: str) -> None:
        """ほか(ギャラリー・コード)から配色が変わったとき、セグメントの表示を合わせる。"""
        if self.theme_mode() != "system" and self.theme_seg.current() != name:
            self.theme_seg.set_current(name)
            self.prefs.set("theme_mode", name)

    # ================================================================ レイアウトの初期化
    def reset_layout(self) -> bool:
        """確認のうえ、ウィンドウの大きさと各ページの区画の幅を初期状態に戻す。"""
        if not self._confirm_reset():
            return False
        self.prefs.clear_layout()
        reset = getattr(self.shell, "reset_layout", None)
        if callable(reset):
            reset()
        page = getattr(self.shell, "page", None)
        if callable(page):
            for pid in ("operate", "params", "diagnostics", "maintenance"):
                try:
                    hook = getattr(page(pid), "reset_layout", None)
                except KeyError:
                    continue
                if callable(hook):
                    hook()
        if self.shell is not None:
            self.shell.toast(tr("settings.layout_done"), "success")
        return True

    def _confirm_reset(self) -> bool:
        """テストで差し替える。"""
        dlg = ActionDialog(
            self.window(),
            title=tr("settings.layout_confirm_title"),
            purpose=tr("settings.layout_confirm_body"),
            icon="rotate-ccw",
            confirm_text=tr("settings.layout_confirm_ok"),
        )
        return dlg.exec() == ActionDialog.DialogCode.Accepted
