"""コア共通の時計。

Windows の Python 3.12 では `time.monotonic()` の分解能が約 15.6 ms と粗く、フレームの
タイムスタンプ・要求の期限・rx_rate/fps の算出には使えない。コアの時刻(`CanFrame.timestamp`、
期限、レート)はすべてこの `now()`(`time.perf_counter()`、高分解能・単調、秒・float)で測る。
epoch との対応は持たない(壁時計への換算は FrameLogWriter などが起動時のオフセットで行う)。
"""

from __future__ import annotations

import time


def now() -> float:
    return time.perf_counter()
