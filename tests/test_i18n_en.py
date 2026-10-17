"""英語カタログ(en.toml)と言語の解決・フォールバック・プラグインの文字列。"""

from __future__ import annotations

import ast
import logging
import re
import string
import tomllib
from pathlib import Path

import pytest

from edulite05.app import i18n

I18N_DIR = Path(i18n.__file__).parent
ROOT = Path(__file__).resolve().parents[1]
_JA = re.compile(r"[぀-ヿ一-鿿]")
ALLOW_EMPTY = {"operate.monitor.preset"}
_SPEC = re.compile(r"\{[^{}:]*(:[^{}]*)?\}")


def _flat(path: Path) -> dict[str, str]:
    with path.open("rb") as f:
        return i18n._flatten(tomllib.load(f))


def _fields(text: str) -> list[str]:
    """str.format のフィールド名(書式指定は除く)を並べ替えて返す。"""
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name is not None)


def _specs(text: str) -> list[str]:
    return sorted(m.group(0) for m in _SPEC.finditer(text))


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    i18n.load_catalog("ja")
    i18n.clear_plugin_strings()


JA = _flat(I18N_DIR / "ja.toml")
EN = _flat(I18N_DIR / "en.toml")


# ---------------------------------------------------------------- カタログの対応
def test_every_ja_key_exists_in_en_and_vice_versa() -> None:
    assert sorted(set(JA) - set(EN)) == [], "en.toml に無いキー"
    assert sorted(set(EN) - set(JA)) == [], "ja.toml に無いキー"


def test_placeholders_match_per_key() -> None:
    bad = {k: (_fields(JA[k]), _fields(EN[k])) for k in JA.keys() & EN.keys()
           if _fields(JA[k]) != _fields(EN[k])}
    assert not bad, bad


def test_format_specs_match_per_key() -> None:
    """``{fault:X}`` や ``{host:02X}`` の書式指定も揃っていること。"""
    bad = {k: (_specs(JA[k]), _specs(EN[k])) for k in JA.keys() & EN.keys()
           if _specs(JA[k]) != _specs(EN[k])}
    assert not bad, bad


def test_en_has_no_japanese_or_empty_text() -> None:
    jp = {k: v for k, v in EN.items() if _JA.search(v)}
    assert not jp, jp
    # 空にしてよいのは、見出しを省いて幅を節約する項目だけ(コード側で空なら表示しない)
    assert {k for k, v in EN.items() if not v.strip()} <= ALLOW_EMPTY


def test_en_strings_format_with_their_own_fields() -> None:
    """波括弧の取り違え(``format`` が例外になる文字列)が無いこと。"""
    for text in EN.values():
        text.format(**dict.fromkeys(_fields(text), 1))


def test_newline_and_list_separators_match() -> None:
    for key in JA.keys() & EN.keys():
        assert JA[key].count("\n") == EN[key].count("\n"), key
        assert JA[key].count("|") == EN[key].count("|"), key


def test_terms_of_art_consistent() -> None:
    assert EN["tier.low.short"] == "Low power" and EN["tier.high.short"] == "High power"
    for key in ("controller.mit", "controller.csp", "controller.pp", "controller.jog"):
        assert EN[key] == JA[key]
    assert "E-STOP" in EN["header.estop"] and "E-STOP" in EN["banner.estop_title"]
    assert not any("emergency" in v.lower() for v in EN.values())


# ---------------------------------------------------------------- 言語の読込・tr
def test_tr_in_english() -> None:
    i18n.load_catalog("en")
    assert i18n.current_language() == "en"
    assert i18n.tr("header.estop") == EN["header.estop"] != JA["header.estop"]
    assert i18n.tr("status.target", id=1, name="A") == "Target: ID 1  A"
    assert i18n.tr("ui.motor_id", id=3) == "ID3"
    i18n.load_catalog("ja")
    assert i18n.tr("header.estop") == "非常停止"


def test_unsupported_language_falls_back_to_ja(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=i18n.__name__):
        i18n.load_catalog("fr")
    assert i18n.current_language() == "ja"
    assert any("fr" in r.message for r in caplog.records)


