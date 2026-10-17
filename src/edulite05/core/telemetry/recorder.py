"""CSV Recorder(phase2.md §1.6)。

選択チャネルの追記を購読し、専用スレッドで CSV へ書く(RX スレッドをディスク I/O で止めない)。

形式: 先頭に `# ` 始まりのメタデータ行(プロファイル名・開始日時・出力段階・チャネル定義など)、
続いて列見出し `time_s,<ch>,...`。1 サンプル追記毎に 1 行で、他チャネルは直近値を保持して出力
(未受信は空欄)。`time_s` は記録開始からの相対秒(開始時の `clock.now()` はヘッダの `clock_t0`)。
pandas なら `read_csv(path, comment="#")` で読める。
"""

from __future__ import annotations

import csv
import datetime as _dt
import queue
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from edulite05.core import clock
from edulite05.core.telemetry.hub import TelemetryHub

_STOP = object()


def _clean(s: object) -> str:
    return str(s).replace("\r", " ").replace("\n", " ")


class Recorder:
    def __init__(self, hub: TelemetryHub) -> None:
        self._hub = hub
        self._lock = threading.Lock()
        self._q: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._unsub: Callable[[], None] | None = None
        self._path: Path | None = None
        self._rows = 0
        self._error: BaseException | None = None

    # -- 状態 ---------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._thread is not None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def error(self) -> BaseException | None:
        """書込みスレッドで起きた例外(あれば)。"""
        return self._error

    # -- 開始/停止 -------------------------------------------------------------
    def start(
        self,
        path: str | Path,
        channels: Sequence[str],
        *,
        profile_name: str = "",
        tier: str = "",
        extra: Mapping[str, object] | None = None,
    ) -> None:
        """記録開始。`channels` は登録済みである必要がある(未登録は `KeyError`)。"""
        names = list(dict.fromkeys(channels))
        if not names:
            raise ValueError("channels が空です")
        infos = [self._hub.info(n) for n in names]  # KeyError を先に出す
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("すでに記録中です")
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            f = p.open("w", encoding="utf-8", newline="")
            t0 = clock.now()
            try:
                started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
                f.write("# edulite05 telemetry recording\n")
                f.write(f"# profile: {_clean(profile_name)}\n")
                f.write(f"# started: {started}\n")
                f.write(f"# tier: {_clean(tier)}\n")
                f.write(f"# clock_t0: {t0!r}\n")
                for k, v in (extra or {}).items():
                    f.write(f"# {_clean(k)}: {_clean(v)}\n")
                for i in infos:
                    f.write(
                        f"# channel: {i.name} unit={_clean(i.unit)} motor={i.motor_id} "
                        f"kind={_clean(i.kind)} label={_clean(i.label)}\n"
                    )
                w = csv.writer(f, lineterminator="\n")
                w.writerow(["time_s", *names])
            except BaseException:
                f.close()
                raise
            self._path = p
            self._rows = 0
            self._error = None
            q: queue.SimpleQueue[object] = queue.SimpleQueue()
            self._q = q
            wanted = frozenset(names)

            def sink(name: str, t: float, v: float) -> None:
                if name in wanted and t >= t0:
                    q.put((name, t, v))

            self._thread = threading.Thread(
                target=self._run, args=(f, w, names, t0, q), name="telemetry-recorder", daemon=True
            )
            self._thread.start()
            self._unsub = self._hub.subscribe(sink)

    def stop(self) -> int:
        """記録停止(ファイルを閉じる)。書いた行数を返す。記録中でなければ 0。"""
        with self._lock:
            th = self._thread
            if th is None:
                return 0
            if self._unsub is not None:
                self._unsub()
                self._unsub = None
            self._q.put(_STOP)
            th.join()
            self._thread = None
            return self._rows

    def _run(
        self,
        f: TextIO,
        w: Any,
        names: list[str],
        t0: float,
        q: queue.SimpleQueue[object],
    ) -> None:
        index = {n: i for i, n in enumerate(names)}
        last: list[str] = [""] * len(names)
        try:
            while True:
                item = q.get()
                if item is _STOP:
                    break
                name, t, v = item  # type: ignore[misc]
                last[index[name]] = format(v, ".9g")
                w.writerow([format(t - t0, ".6f"), *last])
                self._rows += 1
        except BaseException as e:  # noqa: BLE001 - 書込みスレッドの失敗を保持する
            self._error = e
        finally:
            f.close()
