"""公式 USB-CAN の往復遅延(type 17 読出 → 応答)を測る。読み出し専用(モータは動かない)。hw_bench.md HW-5。

使い方: uv run --frozen python tools/rtt_probe.py
"""
import statistics

from edulite05.core import clock
from edulite05.core.protocol import private as P
from edulite05.core.transport.at_serial import AtSerialTransport

MOTOR, HOST = 127, 0xFD
t = AtSerialTransport("COM7")
t.open()
try:
    t.send(P.set_active_report(MOTOR, HOST, False))
    end = clock.now() + 0.3
    while clock.now() < end:
        t.recv(0.01)
    rtts, lost = [], 0
    for _ in range(200):
        t0 = clock.now()
        t.send(P.read_param(MOTOR, HOST, 0x7019))
        got = None
        while clock.now() - t0 < 0.05:
            f = t.recv(0.001)
            if f is not None and (f.can_id >> 24) == 17:
                got = f
                break
        if got is None:
            lost += 1
        else:
            rtts.append((got.timestamp - t0) * 1000)
        w = clock.now() + 0.003
        while clock.now() < w:
            t.recv(0.001)
    rtts.sort()

    def q(p: float) -> float:
        return rtts[min(len(rtts) - 1, int(p * len(rtts)))]

    print(f"n={len(rtts)} lost={lost} min={rtts[0]:.2f} median={statistics.median(rtts):.2f} "
          f"p95={q(0.95):.2f} p99={q(0.99):.2f} max={rtts[-1]:.2f} ms")
finally:
    t.close()