def test_missing_en_key_falls_back_to_ja_then_key_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    i18n.load_catalog("en")
    monkeypatch.delitem(i18n._CATALOG, "header.more")  # en にだけ無いキーを作る
    with caplog.at_level(logging.WARNING, logger=i18n.__name__):
        assert i18n.tr("header.more") == JA["header.more"]  # ja
        assert i18n.tr("header.more") == JA["header.more"]
        assert i18n.tr("no.such.key") == "no.such.key"  # キー
        assert i18n.tr("no.such.key") == "no.such.key"
    msgs = [r.message for r in caplog.records]
    assert sum("header.more" in m for m in msgs) == 1
    assert sum("no.such.key" in m for m in msgs) == 1


def test_literal_text_passes_through_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    i18n.load_catalog("en")
    with caplog.at_level(logging.WARNING, logger=i18n.__name__):
        assert i18n.tr("そのままの文字列") == "そのままの文字列"
        assert i18n.tr("{n} 件", n=2) == "2 件"
    assert not caplog.records


# ---------------------------------------------------------------- 言語の決定
def test_resolve_language_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(i18n.ENV_VAR, raising=False)
    assert i18n.resolve_language() == "ja"
    assert i18n.resolve_language(saved="en") == "en"
    monkeypatch.setenv(i18n.ENV_VAR, "ja")
    assert i18n.resolve_language(saved="en") == "ja"  # 環境変数 > 保存値
    assert i18n.resolve_language("en", saved="ja") == "en"  # --lang > 環境変数
    monkeypatch.setenv(i18n.ENV_VAR, "en_US.UTF-8")
    assert i18n.resolve_language() == "en"


def test_resolve_language_ignores_unsupported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(i18n.ENV_VAR, "xx")
    with caplog.at_level(logging.WARNING, logger=i18n.__name__):
        assert i18n.resolve_language(saved="en") == "en"
    assert i18n.normalize_language("EN-us") == "en"
    assert i18n.normalize_language("klingon") is None
    assert i18n.normalize_language(None) is None


def test_cli_lang_option() -> None:
    from edulite05.app.main import _parse

    args, _ = _parse(["--lang", "EN", "--demo"])
    assert args.lang == "en"
    assert _parse(["--demo"])[0].lang is None
    with pytest.raises(SystemExit):
        _parse(["--lang", "xx"])


def test_saved_language_reads_ini(tmp_path: Path) -> None:
    from PySide6.QtCore import QSettings

    from edulite05.app.main import saved_language

    ini = tmp_path / "ui_state.ini"
    assert saved_language(ini) is None  # ファイルが無い
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    s.setValue("prefs/language", "en")
    s.sync()
    assert saved_language(ini) == "en"
    assert saved_language(None) is None


# ---------------------------------------------------------------- プラグインの文字列
PLUGIN_DIRS = [ROOT / "plugins" / "example_triangle", ROOT / "plugins" / "scripting",
               ROOT / "src" / "edulite05" / "builtin"]


def _literal_tr_args(directory: Path) -> set[str]:
    """プラグインのソースにある ``tr("日本語の原文")`` の原文(キー形式でないもの)。"""
    out: set[str] = set()
    for py in directory.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and not i18n._KEY_SHAPE.match(node.args[0].value)):
                out.add(node.args[0].value)
    return out


@pytest.mark.parametrize("directory", PLUGIN_DIRS, ids=lambda p: p.name)
def test_plugin_literal_strings_have_english(directory: Path) -> None:
    assert i18n.add_strings_dir(directory) >= 1
    en = i18n.plugin_strings("en")
    missing = sorted(s for s in _literal_tr_args(directory) if s not in en)
    assert not missing, missing
    assert not [k for k, v in en.items() if _JA.search(v)]


@pytest.mark.parametrize("directory", PLUGIN_DIRS, ids=lambda p: p.name)
def test_plugin_placeholders_match(directory: Path) -> None:
    i18n.add_strings_dir(directory)
    for key, text in i18n.plugin_strings("en").items():
        assert _fields(key) == _fields(text), key
        assert _specs(key) == _specs(text), key


