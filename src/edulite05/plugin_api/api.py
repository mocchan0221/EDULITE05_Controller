"""プラグイン API の本体(architecture.md §4.8)。Qt を import しない。

    class MyPlugin(Plugin):
        id = "my_plugin"; name = "私のプラグイン"; version = "0.1.0"

        def activate(self, ctx: PluginContext) -> None:
            ctx.core.register_controller(MyController)       # コア側の登録口
            if ctx.ui is not None:                           # GUI のときだけ UI を足す
                ctx.ui.add_mode(MyController, "その他")

        def deactivate(self) -> None: ...

コア側の登録口(`ctx.core`)は、Controller・トランスポート・テレメトリのチャネル・モータの
プロファイル(TOML)の置き場。失敗したプラグインの登録は自動で取り消される。
モータへの指令は必ず `MotorService`(`ctx.service`)経由にすること。生フレームを直接送って
`SafetyGuard` を迂回する経路を作ってはいけない(docs/design/plugin_guide.md)。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from edulite05.plugin_api import runtime
from edulite05.plugin_api.ui import UiRegistry

log = logging.getLogger("edulite05.plugins")


class PluginStatus(StrEnum):
    ACTIVE = "active"  # 有効(activate 済み)
    DISABLED = "disabled"  # 利用者が無効にした(または GUI なしで requires_gui)
    FAILED = "failed"  # 読み込み・有効化に失敗(このプラグインだけ無効)
    SKIPPED = "skipped"  # 条件を満たさず読み込まなかった(GUI なしの環境での requires_gui など)


class Plugin:
    """プラグインの基底。`id` は一意(英数字と `_` `.` `-`)。`activate` で登録する。"""

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    requires_gui: ClassVar[bool] = True  # False ならコアだけで動く(CLI でも読み込める)
    description: ClassVar[str] = ""  # 設定ページの一覧に出す 1 行説明

    def activate(self, ctx: PluginContext) -> None:
        raise NotImplementedError("activate(ctx) を実装してください")

    def deactivate(self) -> None:  # noqa: B027 - 任意の拡張点
        """終了時。`ctx` 経由の登録は自動で取り消されるので、独自のリソースだけ片付ける。"""


class StateStore(Protocol):
    """有効/無効やプラグイン設定の保存先。GUI は `user_data/ui_state.ini`(QSettings)を使う。"""

    def get(self, key: str, default: Any = None) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...


class MemoryStateStore:
    """保存しない(テスト・設定ファイル無しの起動)。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(data or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


class JsonFileStateStore:
    """JSON ファイルに保存する(CLI など Qt の無い環境用)。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._read()
            data[key] = value
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )


DISABLED_KEY = "disabled"  # StateStore のキー: 無効にしたプラグイン id のリスト


class PluginSettings:
    """プラグイン専用の永続設定(`ctx.settings`)。値は JSON にできる型(数値・文字列・list 等)。"""

    def __init__(self, store: StateStore, plugin_id: str) -> None:
        self._store = store
        self._prefix = f"settings/{plugin_id}/"

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(self._prefix + key, default)

    def set(self, key: str, value: Any) -> None:
        self._store.set(self._prefix + key, value)


class CoreRegistry:
    """コア側の登録口(`ctx.core`)。UI を使わず CLI でも有効。取消はマネージャが自動で行う。"""

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._undo: list[Callable[[], None]] = []

    def register_controller(self, cls: type) -> None:
        """`Controller` のサブクラスを登録する(`CONTROLLERS`)。指令は SafetyGuard を通る。"""
        from edulite05.core.control.controllers import (
            CONTROLLERS,
            register_controller,
            unregister_controller,
        )

        already = CONTROLLERS.get(getattr(cls, "name", "")) is cls
        register_controller(cls)  # type: ignore[arg-type]
        if not already:
            name = cls.name  # type: ignore[attr-defined]
            self._undo.append(lambda: unregister_controller(name))

    def register_transport(self, name: str, factory: Callable[..., Any], describe: Any) -> None:
        """CAN トランスポートを登録する(接続ポップオーバーの選択肢に出る)。既存名は上書き不可。"""
        from edulite05.core.transport import registry

        if registry.is_registered(name):
            raise ValueError(f"トランスポート名 {name!r} は登録済みです")
        registry.register(name, factory, describe)
        self._undo.append(lambda: registry.unregister(name))

    def register_channel(
        self, suffix: str, unit: str, *, kind: str = "plugin", label: str | None = None
    ) -> None:
        """モータごとの追加テレメトリチャネル `m{id}.{suffix}` を足す。

        接続前(activate 時)に登録すると、モータが見つかったとき標準チャネルと一緒に登録される。
        値は `ctx.service.telemetry.append(motor_channel(id, suffix), t, value)` で入れる。
        """
        from edulite05.core.telemetry import register_extra_channel, unregister_extra_channel

        register_extra_channel(suffix, unit, kind, label or suffix)
        self._undo.append(lambda: unregister_extra_channel(suffix))

    def add_profile_dir(self, path: str | os.PathLike[str]) -> None:
        """モータのプロファイル(`<name>.toml`)を探す場所を足す。組込みと同名なら組込みが優先。"""
        from edulite05.core.device.profile import add_profile_dir, remove_profile_dir

        p = Path(path)
        add_profile_dir(p)
        self._undo.append(lambda: remove_profile_dir(p))

    def rollback(self) -> None:
        """登録を逆順に取り消す(個々の失敗は無視してログ)。"""
        while self._undo:
            fn = self._undo.pop()
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("プラグイン %s の登録の取消に失敗", self._plugin_id)


@dataclass(slots=True)
class PluginContext:
    """`activate(ctx)` に渡される。プラグインごとに別のインスタンス。"""

    plugin_id: str
    core: CoreRegistry
    ui: UiRegistry | None  # CLI(GUI なし)のときは None
    settings: PluginSettings
    log: logging.Logger
    project_root: Path | None = None
    _unsubs: list[Callable[[], None]] = field(default_factory=list)

    @property
    def service(self) -> Any:
        """接続中の `MotorService`(未接続なら None)。モータへの指令はここ経由に限る。"""
        return runtime.current_service()

    @property
    def session(self) -> Any:
        """接続中の `Session`(未接続なら None)。bus / events / profile への読み取り用。"""
        svc = runtime.current_service()
        return getattr(svc, "session", None)

    def on_service(self, cb: Callable[[Any], None]) -> Callable[[], None]:
        """接続(サービス)/ 切断(None)に追従する。購読はプラグイン終了時に自動で解除される。"""
        unsub = runtime.on_service_changed(cb)
        self._unsubs.append(unsub)
        return unsub

    def release(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
