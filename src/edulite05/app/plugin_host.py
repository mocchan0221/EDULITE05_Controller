"""プラグイン基盤の GUI 側の接続(Qt を使う部分)。

- `QSettingsStateStore` … プラグインの有効/無効・プラグイン設定を `user_data/ui_state.ini`
  (シェルの QSettings)の `plugins/` 以下に JSON で保存する(`plugin_api.StateStore`)
- `create_manager` … 起動時に使う `PluginManager`(組込み + `plugins/` + entry point)を作る
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings

from edulite05.plugin_api import MemoryStateStore, PluginManager, StateStore

SettingsSource = Path | Callable[[], QSettings | None] | None


class QSettingsStateStore:
    """`source` は ini のパス、または `QSettings | None` を返す関数(`MainWindow.settings`)。

    None のときはメモリ上だけ(テスト・設定ファイル無し)。値は JSON 文字列で保存する
    (ini は型を持たず、空のリストなどが崩れるため)。
    """

    def __init__(self, source: SettingsSource) -> None:
        self._source = source
        self._memory = MemoryStateStore()

    def _settings(self) -> QSettings | None:
        src = self._source
        if src is None:
            return None
        if callable(src):
            return src()
        src.parent.mkdir(parents=True, exist_ok=True)
        return QSettings(str(src), QSettings.Format.IniFormat)

    def get(self, key: str, default: Any = None) -> Any:
        s = self._settings()
        if s is None:
            return self._memory.get(key, default)
        raw = s.value(f"plugins/{key}")
        if raw is None:
            return default
        try:
            return json.loads(str(raw))
        except ValueError:
            return default

    def set(self, key: str, value: Any) -> None:
        s = self._settings()
        if s is None:
            self._memory.set(key, value)
            return
        s.setValue(f"plugins/{key}", json.dumps(value, ensure_ascii=False))
        s.sync()


def create_manager(
    source: SettingsSource, *, project_root: Path | None = None, state: StateStore | None = None
) -> PluginManager:
    """起動用のマネージャ(組込み + `plugins/<name>/plugin.py` + entry point)。まだ読み込まない。"""
    return PluginManager(
        gui=True, project_root=project_root,
        state=state if state is not None else QSettingsStateStore(source),
    )