def test_plugin_name_and_description_have_english() -> None:
    """プラグインの ``name`` / ``description``(設定ページに出る)の訳があること。"""
    for directory, cls_file in ((PLUGIN_DIRS[0], "plugin.py"), (PLUGIN_DIRS[1], "plugin.py")):
        i18n.add_strings_dir(directory)
        tree = ast.parse((directory / cls_file).read_text(encoding="utf-8"))
        wanted = {
            n.value.value for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            and n.target.id in ("name", "description")
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
        }
        assert wanted
        assert wanted <= set(i18n.plugin_strings("en")), wanted


def test_plugin_strings_translate_and_fall_back() -> None:
    i18n.add_strings_dir(ROOT / "plugins" / "example_triangle")
    i18n.load_catalog("en")
    assert i18n.tr("三角波") == "Triangle"
    text = i18n.tr(
        "{name} {version}(plugins/example_triangle)。設定ページで有効・無効を切り替えられます。",
        name="X", version="1",
    )
    assert text.startswith("X 1 (plugins/example_triangle)")
    assert i18n.tr("訳の無い文字列") == "訳の無い文字列"
    i18n.load_catalog("ja")
    assert i18n.tr("三角波") == "三角波"  # ja は原文のまま


def test_plugin_strings_with_section_keys_fall_back_to_ja() -> None:
    i18n.add_strings("ja", {"myplug": {"title": "題名", "only_ja": "日本語だけ"}})
    i18n.add_strings("en", {"myplug": {"title": "Title"}})
    i18n.load_catalog("en")
    assert i18n.tr("myplug.title") == "Title"
    assert i18n.tr("myplug.only_ja") == "日本語だけ"
    i18n.load_catalog("ja")
    assert i18n.tr("myplug.title") == "題名"


def test_manager_loads_plugin_strings_on_discovery(tmp_path: Path) -> None:
    from edulite05.plugin_api import PluginManager

    d = tmp_path / "plugins" / "hello"
    (d / "i18n").mkdir(parents=True)
    (d / "plugin.py").write_text(
        "from edulite05.plugin_api import Plugin\n"
        "class P(Plugin):\n"
        "    id = 'hello'\n    name = 'こんにちは'\n    requires_gui = False\n"
        "    def activate(self, ctx): pass\n"
        "PLUGIN = P()\n", encoding="utf-8")
    (d / "i18n" / "en.toml").write_text('"こんにちは" = "Hello"\n', encoding="utf-8")
    mgr = PluginManager(gui=False, project_root=tmp_path, plugins_dir=tmp_path / "plugins",
                        builtin=(), entry_points=False)
    mgr.load_all()
    i18n.load_catalog("en")
    rec = mgr.record("hello")
    assert rec is not None and i18n.tr(rec.name) == "Hello"


# ---------------------------------------------------------------- シーケンスの手順の表示名
def _load_step_text():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "scripting_step_text", ROOT / "plugins" / "scripting" / "step_text.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sequence_step_text_matches_core_in_ja_and_is_english_in_en() -> None:
    from edulite05.core.sequence import load_sequence

    st = _load_step_text()
    seq = load_sequence(ROOT / "sequences" / "velocity_steps.yaml")
    kinds = {s.kind for s in seq.steps}
    assert {"enable", "wait", "record", "start", "set_params", "stop", "run", "disable"} <= kinds
    for step in seq.steps:  # ja: core の日本語と一字一句同じ
        assert st.step_title(step) == step.title()
        assert st.step_detail(step) == step.detail()
        assert st.step_line(step) == step.describe()
    i18n.load_catalog("en")
    for step in seq.steps:
        line = st.step_line(step)
        assert not _JA.search(line), line
    titles = [st.step_title(s) for s in seq.steps]
    assert titles[0] == "Enable" and "Wait 0.5 s" in titles and "Run velocity for 2 s" in titles


def test_sequence_step_text_covers_every_step_kind() -> None:
    st = _load_step_text()
    assert st.known_kinds() == {
        "enable", "start", "run", "set_params", "wait", "stop", "record", "log", "disable"
    }
