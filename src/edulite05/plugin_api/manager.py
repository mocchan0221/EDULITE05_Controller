"""PluginManager: プラグインの発見・読込・有効化・取消(architecture.md §4.8)。Qt を import しない。

読み込み元(この順に有効化する):
  1. `edulite05.builtin` … 組込みのページ・運転モード(利用者は無効にできない)
  2. プロジェクト直下 `plugins/<name>/plugin.py` … 置くだけで読み込まれる(名前順)
  3. entry point グループ `edulite05.plugins` … pip で配布するプラグイン(名前順)

読込失敗(import 例外・`activate` の例外・不正なオブジェクト・id 重複)は、そのプラグインだけを
`FAILED` にしてログへ出し、登録を取り消して続行する(アプリは落とさない)。
結果は `records` に残るので、
設定ページが一覧とエラーを表示できる。無効化は `StateStore` に保存し、**次回起動から**効く。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import re
import sys
import traceback
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any

from edulite05.plugin_api.api import (
    DISABLED_KEY,
    CoreRegistry,
    MemoryStateStore,
    Plugin,
    PluginContext,
    PluginSettings,
    PluginStatus,
    StateStore,
)
from edulite05.plugin_api.ui import UiRegistry

log = logging.getLogger("edulite05.plugins")

ENTRY_POINT_GROUP = "edulite05.plugins"
SOURCE_BUILTIN = "builtin"
SOURCE_DROPIN = "dropin"
SOURCE_ENTRY_POINT = "entry_point"
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class PluginRecord:
    """読み込んだ(読み込もうとした)プラグイン 1 件の結果。"""

    id: str
    name: str
    version: str
    source: str  # SOURCE_*
    origin: str  # ディレクトリ / entry point / モジュール名
    status: PluginStatus
    description: str = ""
    error: str = ""  # 1 行の理由(失敗・スキップのとき)
    detail: str = ""  # トレースバック(ログと同じ内容)
    plugin: Plugin | None = None
    context: PluginContext | None = None
    requires_gui: bool = True

    @property
    def builtin(self) -> bool:
        return self.source == SOURCE_BUILTIN

    @property
    def active(self) -> bool:
        return self.status is PluginStatus.ACTIVE


class PluginManager:
    """`PluginManager(gui=True).load_all()` で読み込む。`ui` は GUI のときの `UiRegistry`。"""

    def __init__(
        self,
        *,
        gui: bool = True,
        project_root: Path | None = None,
        state: StateStore | None = None,
        builtin: Sequence[Plugin] | None = None,
        dropin: bool = True,
        entry_points: bool = True,
        plugins_dir: Path | None = None,
    ) -> None:
        if builtin is None:
            from edulite05.builtin import builtin_plugins

            builtin = builtin_plugins()
        self.gui = gui
        self.project_root = project_root or default_project_root()
        self.plugins_dir = plugins_dir or (self.project_root / "plugins")
        self.state: StateStore = state if state is not None else MemoryStateStore()
        self.ui: UiRegistry | None = UiRegistry() if gui else None
        self.records: list[PluginRecord] = []
        self.loaded = False
        self._builtin = list(builtin)
        self._dropin = dropin
        self._entry_points = entry_points

    # ============================================================ 読込
    def load_all(self) -> list[PluginRecord]:
        """発見して有効化する(2 回目以降は何もしない)。例外は投げない。"""
        if self.loaded:
            return self.records
        self.loaded = True
        for plugin in self._builtin:
            module = sys.modules.get(type(plugin).__module__)
            if getattr(module, "__file__", None):  # 組込みはモジュールの隣の i18n/<lang>.toml
                _register_strings(Path(str(module.__file__)).parent)
            self._take(plugin, SOURCE_BUILTIN, getattr(type(plugin), "__module__", ""))
        if self._dropin:
            self._load_dropins()
        if self._entry_points:
            self._load_entry_points()
        return self.records

    def _load_dropins(self) -> None:
        root = self.plugins_dir
        try:
            dirs = sorted(
                d for d in root.iterdir()
                if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "plugin.py").is_file()
            ) if root.is_dir() else []
        except OSError:
            log.exception("plugins/ を読めません: %s", root)
            return
        for d in dirs:
            prov = f"dropin:{d.name}"
            _register_strings(d)  # 文字列(i18n/<lang>.toml)はデータ。無効でも設定ページの表示に使う
            if prov in self.disabled_ids():  # import 前に無効なら、コードを実行しない
                self._add_stub(prov, d.name, SOURCE_DROPIN, str(d), PluginStatus.DISABLED)
                continue
            try:
                plugin = _plugin_from_object(_import_dropin(d), d.name)
            except Exception as exc:  # noqa: BLE001 - 失敗は記録して続行
                self._add_failure(prov, d.name, SOURCE_DROPIN, str(d), exc)
                continue
            self._take(plugin, SOURCE_DROPIN, str(d), provisional=prov)

    def _load_entry_points(self) -> None:
        try:
            eps = sorted(metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name)
        except Exception:  # noqa: BLE001
            log.exception("entry point の列挙に失敗しました")
            return
        for ep in eps:
            prov = f"entry_point:{ep.name}"
            if prov in self.disabled_ids():
                self._add_stub(prov, ep.name, SOURCE_ENTRY_POINT, ep.value, PluginStatus.DISABLED)
                continue
            try:
                plugin = _plugin_from_object(ep.load(), ep.name)
            except Exception as exc:  # noqa: BLE001
                self._add_failure(prov, ep.name, SOURCE_ENTRY_POINT, ep.value, exc)
                continue
            self._take(plugin, SOURCE_ENTRY_POINT, ep.value, provisional=prov)

    def _take(self, plugin: Plugin, source: str, origin: str, *, provisional: str = "") -> None:
        """Plugin を検査して記録し、有効なら activate する。"""
        pid = str(getattr(plugin, "id", "") or "")
        name = str(getattr(plugin, "name", "") or pid)
        version = str(getattr(plugin, "version", "") or "")
        base = {
            "name": name, "version": version, "source": source, "origin": origin,
            "description": str(getattr(plugin, "description", "") or ""),
            "plugin": plugin, "requires_gui": bool(getattr(plugin, "requires_gui", True)),
        }
        if not _ID_RE.match(pid):
            self._append_failed(
                provisional or pid or origin, base, "id が不正です(英数字と _ . - のみ)"
            )
            return
        if any(r.id == pid for r in self.records):
            self._append_failed(
                provisional or f"{source}:{pid}", base, f"id {pid!r} が重複しています"
            )
            return
        if source != SOURCE_BUILTIN and pid in self.disabled_ids():
            self.records.append(PluginRecord(id=pid, status=PluginStatus.DISABLED, **base))
            return
        if base["requires_gui"] and not self.gui:
            self.records.append(PluginRecord(
                id=pid, status=PluginStatus.SKIPPED, error="GUI が必要なため読み込みません", **base
            ))
            return
        rec = PluginRecord(id=pid, status=PluginStatus.ACTIVE, **base)
        self.records.append(rec)
        self._activate(rec)

    def _activate(self, rec: PluginRecord) -> None:
        assert rec.plugin is not None
        ui = self.ui.scoped(rec.id, builtin=rec.builtin) if self.ui is not None else None
        ctx = PluginContext(
            plugin_id=rec.id,
            core=CoreRegistry(rec.id),
            ui=ui,
            settings=PluginSettings(self.state, rec.id),
            log=logging.getLogger(f"edulite05.plugins.{rec.id}"),
            project_root=self.project_root,
        )
        rec.context = ctx
        try:
            rec.plugin.activate(ctx)
        except Exception as exc:  # noqa: BLE001 - このプラグインだけ無効にして続行
            self._fail(rec, exc, "有効化")
        else:
            log.info("プラグイン %s %s を有効化しました(%s)", rec.id, rec.version, rec.source)

    # ============================================================ 失敗の記録
    def _append_failed(self, rid: str, base: dict[str, Any], message: str) -> None:
        log.error("プラグインを読み込めません(%s): %s", base["origin"], message)
        self.records.append(PluginRecord(
            id=rid, status=PluginStatus.FAILED, error=message, **base
        ))

    def _add_failure(
        self, rid: str, name: str, source: str, origin: str, exc: BaseException
    ) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log.error("プラグイン %s を読み込めません: %s\n%s", name, _one_line(exc), detail)
        self.records.append(PluginRecord(
            id=rid, name=name, version="", source=source, origin=origin,
            status=PluginStatus.FAILED, error=_one_line(exc), detail=detail,
        ))

    def _add_stub(
        self, rid: str, name: str, source: str, origin: str, status: PluginStatus
    ) -> None:
        self.records.append(PluginRecord(
            id=rid, name=name, version="", source=source, origin=origin, status=status
        ))

    def _fail(self, rec: PluginRecord, exc: BaseException, phase: str) -> None:
        rec.status = PluginStatus.FAILED
        rec.error = _one_line(exc)
        rec.detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log.error("プラグイン %s の%sに失敗しました: %s\n%s", rec.id, phase, rec.error, rec.detail)
        self._teardown(rec)

    def report_failure(self, plugin_id: str, exc: BaseException, phase: str = "表示") -> None:
        """GUI が factory(ページ等)の例外を知らせる。そのプラグインだけ失敗扱いにする。"""
        rec = self.record(plugin_id)
        if rec is not None and rec.status is PluginStatus.ACTIVE:
            self._fail(rec, exc, phase)

    def _teardown(self, rec: PluginRecord) -> None:
        """登録の取消(UI → コア)と購読の解除。deactivate は成功した有効化のときだけ呼ぶ。"""
        if rec.context is not None:
            rec.context.release()
            rec.context.core.rollback()
        if self.ui is not None:
            self.ui.remove_owner(rec.id)

    # ============================================================ 有効/無効
    def disabled_ids(self) -> set[str]:
        raw = self.state.get(DISABLED_KEY, [])
        if isinstance(raw, str):
            raw = [s for s in raw.split(",") if s]
        return {str(x) for x in raw or []}

    def is_enabled(self, plugin_id: str) -> bool:
        """利用者の設定(次回起動から効く)。組込みは常に True。"""
        rec = self.record(plugin_id)
        if rec is not None and rec.builtin:
            return True
        return plugin_id not in self.disabled_ids()

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """有効/無効を保存する。**次回起動から**反映する(実行中のプラグインは止めない)。"""
        rec = self.record(plugin_id)
        if rec is not None and rec.builtin:
            raise ValueError("組込みプラグインは無効にできません")
        ids = self.disabled_ids()
        if enabled:
            ids.discard(plugin_id)
        else:
            ids.add(plugin_id)
        self.state.set(DISABLED_KEY, sorted(ids))

    def pending_restart(self) -> bool:
        """保存した設定と実行中の状態が違う(再起動で変わる)プラグインがあるか。"""
        for r in self.records:
            if r.builtin:
                continue
            want = r.id not in self.disabled_ids()
            if want != (r.status is not PluginStatus.DISABLED):
                return True
        return False

    # ============================================================ 参照
    def record(self, plugin_id: str) -> PluginRecord | None:
        return next((r for r in self.records if r.id == plugin_id), None)

    def failed(self) -> list[PluginRecord]:
        return [r for r in self.records if r.status is PluginStatus.FAILED]

    # ============================================================ 終了
    def shutdown(self) -> None:
        """有効なプラグインを逆順に `deactivate` し、登録を取り消す。何度呼んでも良い。"""
        for rec in reversed(self.records):
            if rec.status is not PluginStatus.ACTIVE or rec.plugin is None:
                continue
            try:
                rec.plugin.deactivate()
            except Exception:  # noqa: BLE001
                log.exception("プラグイン %s の deactivate が失敗しました", rec.id)
            self._teardown(rec)
            rec.context = None
            rec.status = PluginStatus.DISABLED
            rec.error = "終了しました"


# ================================================================ 読込の部品
def _one_line(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text.splitlines()[0] if text else type(exc).__name__


def _register_strings(directory: Path) -> None:
    """`plugins/<name>/i18n/<lang>.toml` を UI 文字列カタログへ足す(無ければ何もしない)。"""
    try:
        from edulite05.app.i18n import add_strings_dir

        add_strings_dir(directory)
    except Exception:  # noqa: BLE001 - 文字列が読めなくてもプラグインは読み込む
        log.exception("プラグインの文字列を読めません: %s", directory)


def _import_dropin(directory: Path) -> ModuleType:
    """`plugins/<name>/plugin.py` をパッケージとして import する(隣のファイルを相対 import 可)。"""
    mod_name = "edulite05_plugin_" + re.sub(r"\W", "_", directory.name)
    for key in [k for k in sys.modules if k == mod_name or k.startswith(mod_name + ".")]:
        del sys.modules[key]  # 再読込のとき古いモジュールを引きずらない
    spec = importlib.util.spec_from_file_location(
        mod_name, directory / "plugin.py", submodule_search_locations=[str(directory)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"{directory / 'plugin.py'} を読み込めません")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for key in [k for k in sys.modules if k == mod_name or k.startswith(mod_name + ".")]:
            del sys.modules[key]
        raise
    return module


def _plugin_from_object(obj: Any, label: str) -> Plugin:
    """モジュール / クラス / インスタンス / factory から Plugin を取り出す。

    モジュールの場合の探し方: `PLUGIN`(インスタンスまたはクラス)→ `plugin` → `create_plugin()` →
    そのモジュールで定義された `Plugin` の派生クラスがちょうど 1 つ。
    """
    if isinstance(obj, ModuleType):
        for attr in ("PLUGIN", "plugin"):
            if hasattr(obj, attr):
                return _plugin_from_object(getattr(obj, attr), label)
        factory = getattr(obj, "create_plugin", None)
        if callable(factory):
            return _plugin_from_object(factory(), label)
        classes = [
            c for c in vars(obj).values()
            if isinstance(c, type) and issubclass(c, Plugin) and c is not Plugin
            and c.__module__ == obj.__name__
        ]
        if len(classes) == 1:
            return classes[0]()
        raise TypeError(
            f"{label}: Plugin が見つかりません(PLUGIN = MyPlugin() を定義するか、"
            "Plugin の派生クラスを 1 つだけ置いてください)"
        )
    if isinstance(obj, Plugin):
        return obj
    if isinstance(obj, type) and issubclass(obj, Plugin):
        return obj()
    if callable(obj):
        return _plugin_from_object(obj(), label)
    raise TypeError(f"{label}: Plugin ではありません: {type(obj).__name__}")


def load_plugins(
    *,
    gui: bool = False,
    state: StateStore | None = None,
    project_root: Path | None = None,
    builtin: Iterable[Plugin] | None = None,
) -> PluginManager:
    """CLI などから `requires_gui=False` のプラグインだけ読み込む簡易入口。"""
    mgr = PluginManager(
        gui=gui, state=state, project_root=project_root,
        builtin=list(builtin) if builtin is not None else None,
    )
    mgr.load_all()
    return mgr
