"""シーケンス定義(YAML)の読込と検証(phase4_5.md M)。Qt を import しない。

`sequences/*.yaml` の形(1 ステップ = 1 つのキー。値が要らないものは文字列だけでもよい)::

    name: 速度ステップ            # 省略可(既定はファイル名)
    description: ゆっくり速度を変える   # 省略可
    motor: 127                   # 省略可(実行時に指定した ID が優先)
    steps:
      - enable: velocity                    # 有効化(run_mode: velocity/current/csp/pp/mit)
      - run:                                # コントローラを N 秒動かして運転停止
          controller: velocity
          params: {velocity: 1.0, accel: 20}
          seconds: 3
      - start: {controller: velocity, params: {velocity: 0.5}}   # 開始だけ(待たない)
      - set_params: {velocity: 1.0}         # 運転中の目標を変える
      - wait: 2                             # N 秒待つ
      - stop                                # 運転停止(有効のまま保持)
      - record: start                       # 記録の開始 / 停止
      - record: {action: start, path: velocity_steps.csv}
      - log: 区切り                          # 実行ログへメッセージ
      - disable                             # 無効化

検証は**実行前にすべて**行い、問題を `SequenceError.problems`(行番号つき・日本語)にまとめて返す:
YAML の構文、未知のキー、型、`Params` のフィールド名・範囲、run_mode の整合
(enable したモードと controller の run_mode)、手順の順序(enable 前の start など)。
出力段階 LOW の上限を超える値は**警告**(実行時は SafetyGuard が制限する)。
"""

from __future__ import annotations

import dataclasses
import difflib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import yaml

from edulite05.core.protocol.types import RunMode

MAX_SECONDS = 3600.0  # 1 ステップの上限(誤入力で何時間も回し続けない)

# enable の run_mode 名(YAML の値)→ RunMode
RUN_MODE_NAMES: dict[str, RunMode] = {
    "velocity": RunMode.VELOCITY,
    "current": RunMode.CURRENT,
    "csp": RunMode.CSP,
    "pp": RunMode.PP,
    "mit": RunMode.OPERATION,
    "operation": RunMode.OPERATION,
}
RUN_MODE_LABELS: dict[RunMode, str] = {
    RunMode.OPERATION: "運控(MIT)",
    RunMode.PP: "位置(PP)",
    RunMode.VELOCITY: "速度",
    RunMode.CURRENT: "電流",
    RunMode.CSP: "位置(CSP)",
}
CONTROLLER_LABELS: dict[str, str] = {
    "velocity": "速度",
    "current": "電流",
    "csp": "位置(CSP)",
    "pp": "位置(PP)",
    "mit": "MIT",
    "sine": "正弦波",
    "step": "ステップ",
}
ACTIONS = ("enable", "start", "run", "set_params", "wait", "stop", "record", "log", "disable")
_NO_ARG_ACTIONS = ("stop", "disable")
_TOP_KEYS = ("name", "description", "motor", "steps")


# ====================================================================== エラー
@dataclass(frozen=True, slots=True)
class Problem:
    """検証で見つかった問題 1 件。`line` は 1 始まり(不明なら None)。"""

    message: str
    line: int | None = None
    warning: bool = False

    def format(self, source: str = "") -> str:
        where = f"{source}:" if source else ""
        loc = f"{where}行 {self.line}" if self.line is not None else (where.rstrip(":") or "")
        head = f"{loc}: " if loc else ""
        return f"{head}{self.message}"


class SequenceError(Exception):
    """シーケンスを読めない / 検証に失敗した。`problems` に全件(行番号つき)が入る。"""

    def __init__(self, problems: list[Problem], source: str = "") -> None:
        self.problems = problems
        self.source = source
        super().__init__("\n".join(p.format(source) for p in problems))


# ====================================================================== ステップ
@dataclass(frozen=True, slots=True)
class Step:
    line: int
    kind: ClassVar[str] = ""

    def title(self) -> str:
        """短い名前(手順の一覧の 1 行目)。"""
        raise NotImplementedError

    def detail(self) -> str:
        """補足(パラメータなど。手順の一覧の 2 行目)。無ければ空。"""
        return ""

    def describe(self) -> str:
        """1 行の説明(ログ・CLI 用)。"""
        d = self.detail()
        return f"{self.title()}({d})" if d else self.title()

    @property
    def seconds(self) -> float:
        """このステップが待つ時間の見積り(秒)。"""
        return 0.0


