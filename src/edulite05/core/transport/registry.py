"""トランスポートのレジストリ(phase1.md §1.1)。

``register(name, factory, describe)`` で登録し、``create(name, **kwargs)`` で生成する。
``describe`` は ``TransportInfo``、または引数なしで ``TransportInfo`` を返す callable。
``TransportInfo.name`` は登録名で上書きされる。
``available`` / ``unavailable_reason`` は依存不足で使えない場合に describe が返す
(``available()`` の一覧には載せ、``create`` は通常どおり試みて TransportError になる)。
``options_schema`` は ``{オプション名: {"type": "str"|"int"|"float"|"bool"|"list",
"label": 表示名, "default": 既定値(任意), "required": bool(任意), ...}}`` の素朴な dict
(UI がフォームを組むための情報。型の意味づけは UI 側が行う)。

``"sim"`` は ``edulite05.sim.transport`` が import 時に自分で register する。
``create("sim")`` / ``available()`` はその module を遅延 import する(未実装なら無視)。
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .base import CanTransport


@dataclass(frozen=True, slots=True)
class TransportInfo:
    name: str
    label: str
    options_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 任意依存(python-can 等)が無いときは False + 理由。UI は選べないようにして理由を表示する
    available: bool = True
    unavailable_reason: str = ""


Describe = TransportInfo | Callable[[], TransportInfo]
Factory = Callable[..., CanTransport]

_lock = threading.Lock()
_entries: dict[str, tuple[Factory, Describe]] = {}

# 遅延ロードする組込みトランスポート(import 時に自分で register する)
_LAZY_MODULES = {"sim": "edulite05.sim.transport"}
_lazy_done: set[str] = set()


def register(name: str, factory: Factory, describe: Describe) -> None:
    with _lock:
        _entries[name] = (factory, describe)


def unregister(name: str) -> None:
    """プラグインの読込失敗・無効化時の取消用。"""
    with _lock:
        _entries.pop(name, None)


def is_registered(name: str) -> bool:
    _load_lazy(name)
    with _lock:
        return name in _entries


def _load_lazy(only: str | None = None) -> None:
    for name, module in _LAZY_MODULES.items():
        if (only is not None and only != name) or name in _lazy_done:
            continue
        if name in _entries:
            _lazy_done.add(name)
            continue
        try:
            importlib.import_module(module)
        except ImportError:
            continue  # まだ実装されていない/依存不足
        _lazy_done.add(name)


def create(name: str, **kwargs: Any) -> CanTransport:
    if name not in _entries:
        _load_lazy(name)
    with _lock:
        entry = _entries.get(name)
    if entry is None:
        raise KeyError(f"unknown transport: {name!r} (available: {sorted(_entries)})")
    return entry[0](**kwargs)


def available() -> list[TransportInfo]:
    _load_lazy()
    with _lock:
        items = list(_entries.items())
    out: list[TransportInfo] = []
    for name, (_, describe) in items:
        info = describe() if callable(describe) else describe
        out.append(replace(info, name=name))
    return out


def _create_robstride_at(
    port: str,
    baudrate: int = 921600,
    read_chunk: int = 4096,
    min_frame_gap_s: float = 0.001,
    **kwargs: Any,
) -> CanTransport:
    from .at_serial import AtSerialTransport

    return AtSerialTransport(
        port, int(baudrate), int(read_chunk), min_frame_gap_s=float(min_frame_gap_s), **kwargs
    )


register(
    "robstride_at",
    _create_robstride_at,
    TransportInfo(
        name="robstride_at",
        label="RobStride 公式 USB-CAN (AT)",
        options_schema={
            "port": {"type": "str", "label": "COM ポート", "required": True},
            "baudrate": {"type": "int", "label": "ボーレート", "default": 921600},
            "min_frame_gap_s": {
                "type": "float",
                "label": "最小フレーム間隔 [s](0.5 ms 未満は取りこぼす。0 で無効)",
                "default": 0.001,
            },
        },
    ),
)


# -- python-can(任意依存)。pycan.py はモジュール先頭で can を import しないので、
#    ここでの遅延 import は軽く、未導入でも describe / create 自体は動く ----------------
def _create_pycan(
    interface: str,
    channel: str | None = None,
    bitrate: int = 1_000_000,
    min_frame_gap_s: float = 0.0,
    **kwargs: Any,
) -> CanTransport:
    from .pycan import PythonCanTransport

    extra = dict(kwargs)
    index = extra.pop("index", None)
    if index not in (None, ""):
        extra["index"] = int(index)  # gs_usb / candle のデバイス番号
    return PythonCanTransport(
        interface,
        channel or None,
        int(bitrate),
        min_frame_gap_s=float(min_frame_gap_s),
        **extra,
    )


def _describe_pycan() -> TransportInfo:
    from . import pycan

    ok = pycan.is_available()
    return TransportInfo(
        name="pycan",
        label="汎用 CAN アダプタ (python-can)",
        options_schema={
            "interface": {
                "type": "str",
                "label": "インタフェース",
                "required": True,
                "default": "slcan",
                "choices": list(pycan.KNOWN_INTERFACES),  # UI へのヒント(コンボボックス)
            },
            "channel": {
                "type": "str",
                "label": "チャネル(slcan: COM5 / pcan: PCAN_USBBUS1 / socketcan: can0)",
                "default": "",
            },
            "bitrate": {"type": "int", "label": "ビットレート [bps]", "default": 1_000_000},
            "index": {
                "type": "int",
                "label": "デバイス番号(gs_usb / candle で複数接続時)",
            },
            "min_frame_gap_s": {
                "type": "float",
                "label": "最小フレーム間隔 [s](0 で無効)",
                "default": 0.0,
            },
        },
        available=ok,
        unavailable_reason="" if ok else pycan.INSTALL_HINT,
    )


register("pycan", _create_pycan, _describe_pycan)


def _create_replay(
    path: str, speed: float = 1.0, loop: bool = False, **kwargs: Any
) -> CanTransport:
    from .replay import ReplayTransport

    return ReplayTransport(path, float(speed), bool(loop), **kwargs)


register(
    "replay",
    _create_replay,
    TransportInfo(
        name="replay",
        label="ログを再生 (読み出し専用)",
        options_schema={
            "path": {
                "type": "str",
                "label": "フレームログ(candump -L 形式)",
                "required": True,
                "widget": "file_open",  # UI へのヒント(ファイル選択ダイアログ)
            },
            "speed": {"type": "float", "label": "再生速度(倍)", "default": 1.0},
            "loop": {"type": "bool", "label": "繰り返し再生", "default": False},
        },
    ),
)
