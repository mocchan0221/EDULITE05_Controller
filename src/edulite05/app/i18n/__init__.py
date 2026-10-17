"""UI 文字列カタログ。`tr("key")` で ja.toml / en.toml から引く。

カタログはネストした TOML テーブルで、キーは ``section.name`` 形式に平坦化する。
`tr("key", n=3)` のように渡すと ``str.format`` で埋め込む。

言語:
- 対応言語は ``SUPPORTED``(ja / en)。起動時に ``resolve_language`` で決め、``load_catalog`` で読む
  (優先順位: ``--lang`` > 環境変数 ``EDULITE05_LANG`` > 設定ページで保存した値 > ja)。
  実行中の切替は行わず、設定ページで選んだ言語は次回の起動から反映する。
- 現在の言語に無いキーは ja にフォールバックし、それも無ければキー文字列を返す。
  どちらもキーごとに警告ログを 1 回だけ出す。

プラグインの文字列:
- 組込みページはカタログ(ja.toml / en.toml)を使う。
- プラグインは ``plugins/<name>/i18n/<lang>.toml`` を置くと、``PluginManager`` が読み込み時に
  ``add_strings_dir`` で足す。キーは ``section.name`` でも、日本語の原文そのまま
  (``"三角波" = "Triangle wave"``)でもよい。原文が日本語の `tr("三角波")` は、
  訳が無ければ原文をそのまま返す(警告しない)。pip 配布のプラグインは ``add_strings`` を直接呼ぶ。
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

SUPPORTED: tuple[str, ...] = ("ja", "en")
LANGUAGE_NAMES: dict[str, str] = {"ja": "日本語", "en": "English"}  # 各言語の自称(訳さない)
DEFAULT_LANG = "ja"
ENV_VAR = "EDULITE05_LANG"

_CATALOG: dict[str, str] = {}  # 現在の言語
_FALLBACK: dict[str, str] = {}  # ja(現在の言語が ja 以外のときだけ読む)
_EXTRA: dict[str, dict[str, str]] = {}  # プラグインの文字列: 言語 → {キー: 文字列}
_MISSING: set[str] = set()  # 警告済みのキー
_LANG = DEFAULT_LANG
# カタログのキー形式(`section.name`)。これに当てはまらない文字列(日本語の文など)は、
# プラグインが直接渡した表示文字列とみなしてそのまま返す(警告しない)
_KEY_SHAPE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)+$")


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, full + "."))
        else:
            out[full] = str(value)
    return out


def _read(lang: str) -> dict[str, str]:
    path = Path(__file__).with_name(f"{lang}.toml")
    with path.open("rb") as f:
        return _flatten(tomllib.load(f))


def normalize_language(lang: object) -> str | None:
    """``"en"`` ``"EN"`` ``"en_US"`` ``"ja-JP"`` などを対応言語に直す。非対応なら None。"""
    if not isinstance(lang, str):
        return None
    head = re.split(r"[-_.]", lang.strip().lower(), maxsplit=1)[0]
    return head if head in SUPPORTED else None


def resolve_language(cli: str | None = None, saved: object = None) -> str:
    """起動時の言語を決める: ``--lang`` > 環境変数 > 保存済みの設定 > ja。"""
    for candidate in (cli, os.environ.get(ENV_VAR), saved):
        if candidate in (None, ""):
            continue
        lang = normalize_language(candidate)
        if lang is not None:
            return lang
        _log.warning(
            "i18n: 非対応の言語 %r を無視します(対応: %s)", candidate, ", ".join(SUPPORTED)
        )
    return DEFAULT_LANG


def load_catalog(lang: str = DEFAULT_LANG) -> None:
    """カタログを読み込む。非対応の言語は警告して ja にする。"""
    global _LANG
    code = normalize_language(lang)
    if code is None:
        _log.warning("i18n: 非対応の言語 %r のため ja を使います", lang)
        code = DEFAULT_LANG
    _CATALOG.clear()
    _CATALOG.update(_read(code))
    _FALLBACK.clear()
    if code != DEFAULT_LANG:
        _FALLBACK.update(_read(DEFAULT_LANG))
    _LANG = code
    _MISSING.clear()


def current_language() -> str:
    return _LANG


def catalog() -> dict[str, str]:
    if not _CATALOG:
        load_catalog(_LANG)
    return _CATALOG


# ---------------------------------------------------------------- プラグインの文字列
def add_strings(lang: str, strings: Mapping[str, Any]) -> None:
    """プラグインの文字列を足す。キーは ``section.name``、または日本語の原文。

    入れ子の辞書は ``a.b`` に平坦化する。同じキーは後から足したものが勝つ。
    """
    code = normalize_language(lang)
    if code is None:
        _log.warning("i18n: 非対応の言語 %r の文字列は無視します", lang)
        return
    _EXTRA.setdefault(code, {}).update(_flatten(strings))


def add_strings_file(path: Path) -> None:
    """``<lang>.toml``(ファイル名が言語)を読んで ``add_strings`` する。"""
    with path.open("rb") as f:
        add_strings(path.stem, tomllib.load(f))


def add_strings_dir(directory: Path) -> int:
    """``directory/i18n/*.toml`` を全部足す。足したファイル数を返す。壊れたファイルは飛ばす。"""
    n = 0
    folder = directory / "i18n"
    if not folder.is_dir():
        return 0
    for path in sorted(folder.glob("*.toml")):
        try:
            add_strings_file(path)
        except (OSError, tomllib.TOMLDecodeError):
            _log.exception("i18n: %s を読めません", path)
            continue
        n += 1
    return n


def clear_plugin_strings() -> None:
    """プラグインの文字列を全部消す(テスト用)。"""
    _EXTRA.clear()


def plugin_strings(lang: str) -> dict[str, str]:
    """足されたプラグインの文字列(テスト・検査用のコピー)。"""
    return dict(_EXTRA.get(lang, {}))


# ---------------------------------------------------------------- 引く
def _lookup(key: str) -> str | None:
    text = catalog().get(key)
    if text is None:
        text = _EXTRA.get(_LANG, {}).get(key)
    return text


def tr(key: str, **kwargs: Any) -> str:
    """キーに対応する UI 文字列を返す。

    現在の言語に無ければ ja、それも無ければキー自身を返す(キーごとに警告 1 回)。
    キーの形でない文字列(日本語の原文など)は訳が無ければそのまま返す。
    """
    text = _lookup(key)
    if text is None:
        shaped = bool(_KEY_SHAPE.match(key))
        if _LANG != DEFAULT_LANG:
            text = _FALLBACK.get(key)
            if text is None:
                text = _EXTRA.get(DEFAULT_LANG, {}).get(key)
            if text is not None and shaped and key not in _MISSING:
                _MISSING.add(key)
                _log.warning("i18n: キー %r が %s に無いため ja を使います", key, _LANG)
        if text is None:
            if not shaped:
                return key.format(**kwargs) if kwargs else key
            if key not in _MISSING:
                _MISSING.add(key)
                _log.warning("i18n: 未定義のキー %r", key)
            return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            _log.warning("i18n: %r の書式引数が不足", key)
    return text