@dataclass(frozen=True, slots=True)
class EnableStep(Step):
    mode: RunMode = RunMode.VELOCITY
    kind: ClassVar[str] = "enable"

    def title(self) -> str:
        return "有効化"

    def detail(self) -> str:
        return RUN_MODE_LABELS[self.mode]


@dataclass(frozen=True, slots=True)
class StartStep(Step):
    controller: type = object
    params: Any = None
    given: tuple[str, ...] = ()  # YAML で明示したフィールド名(表示用)
    kind: ClassVar[str] = "start"

    def title(self) -> str:
        return f"{_ctrl_label(self.controller)}で運転開始"

    def detail(self) -> str:
        return _given_text(self.params, self.given)


@dataclass(frozen=True, slots=True)
class RunStep(Step):
    controller: type = object
    params: Any = None
    given: tuple[str, ...] = ()
    duration: float = 1.0
    kind: ClassVar[str] = "run"

    def title(self) -> str:
        return f"{_ctrl_label(self.controller)}で {self.duration:g} 秒運転"

    def detail(self) -> str:
        return _given_text(self.params, self.given)

    @property
    def seconds(self) -> float:
        return self.duration


@dataclass(frozen=True, slots=True)
class SetParamsStep(Step):
    changes: Mapping[str, Any] = field(default_factory=dict)
    kind: ClassVar[str] = "set_params"

    def title(self) -> str:
        return "目標を変更"

    def detail(self) -> str:
        return ", ".join(f"{k}={_fmt(v)}" for k, v in self.changes.items())


@dataclass(frozen=True, slots=True)
class WaitStep(Step):
    duration: float = 0.0
    kind: ClassVar[str] = "wait"

    def title(self) -> str:
        return f"{self.duration:g} 秒待つ"

    @property
    def seconds(self) -> float:
        return self.duration


@dataclass(frozen=True, slots=True)
class StopStep(Step):
    kind: ClassVar[str] = "stop"

    def title(self) -> str:
        return "運転停止"


@dataclass(frozen=True, slots=True)
class RecordStep(Step):
    action: str = "start"  # "start" / "stop"
    path: str | None = None  # start のときの保存先(相対は記録フォルダ基準)
    kind: ClassVar[str] = "record"

    def title(self) -> str:
        return "記録を開始" if self.action == "start" else "記録を停止"

    def detail(self) -> str:
        return (self.path or "") if self.action == "start" else ""


@dataclass(frozen=True, slots=True)
class LogStep(Step):
    text: str = ""
    kind: ClassVar[str] = "log"

    def title(self) -> str:
        return f"ログ: {self.text}"


@dataclass(frozen=True, slots=True)
class DisableStep(Step):
    kind: ClassVar[str] = "disable"

    def title(self) -> str:
        return "無効化"


@dataclass(frozen=True, slots=True)
class Sequence:
    name: str
    description: str
    motor: int | None
    steps: tuple[Step, ...]
    source: str = ""
    warnings: tuple[Problem, ...] = ()

    @property
    def total_seconds(self) -> float:
        """待ち時間の見積り(有効化・無効化などの所要は含まない)。"""
        return sum(s.seconds for s in self.steps)

    def describe_steps(self) -> list[str]:
        return [s.describe() for s in self.steps]


def _ctrl_label(cls: type) -> str:
    name = getattr(cls, "name", "")
    return CONTROLLER_LABELS.get(name, name)


def _fmt(v: Any) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _given_text(params: Any, given: tuple[str, ...]) -> str:
    return ", ".join(f"{k}={_fmt(getattr(params, k))}" for k in given)


