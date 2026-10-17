"""組込みプラグイン(architecture.md §4.8: 組込みの機能もプラグイン API で登録する)。

- `pages`  … ナビのページ(運転・パラメータ・診断・保守・設定)
- `operate_modes` … 運転ページの制御モード(JOG・速度・位置・MIT・電流・試験信号・2台連動)

どちらも登録するだけで Qt を import しない(ページのクラスは遅延 import)。
組込みは利用者が無効にできない(`PluginManager` が先頭で読み込む)。
"""

from __future__ import annotations

from edulite05.builtin.operate_modes import BuiltinOperateModes
from edulite05.builtin.pages import BuiltinPages
from edulite05.plugin_api import Plugin

__all__ = ["BuiltinOperateModes", "BuiltinPages", "builtin_plugins"]


def builtin_plugins() -> list[Plugin]:
    """読み込み順(ページ → 運転モード)。"""
    return [BuiltinPages(), BuiltinOperateModes()]
