"""CSV 記録(⏺ 記録、ui_design.md §5.1)。Qt を使わない純粋なロジック。

`Recorder(directory, bridge)` はブリッジの `start_recording` / `stop_recording` に任せる
(CoreBridge は TelemetryHub の Recorder で全レート、DemoBridge は状態更新ごとの行)。
ブリッジなし(`Recorder(directory)`)のときは、従来どおり `add(states)` で状態を溜め、停止時に
CSV へ書き出す。グラフ用のリングバッファ(`Bridge.scope_data`)は「現在 = 0」の相対時刻しか返さず、
呼出し間でサンプルを突き合わせられないため、記録には使わない。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from edulite05.app.bridge import Bridge
from edulite05.app.state_csv import COLUMNS, write_state_csv
from edulite05.app.viewmodels import MotorViewState
from edulite05.core import clock

__all__ = ["COLUMNS", "Recorder", "default_record_dir", "format_elapsed"]


def format_elapsed(seconds: float) -> str:
    """`00:12` 形式(1 時間以上は `1:02:03`)。"""
    s = max(int(seconds), 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def default_record_dir() -> Path:
    """プロジェクトルートの user_data/records(git 管理外)。"""
    return Path(__file__).resolve().parents[5] / "user_data" / "records"


class Recorder:
    """`start()` → (`add(states)` を繰り返す)→ `stop()` で CSV を保存してパスを返す。"""

    def __init__(self, directory: Path | None = None, bridge: Bridge | None = None) -> None:
        self._dir = directory
        self._bridge = bridge
        self._active = False
        self._t0 = 0.0
        self._rows: list[tuple[float, dict[int, MotorViewState]]] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def rows(self) -> int:
        if self._bridge is not None:
            return self._bridge.recording_rows() if self._active else 0
        return len(self._rows)

    def elapsed(self) -> float:
        return clock.now() - self._t0 if self._active else 0.0

    def _new_path(self) -> Path:
        directory = self._dir or default_record_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"record_{datetime.now():%Y%m%d_%H%M%S}.csv"

    def start(self) -> None:
        self._rows = []
        self._t0 = clock.now()
        if self._bridge is not None:
            self._bridge.start_recording(self._new_path(), None)
        self._active = True

    def add(self, states: dict[int, MotorViewState]) -> None:
        """ブリッジなしのときだけ状態を溜める(ブリッジ使用時はブリッジが自分で記録する)。"""
        if self._bridge is None and self._active and states:
            self._rows.append((clock.now() - self._t0, dict(states)))

    def discard(self) -> None:
        was = self._active
        self._active = False
        self._rows = []
        if self._bridge is not None and was:
            path = self._bridge.stop_recording()
            if path is not None:
                path.unlink(missing_ok=True)

    def stop(self) -> Path | None:
        """記録を止めて保存する。データが無ければ None。書込み失敗は OSError を投げる。"""
        was = self._active
        self._active = False
        rows, self._rows = self._rows, []
        if self._bridge is not None:
            return self._bridge.stop_recording() if was else None
        if not rows:
            return None
        path = self._new_path()
        write_state_csv(path, rows)
        return path
