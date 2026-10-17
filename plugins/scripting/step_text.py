"""シーケンスの手順の表示名(UI 言語に合わせる)。

core の `Step.title()` / `detail()` / `describe()` は日本語固定(core は Qt も i18n も使わない)。
画面ではこのモジュールが `kind` から `tr("script.step_*")` で組み立てる。
日本語の出力は core と同じ。
core が作る実行ログ(`ev.kind == "log"`)や検証メッセージは対象外(日本語のまま)。
"""

from __future__ import annotations

from typing import Any

from edulite05.app.i18n import catalog, tr
from edulite05.core.protocol.types import RunMode
from edulite05.core.sequence import schema

_MODE_KEYS = {
    RunMode.OPERATION: "mit", RunMode.PP: "pp", RunMode.VELOCITY: "velocity",
    RunMode.CURRENT: "current", RunMode.CSP: "csp",
}


def _controller(cls: type) -> str:
    name = getattr(cls, "name", "")
    key = f"script.ctrl_{name}"
    return tr(key) if key in catalog() else name  # 知らないコントローラは名前そのまま


def step_title(step: Any) -> str:
    kind = getattr(step, "kind", "")
    if kind == "enable":
        return tr("script.step_enable")
    if kind == "start":
        return tr("script.step_start", ctrl=_controller(step.controller))
    if kind == "run":
        return tr("script.step_run", ctrl=_controller(step.controller), sec=f"{step.duration:g}")
    if kind == "set_params":
        return tr("script.step_set_params")
    if kind == "wait":
        return tr("script.step_wait", sec=f"{step.duration:g}")
    if kind == "stop":
        return tr("script.step_stop")
    if kind == "record":
        started = step.action == "start"
        return tr("script.step_record_start" if started else "script.step_record_stop")
    if kind == "log":
        return tr("script.step_log", text=step.text)
    if kind == "disable":
        return tr("script.step_disable")
    return str(step.title())


def step_detail(step: Any) -> str:
    if getattr(step, "kind", "") == "enable":
        key = _MODE_KEYS.get(step.mode)
        return tr(f"script.mode_{key}") if key else str(step.detail())
    return str(step.detail())


def step_line(step: Any) -> str:
    """1 行の説明(進捗・ログ用。core の `Step.describe()` に相当)。"""
    detail = step_detail(step)
    title = step_title(step)
    return f"{title}({detail})" if detail else title


def known_kinds() -> set[str]:
    """このモジュールが訳せる手順の種類(テスト用)。"""
    return {c.kind for c in vars(schema).values()
            if isinstance(c, type) and issubclass(c, schema.Step) and c is not schema.Step}