# ====================================================================== 読込
def load_sequence(path: str | Path, *, profile: Any = None) -> Sequence:
    """ファイルを読んで検証する。問題があれば `SequenceError`。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise SequenceError([Problem(f"ファイルを読めません: {e}")], str(p)) from e
    except UnicodeDecodeError as e:
        raise SequenceError([Problem(f"UTF-8 で読めません: {e}")], str(p)) from e
    return parse_sequence(text, source=p.name, default_name=p.stem, profile=profile)


def parse_sequence(
    text: str, *, source: str = "", default_name: str = "", profile: Any = None
) -> Sequence:
    """YAML 文字列を検証して `Sequence` にする。問題があれば `SequenceError`(全件)。"""
    parser = _Parser(text, source, default_name, profile)
    seq = parser.parse()
    errors = [p for p in parser.problems if not p.warning]
    if errors:
        raise SequenceError(sorted(parser.problems, key=_order), source)
    return dataclasses.replace(
        seq, warnings=tuple(p for p in parser.problems if p.warning)
    )


def _order(p: Problem) -> tuple[int, int]:
    return (p.line if p.line is not None else 0, 1 if p.warning else 0)


class _Parser:
    def __init__(self, text: str, source: str, default_name: str, profile: Any) -> None:
        self.text = text
        self.source = source
        self.default_name = default_name or "sequence"
        self._profile = profile
        self.problems: list[Problem] = []
        self._loader = yaml.SafeLoader(text)
        # 静的な状態(手順の順序の検査)
        self._mode: RunMode | None = None
        self._running = False
        self._ctrl: type | None = None
        self._recording = False
        self._rec_line = 0

    # ------------------------------------------------------------ 補助
    def err(self, node_or_line: Any, message: str) -> None:
        self.problems.append(Problem(message, _line(node_or_line)))

    def warn(self, node_or_line: Any, message: str) -> None:
        self.problems.append(Problem(message, _line(node_or_line), warning=True))

    def value(self, node: yaml.Node) -> Any:
        return self._loader.construct_object(node, deep=True)

    # ------------------------------------------------------------ 全体
    def parse(self) -> Sequence:
        try:
            root = self._loader.get_single_node()
        except yaml.YAMLError as e:
            self.problems.append(_yaml_problem(e))
            return _empty(self.default_name)
        finally:
            self._loader.dispose()
        if root is None:
            self.err(1, "ファイルが空です(steps を書いてください)")
            return _empty(self.default_name)
        if not isinstance(root, yaml.MappingNode):
            self.err(root, "先頭は「キー: 値」の形にしてください(name / steps など)")
            return _empty(self.default_name)
        pairs = self._mapping_pairs(root, _TOP_KEYS, "最上位")
        name = self._opt_str(pairs, "name", self.default_name)
        desc = self._opt_str(pairs, "description", "")
        motor = self._opt_motor(pairs)
        steps: list[Step] = []
        if "steps" not in pairs:
            self.err(root, "steps(手順の一覧)がありません")
        else:
            knode, vnode = pairs["steps"]
            if not isinstance(vnode, yaml.SequenceNode):
                self.err(vnode, "steps は「- 手順」の一覧にしてください")
            elif not vnode.value:
                self.err(vnode, "steps が空です(手順を 1 つ以上書いてください)")
            else:
                for item in vnode.value:
                    st = self._step(item)
                    if st is not None:
                        steps.append(st)
                self._finish(vnode)
        return Sequence(name, desc, motor, tuple(steps), self.source)

    def _finish(self, steps_node: yaml.Node) -> None:
        if self._recording:
            self.warn(
                self._rec_line, "record: stop がありません(終了時に自動で記録を止めます)"
            )

    def _mapping_pairs(
        self, node: yaml.MappingNode, allowed: tuple[str, ...], where: str
    ) -> dict[str, tuple[yaml.Node, yaml.Node]]:
        out: dict[str, tuple[yaml.Node, yaml.Node]] = {}
        for k, v in node.value:
            if not isinstance(k, yaml.ScalarNode):
                self.err(k, f"{where}のキーは文字列にしてください")
                continue
            key = str(k.value)
            if key in out:
                self.err(k, f"キー {key!r} が重複しています")
                continue
            if key not in allowed:
                self.err(k, _unknown(key, allowed, where))
                continue
            out[key] = (k, v)
        return out

    def _opt_str(
        self, pairs: dict[str, tuple[yaml.Node, yaml.Node]], key: str, default: str
    ) -> str:
        if key not in pairs:
            return default
        node = pairs[key][1]
        v = self.value(node)
        if v is None:
            return default
        if isinstance(v, (dict, list)):
            self.err(node, f"{key} は文字列にしてください")
            return default
        return str(v)

    def _opt_motor(self, pairs: dict[str, tuple[yaml.Node, yaml.Node]]) -> int | None:
        if "motor" not in pairs:
            return None
        node = pairs["motor"][1]
        v = self.value(node)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 127:
            self.err(node, f"motor は 1〜127 の整数にしてください(いまの値: {v!r})")
            return None
        return v

    # ------------------------------------------------------------ ステップ
    def _step(self, item: yaml.Node) -> Step | None:
        line = _line(item)
        if isinstance(item, yaml.ScalarNode):
            action = str(item.value)
            if action not in ACTIONS:
                self.err(item, _unknown(action, ACTIONS, "手順"))
                return None
            if action not in _NO_ARG_ACTIONS:
                self.err(item, f"{action} には内容が必要です(例: `{action}: …`)")
                return None
            return self._build(action, None, line)
        if not isinstance(item, yaml.MappingNode) or len(item.value) != 1:
            self.err(
                item,
                "各手順は「- 動作: 内容」の 1 つのキーだけにしてください"
                "(インデントを確認してください)",
            )
            return None
        knode, vnode = item.value[0]
        if not isinstance(knode, yaml.ScalarNode):
            self.err(knode, "動作の名前は文字列にしてください")
            return None
        action = str(knode.value)
        if action not in ACTIONS:
            self.err(knode, _unknown(action, ACTIONS, "手順"))
            return None
        return self._build(action, vnode, line)

    def _build(self, action: str, vnode: yaml.Node | None, line: int) -> Step | None:
        fn = getattr(self, f"_s_{action}")
        return fn(vnode, line)  # type: ignore[no-any-return]

    # ---- enable
    def _s_enable(self, vnode: yaml.Node | None, line: int) -> Step | None:
        name: Any
        if isinstance(vnode, yaml.MappingNode):
            pairs = self._mapping_pairs(vnode, ("mode",), "enable")
            if "mode" not in pairs:
                self.err(vnode, "enable には mode が必要です(例: `enable: velocity`)")
                return None
            node = pairs["mode"][1]
            name = self.value(node)
        else:
            node = vnode
            name = self.value(vnode) if vnode is not None else None
        if not isinstance(name, str) or name.lower() not in RUN_MODE_NAMES:
            self.err(
                node if node is not None else line,
                f"enable のモードは {' / '.join(sorted(set(RUN_MODE_NAMES)))} のどれかです"
                f"(いまの値: {name!r})",
            )
            return None
        mode = RUN_MODE_NAMES[name.lower()]
        if self._mode is not None:
            self.err(line, "すでに有効化されています(先に disable してください)")
            return None
        self._mode, self._running = mode, False
        return EnableStep(line, mode)

    # ---- start / run
    def _s_start(self, vnode: yaml.Node | None, line: int) -> Step | None:
        return self._controller_step(vnode, line, with_seconds=False)

    def _s_run(self, vnode: yaml.Node | None, line: int) -> Step | None:
        return self._controller_step(vnode, line, with_seconds=True)

    def _controller_step(
        self, vnode: yaml.Node | None, line: int, *, with_seconds: bool
    ) -> Step | None:
        action = "run" if with_seconds else "start"
        if not isinstance(vnode, yaml.MappingNode):
            example = "{controller: velocity, params: {velocity: 1.0}, seconds: 3}"
            self.err(vnode if vnode is not None else line, f"{action} は {example} の形にします")
            return None
        allowed = ("controller", "params", "seconds") if with_seconds else ("controller", "params")
        pairs = self._mapping_pairs(vnode, allowed, action)
        ok = True
        cls = self._controller_class(pairs, vnode)
        if cls is None:
            ok = False
        duration = 0.0
        if with_seconds:
            if "seconds" not in pairs:
                self.err(vnode, "run には seconds(動かす秒数)が必要です")
                ok = False
            else:
                d = self._seconds(pairs["seconds"][1], "seconds", minimum=0.0, exclusive=True)
                if d is None:
                    ok = False
                else:
                    duration = d
        params = None
        given: tuple[str, ...] = ()
        if cls is not None:
            raw_node = pairs["params"][1] if "params" in pairs else None
            params, given, pok = self._build_params(cls, raw_node)
            ok = ok and pok
        if not ok or cls is None:
            return None
        # 順序と run_mode の整合
        if self._mode is None:
            self.err(line, f"{action} の前に enable が必要です")
            return None
        if self._running:
            self.err(line, "すでに運転中です(先に stop してください)")
            return None
        cmode = _controller_mode(cls, params)
        if cmode is not self._mode:
            self.err(
                line,
                f"run_mode が一致しません: enable は「{RUN_MODE_LABELS[self._mode]}」、"
                f"{_ctrl_label(cls)}コントローラは「{RUN_MODE_LABELS[cmode]}」です",
            )
            return None
        if with_seconds:
            return RunStep(line, cls, params, given, duration)
        self._running, self._ctrl = True, cls
        return StartStep(line, cls, params, given)

    def _controller_class(
        self, pairs: dict[str, tuple[yaml.Node, yaml.Node]], parent: yaml.Node
    ) -> type | None:
        from edulite05.core.control.controllers import CONTROLLERS
        from edulite05.core.control.params import PARAMS_BY_CONTROLLER

        if "controller" not in pairs:
            self.err(parent, "controller(コントローラ名)が必要です")
            return None
        node = pairs["controller"][1]
        name = self.value(node)
        usable = {  # 運転ページに出る 1 台用のコントローラ(JOG は押下保持専用なので除く)
            n: c for n, c in CONTROLLERS.items()
            if len(c.slots) == 1 and n != "jog" and n in PARAMS_BY_CONTROLLER
        }
        if name == "jog":
            self.err(
                node, "jog は押している間だけ回すためシーケンスでは使えません(velocity を使う)"
            )
            return None
        if not isinstance(name, str) or name not in usable:
            hint = ""
            if isinstance(name, str) and name in CONTROLLERS:
                hint = "(1 台用のコントローラだけ使えます)"
            elif isinstance(name, str):
                near = difflib.get_close_matches(name, list(usable), n=1)
                hint = f"(もしかして {near[0]!r}?)" if near else ""
            self.err(
                node,
                f"controller {name!r} は使えません{hint}。使えるもの: {', '.join(sorted(usable))}",
            )
            return None
        return usable[name]

    def _build_params(
        self, cls: type, node: yaml.Node | None
    ) -> tuple[Any, tuple[str, ...], bool]:
        pcls: type = cls.Params  # type: ignore[attr-defined]
        if node is None:
            return pcls(), (), True
        if not isinstance(node, yaml.MappingNode):
            if isinstance(node, yaml.ScalarNode) and self.value(node) is None:
                return pcls(), (), True
            self.err(node, "params は「名前: 値」の形にしてください")
            return pcls(), (), False
        names = tuple(f.name for f in dataclasses.fields(pcls))
        pairs = self._mapping_pairs(node, names, f"{cls.name} の params")  # type: ignore[attr-defined]
        values: dict[str, Any] = {}
        ok = len(pairs) == len(node.value)
        for key, (_k, v) in pairs.items():
            val = self._field_value(pcls, key, v)
            if val is _BAD:
                ok = False
            else:
                values[key] = val
        if not ok:
            return pcls(), (), False
        params = pcls(**values)
        self._low_warnings(pcls, pairs, params)
        return params, tuple(values), True

    def _field_value(self, pcls: type, key: str, node: yaml.Node) -> Any:
        f = next(f for f in dataclasses.fields(pcls) if f.name == key)
        v = self.value(node)
        meta = f.metadata
        choices = meta.get("choices")
        if choices is not None:
            allowed = [c[0] for c in choices]
            if v not in allowed:
                self.err(node, f"{key} は {' / '.join(allowed)} のどれかです(いまの値: {v!r})")
                return _BAD
            return v
        if isinstance(f.default, str):
            if not isinstance(v, str):
                self.err(node, f"{key} は文字列にしてください(いまの値: {v!r})")
                return _BAD
            return v
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            self.err(node, f"{key} は数値にしてください(いまの値: {v!r})")
            return _BAD
        v = float(v)
        if not math.isfinite(v):
            self.err(node, f"{key} は有限の数値にしてください")
            return _BAD
        lo, hi = meta.get("min"), meta.get("max")
        unit = meta.get("unit", "")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            rng = f"{_fmt(lo) if lo is not None else '−∞'} 〜 {_fmt(hi) if hi is not None else '∞'}"
            msg = f"{key} の範囲は {rng} {unit} です(いまの値: {_fmt(v)})"
            self.err(node, msg.replace("  ", " "))
            return _BAD
        return v

    def _low_warnings(
        self, pcls: type, pairs: dict[str, tuple[yaml.Node, yaml.Node]], params: Any
    ) -> None:
        low = self._low_limits()
        if low is None:
            return
        for f in dataclasses.fields(pcls):
            key = f.metadata.get("limit")
            if key is None or f.name not in pairs:
                continue
            limit = getattr(low, key, None)
            val = getattr(params, f.name)
            neg = f.metadata.get("neg_limit")  # 負側に別の上限がある項目(MIT の kd)
            if neg and isinstance(val, float) and val < 0.0:
                limit = getattr(low, neg, None)
            if limit is not None and isinstance(val, float) and abs(val) > limit:
                self.warn(
                    pairs[f.name][1],
                    f"{f.name}={_fmt(val)} は出力段階 LOW の上限 {_fmt(limit)} を超えます"
                    "(実行時は上限に制限されます)",
                )

    def _low_limits(self) -> Any:
        try:
            if self._profile is None:
                from edulite05.core.device.profile import MotorProfile

                self._profile = MotorProfile.load_builtin("el05")
            return self._profile.tier("low")
        except Exception:  # noqa: BLE001 - 警告が出せないだけ
            return None

    # ---- set_params
    def _s_set_params(self, vnode: yaml.Node | None, line: int) -> Step | None:
        if not isinstance(vnode, yaml.MappingNode) or not vnode.value:
            self.err(vnode if vnode is not None else line, "set_params は「名前: 値」の形にします")
            return None
        if not self._running or self._ctrl is None:
            self.err(line, "set_params は運転中(start の後)にだけ使えます")
            return None
        pcls: type = self._ctrl.Params  # type: ignore[attr-defined]
        names = tuple(f.name for f in dataclasses.fields(pcls))
        pairs = self._mapping_pairs(vnode, names, f"{self._ctrl.name} の params")  # type: ignore[attr-defined]
        changes: dict[str, Any] = {}
        ok = len(pairs) == len(vnode.value)
        for key, (k, v) in pairs.items():
            if key == "target":
                self.err(k, "target は運転中に変更できません")
                ok = False
                continue
            val = self._field_value(pcls, key, v)
            if val is _BAD:
                ok = False
            else:
                changes[key] = val
        if not ok:
            return None
        self._low_warnings(pcls, pairs, SimpleNamespace(**changes))
        return SetParamsStep(line, changes)

    # ---- wait
    def _s_wait(self, vnode: yaml.Node | None, line: int) -> Step | None:
        node: yaml.Node | None = vnode
        if isinstance(vnode, yaml.MappingNode):
            pairs = self._mapping_pairs(vnode, ("seconds",), "wait")
            if "seconds" not in pairs:
                self.err(vnode, "wait には seconds が必要です")
                return None
            node = pairs["seconds"][1]
        if node is None:
            self.err(line, "wait には秒数が必要です(例: `wait: 1.5`)")
            return None
        d = self._seconds(node, "wait", minimum=0.0, exclusive=False)
        return None if d is None else WaitStep(line, d)

    def _seconds(
        self, node: yaml.Node, what: str, *, minimum: float, exclusive: bool
    ) -> float | None:
        v = self.value(node)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.err(node, f"{what} は秒数(数値)にしてください(いまの値: {v!r})")
            return None
        bad = v <= minimum if exclusive else v < minimum
        if bad:
            cond = "0 より大きい" if exclusive else "0 以上の"
            self.err(node, f"{what} は {cond}数にしてください")
            return None
        if v > MAX_SECONDS:
            self.err(node, f"{what} は {MAX_SECONDS:g} 秒以下にしてください(いまの値: {v:g})")
            return None
        return float(v)

    # ---- stop / disable
    def _s_stop(self, vnode: yaml.Node | None, line: int) -> Step | None:
        if not self._no_arg(vnode, "stop"):
            return None
        self._running = False
        return StopStep(line)

    def _s_disable(self, vnode: yaml.Node | None, line: int) -> Step | None:
        if not self._no_arg(vnode, "disable"):
            return None
        self._mode, self._running = None, False
        return DisableStep(line)

    def _no_arg(self, vnode: yaml.Node | None, action: str) -> bool:
        if vnode is None:
            return True
        if isinstance(vnode, yaml.ScalarNode) and self.value(vnode) in (None, ""):
            return True
        if isinstance(vnode, yaml.MappingNode) and not vnode.value:
            return True
        self.err(vnode, f"{action} には内容を書きません(`- {action}` だけにしてください)")
        return False

    # ---- record
    def _s_record(self, vnode: yaml.Node | None, line: int) -> Step | None:
        action: Any
        path: str | None = None
        node: yaml.Node | None = vnode
        if isinstance(vnode, yaml.MappingNode):
            pairs = self._mapping_pairs(vnode, ("action", "path"), "record")
            if "action" not in pairs:
                self.err(vnode, "record には action(start / stop)が必要です")
                return None
            node = pairs["action"][1]
            action = self.value(node)
            if "path" in pairs:
                pv = self.value(pairs["path"][1])
                if not isinstance(pv, str) or not pv.strip():
                    self.err(pairs["path"][1], "path は保存先のファイル名(文字列)にしてください")
                    return None
                path = pv
        else:
            action = self.value(vnode) if vnode is not None else None
        if action not in ("start", "stop"):
            self.err(
                node if node is not None else line,
                f"record は start か stop です(いまの値: {action!r})",
            )
            return None
        if path is not None and action == "stop":
            self.err(line, "path は record: start のときだけ指定できます")
            return None
        if action == "start":
            if self._recording:
                self.err(line, "すでに記録中です(先に record: stop)")
                return None
            self._recording, self._rec_line = True, line
        else:
            if not self._recording:
                self.err(line, "記録を開始していません(先に record: start)")
                return None
            self._recording = False
        return RecordStep(line, action, path)

    # ---- log
    def _s_log(self, vnode: yaml.Node | None, line: int) -> Step | None:
        if vnode is None:
            self.err(line, "log にはメッセージが必要です")
            return None
        v = self.value(vnode)
        if isinstance(v, (dict, list)) or v is None:
            self.err(vnode, "log はメッセージ(文字列)にしてください")
            return None
        return LogStep(line, str(v))


# ====================================================================== 部品
_BAD = object()


def _line(node_or_line: Any) -> int | None:
    if node_or_line is None:
        return None
    if isinstance(node_or_line, int):
        return node_or_line
    return int(node_or_line.start_mark.line) + 1


def _empty(name: str) -> Sequence:
    return Sequence(name, "", None, ())


def _unknown(key: str, allowed: tuple[str, ...], where: str) -> str:
    near = difflib.get_close_matches(key, list(allowed), n=1)
    hint = f"(もしかして {near[0]!r}?)" if near else ""
    return f"{where}に {key!r} は使えません{hint}。使えるキー: {', '.join(allowed)}"


def _controller_mode(cls: type, params: Any) -> RunMode:
    """Controller の run_mode(試験信号は対象で決まるのでインスタンスで確かめる)。"""
    return cls(params).run_mode  # type: ignore[call-arg, no-any-return]


def _yaml_problem(e: yaml.YAMLError) -> Problem:
    mark = getattr(e, "problem_mark", None)
    problem = getattr(e, "problem", None) or str(e)
    context = getattr(e, "context", None)
    text = f"YAML の書式が正しくありません: {problem}"
    if context:
        text += f"({context})"
    if "\\t" in str(problem) or "tab" in str(problem).lower():
        text += "。インデントにタブは使えません(空白にしてください)"
    return Problem(text, mark.line + 1 if mark is not None else None)


def list_sequences(directory: str | Path) -> list[Path]:
    """`directory/*.yaml`(と `.yml`)を名前順で返す。無ければ空。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted([*d.glob("*.yaml"), *d.glob("*.yml")], key=lambda p: p.name.lower())
