"""UI の登録口 `UiRegistry`(ui_design.md §10)。Qt を import しない。

プラグインは `activate(ctx)` の中で `ctx.ui.add_page(...)` などを呼んで画面の部品を登録する。
ここは**登録を記録するだけ**で、ウィジェットは作らない。`factory` は呼び出し可能な物
(関数・クラス・`LazyClassFactory`)で、GUI シェル(`app/main_window.py` 等)が必要なときに呼ぶ。
そのため `plugin_api` は Qt なしで import でき、CLI・テストから単体で動く。

組込みのページ・運転モードも、この API で登録する(`edulite05.builtin`)。

factory の署名:
    add_page            factory(bridge, shell)  -> QWidget(通常は `PageBase` の派生)
    add_monitor_view    factory(bridge)         -> QWidget
    add_maintenance_item dialog_factory(parent, motor) -> ToolDialog
    add_settings_section factory(bridge, shell) -> QWidget
    add_more_action     callback()
タイトル・説明の文字列は `tr` のキー(`"nav.operate"`)でも、そのままの文字列でも良い(`tr` が
カタログに無い文字列をそのまま返す)。
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("edulite05.plugins")

GROUP_MAIN = "main"  # 運転ページのモード選択の本体(組込みだけ。5 つ並べる幅しかない)
GROUP_OTHER = "その他"  # 「その他 ▾」メニュー(プラグインの既定)
MAINTENANCE_COMMON = "common"  # 保守ページ: よく使う設定
MAINTENANCE_CAUTION = "caution"  # 保守ページ: 注意が必要な操作
PAGE_ORDER_DEFAULT = 50  # 保守(40)と設定(90)の間


class LazyClassFactory:
    """`module.cls` を最初に呼ばれたときに import してインスタンスを作る factory。

    組込みページは重いモジュール(pyqtgraph 等)を持つので、登録時には import しない。
    `load()` でクラスそのものを取り出せる(テスト用)。
    """

    def __init__(self, module: str, cls: str) -> None:
        self.module = module
        self.cls = cls

    def load(self) -> type:
        return getattr(importlib.import_module(self.module), self.cls)  # type: ignore[no-any-return]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.load()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"LazyClassFactory({self.module}.{self.cls})"


@dataclass(frozen=True, slots=True)
class PageReg:
    id: str  # ナビ・ショートカット・`ShellApi.navigate` で使う識別子
    title: str
    icon: str  # Lucide のアイコン名(assets/icons)
    factory: Callable[..., Any]
    order: int = PAGE_ORDER_DEFAULT  # 小さいほど上。Ctrl+1.. はこの順
    bottom: bool = False  # True ならナビの最下部(設定)
    owner: str = ""  # 登録したプラグインの id


@dataclass(frozen=True, slots=True)
class ControllerForm:
    """運転モードが使うコントローラ 1 つ分の入力フォーム(`Params` の自動フォームの見せ方)。"""

    name: str  # Controller.name
    params_cls: type  # core/control/params.py 流の dataclass(フィールド metadata からフォーム生成)
    target_fields: tuple[str, ...]  # 「目標」として大きく出すフィールド
    limit_fields: tuple[str, ...] = ()  # 「制限と加速度」に畳むフィールド
    label_overrides: Mapping[str, str] = field(default_factory=dict)  # フィールド名 → ラベル


@dataclass(frozen=True, slots=True)
class ModeSpec:
    """運転ページのインスペクタに出す制御モード 1 つ。"""

    id: str
    title: str  # メニュー・選択肢の名前
    group: str = GROUP_OTHER  # GROUP_MAIN(組込みのみ)か、「その他 ▾」メニュー内の見出し名
    controllers: tuple[ControllerForm, ...] = ()
    short_title: str = ""  # 選択中に「その他 ▾」の位置へ出す短い名前(省略時は title)
    description: str = ""  # 1 行説明
    info: str = ""  # ⓘ のツールチップ(省略時は description)
    min_motors: int = 1  # このモードを選べる最小のモータ台数(2 台連動は 2)
    owner: str = ""


@dataclass(frozen=True, slots=True)
class MonitorViewReg:
    id: str
    title: str
    factory: Callable[..., Any]
    owner: str = ""


@dataclass(frozen=True, slots=True)
class MaintenanceItemReg:
    key: str
    group: str  # MAINTENANCE_COMMON / MAINTENANCE_CAUTION
    title: str
    description: str
    tags: tuple[str, ...]
    dialog_factory: Callable[..., Any]
    needs_motor: bool = True
    owner: str = ""


@dataclass(frozen=True, slots=True)
class SettingsSectionReg:
    title: str
    factory: Callable[..., Any]
    owner: str = ""


@dataclass(frozen=True, slots=True)
class MoreActionReg:
    text: str
    callback: Callable[[], None]
    owner: str = ""


@dataclass(slots=True)
class _Store:
    pages: list[PageReg] = field(default_factory=list)
    modes: list[ModeSpec] = field(default_factory=list)
    views: list[MonitorViewReg] = field(default_factory=list)
    maintenance: list[MaintenanceItemReg] = field(default_factory=list)
    sections: list[SettingsSectionReg] = field(default_factory=list)
    more: list[MoreActionReg] = field(default_factory=list)
    # owner → このプラグインが `register_params` で足したコントローラ名(取消用)
    params_added: dict[str, list[str]] = field(default_factory=dict)


class UiRegistry:
    """UI 部品の登録簿。`scoped(owner)` で作った窓口はプラグインごとの所有者を自動で付ける。

    マネージャが所有する本体(`UiRegistry()`)から、プラグインごとに `scoped(plugin_id)` を渡す。
    問い合わせ(`pages()` など)はどの窓口からでも全体が見える。
    """

    def __init__(self, *, _store: _Store | None = None, _owner: str = "",
                 _builtin: bool = False) -> None:
        self._store = _store or _Store()
        self._owner = _owner
        self._builtin = _builtin

    def scoped(self, owner: str, *, builtin: bool = False) -> UiRegistry:
        return UiRegistry(_store=self._store, _owner=owner, _builtin=builtin)

    # ============================================================ 登録(プラグインが呼ぶ)
    def add_page(
        self,
        id: str,  # noqa: A002
        title: str,
        icon: str,
        factory: Callable[..., Any],
        order: int = PAGE_ORDER_DEFAULT,
        *,
        bottom: bool = False,
    ) -> None:
        """ナビにページを足す。`factory(bridge, shell)` が `PageBase` を返す。"""
        if not id or not callable(factory):
            raise ValueError("add_page: id と factory(呼び出し可能)が必要です")
        if any(p.id == id for p in self._store.pages):
            raise ValueError(f"ページ ID {id!r} は登録済みです")
        if bottom and not self._builtin:
            bottom = False  # ナビの最下部は設定ページ専用
        self._store.pages.append(
            PageReg(id, title, icon, factory, int(order), bottom, self._owner)
        )

    def add_mode(
        self,
        controller_cls: type,
        group: str = GROUP_OTHER,
        *,
        id: str | None = None,  # noqa: A002
        title: str | None = None,
        description: str = "",
        info: str = "",
        short_title: str = "",
        target_fields: Sequence[str] | None = None,
        limit_fields: Sequence[str] = (),
        label_overrides: Mapping[str, str] | None = None,
        min_motors: int = 1,
    ) -> None:
        """運転ページのモード選択(「その他 ▾」)に Controller を足す。

        `Params` dataclass から目標フォームを自動生成する(`target_fields` 省略時は全フィールド)。
        Controller は先に `ctx.core.register_controller(cls)` で登録しておく。
        """
        from edulite05.core.control.controllers import CONTROLLERS

        name = getattr(controller_cls, "name", "")
        if CONTROLLERS.get(name) is not controller_cls:
            raise ValueError(
                f"add_mode: Controller {name!r} が未登録です。先に ctx.core.register_controller()"
                "を呼んでください"
            )
        params_cls = controller_cls.Params  # type: ignore[attr-defined]
        fields = tuple(f.name for f in dataclasses.fields(params_cls))
        form = ControllerForm(
            name=name,
            params_cls=params_cls,
            target_fields=tuple(target_fields) if target_fields is not None else fields,
            limit_fields=tuple(limit_fields),
            label_overrides=dict(label_overrides or {}),
        )
        unknown = (set(form.target_fields) | set(form.limit_fields)) - set(fields)
        if unknown:
            raise ValueError(f"add_mode: Params に無いフィールドです: {sorted(unknown)}")
        label = title or str(getattr(controller_cls, "label", name))
        self.add_mode_spec(
            ModeSpec(
                id=id or name, title=label, group=group, controllers=(form,),
                short_title=short_title, description=description, info=info,
                min_motors=min_motors,
            )
        )

    def add_mode_spec(self, spec: ModeSpec) -> None:
        """`ModeSpec` をそのまま登録する(組込みの複数コントローラのモード用)。"""
        from edulite05.core.control.params import register_params

        if any(m.id == spec.id for m in self._store.modes):
            raise ValueError(f"モード ID {spec.id!r} は登録済みです")
        group = spec.group
        if group == GROUP_MAIN and not self._builtin:
            group = GROUP_OTHER  # 本体の並びは組込み専用
        if not group:
            group = GROUP_OTHER
        added = self._store.params_added.setdefault(self._owner, [])
        for form in spec.controllers:
            register_params(form.name, form.params_cls)
            added.append(form.name)
        self._store.modes.append(dataclasses.replace(spec, group=group, owner=self._owner))

    def add_monitor_view(
        self, id: str, title: str, factory: Callable[..., Any]  # noqa: A002
    ) -> None:
        """運転ページのモニタ `[グラフ | 3D | …]` に表示を足す。factory は QWidget を返す。"""
        if not id or not callable(factory):
            raise ValueError("add_monitor_view: id と factory が必要です")
        if id in ("graph", "3d") or any(v.id == id for v in self._store.views):
            raise ValueError(f"モニタ表示 ID {id!r} は使えません(登録済み)")
        self._store.views.append(MonitorViewReg(id, title, factory, self._owner))

    def add_maintenance_item(
        self,
        group: str,
        title: str,
        description: str,
        tags: Sequence[str],
        dialog_factory: Callable[..., Any],
        *,
        key: str | None = None,
        needs_motor: bool = True,
    ) -> None:
        """保守ページに行を足す。`dialog_factory(parent, motor)` が確認ダイアログを返す。

        `group` は `MAINTENANCE_COMMON`(よく使う設定)か `MAINTENANCE_CAUTION`(注意が必要な操作)。
        """
        if group not in (MAINTENANCE_COMMON, MAINTENANCE_CAUTION):
            raise ValueError(f"add_maintenance_item: group は common / caution です: {group!r}")
        k = key or f"{self._owner or 'plugin'}_{len(self._store.maintenance) + 1}"
        if any(i.key == k for i in self._store.maintenance):
            raise ValueError(f"保守項目 {k!r} は登録済みです")
        self._store.maintenance.append(
            MaintenanceItemReg(
                k, group, title, description, tuple(tags), dialog_factory, needs_motor,
                self._owner,
            )
        )

    def add_settings_section(
        self, title: str, factory: Callable[..., Any]
    ) -> None:
        """設定ページにグループを足す。`factory(bridge, shell)` は QWidget を返す。"""
        if not callable(factory):
            raise ValueError("add_settings_section: factory が必要です")
        self._store.sections.append(SettingsSectionReg(title, factory, self._owner))

    def add_more_action(self, text: str, callback: Callable[[], None]) -> None:
        """ヘッダー右端の `⋯` メニューにプラグインの項目を足す。"""
        if not callable(callback):
            raise ValueError("add_more_action: callback が必要です")
        self._store.more.append(MoreActionReg(text, callback, self._owner))

    # ============================================================ 問い合わせ(シェルが呼ぶ)
    def pages(self) -> list[PageReg]:
        """並び順(`order`、同じなら登録順)。"""
        return sorted(self._store.pages, key=lambda p: p.order)

    def modes(self) -> list[ModeSpec]:
        return list(self._store.modes)

    def monitor_views(self) -> list[MonitorViewReg]:
        return list(self._store.views)

    def maintenance_items(self) -> list[MaintenanceItemReg]:
        return list(self._store.maintenance)

    def settings_sections(self) -> list[SettingsSectionReg]:
        return list(self._store.sections)

    def more_actions(self) -> list[MoreActionReg]:
        return list(self._store.more)

    def owners(self) -> set[str]:
        s = self._store
        regs: list[Any] = [*s.pages, *s.modes, *s.views, *s.maintenance, *s.sections, *s.more]
        return {r.owner for r in regs}

    # ============================================================ 取消(マネージャが呼ぶ)
    def remove_owner(self, owner: str) -> None:
        """プラグインの登録をすべて外す(読込失敗・無効化・終了時)。"""
        from edulite05.core.control.params import unregister_params

        s = self._store
        s.pages[:] = [r for r in s.pages if r.owner != owner]
        s.modes[:] = [r for r in s.modes if r.owner != owner]
        s.views[:] = [r for r in s.views if r.owner != owner]
        s.maintenance[:] = [r for r in s.maintenance if r.owner != owner]
        s.sections[:] = [r for r in s.sections if r.owner != owner]
        s.more[:] = [r for r in s.more if r.owner != owner]
        for name in s.params_added.pop(owner, []):
            try:
                unregister_params(name)
            except ValueError:  # 組込みの Params(外せない)
                pass
