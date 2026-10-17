"""Phase 0-2 スパイク(使い捨て): PySide6 6.11 + pyqtgraph 0.14 + pyvistaqt の動作・性能確認。

- pyqtgraph: 8ch x 1 kHz の sin を 60 fps で描画(表示窓 10 s = 10,000 点/ch、バッファ 120 s)
- pyvistaqt: 円柱を回転

実行: uv run --frozen python tools/spikes/stack_check.py [--seconds 6] [--no-3d] [--no-plot]
結果を標準出力に出す。
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

FS = 1000.0  # サンプルレート [Hz]
NCH = 8
BUF_S = 120
WINDOW_S = 10


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--no-3d", action="store_true")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--ds", default="peak", help="downsample method: peak|subsample|mean|none")
    ap.add_argument("--window", type=float, default=WINDOW_S)
    ap.add_argument("--opengl", action="store_true")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--antialias", action="store_true")
    args = ap.parse_args()

    t0 = time.perf_counter()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    app = QApplication(sys.argv)
    print(f"[import PySide6] {time.perf_counter() - t0:.1f}s")

    win = QWidget()
    win.setWindowTitle("stack_check spike")
    win.resize(1400, 700)
    lay = QHBoxLayout(win)
    results: dict[str, float] = {}

    # ---- pyqtgraph ----
    plot_tick = None
    if not args.no_plot:
        t1 = time.perf_counter()
        import pyqtgraph as pg

        pg.setConfigOptions(antialias=args.antialias, useOpenGL=args.opengl)
        print(f"[import pyqtgraph {pg.__version__}] {time.perf_counter() - t1:.1f}s")
        gw = pg.GraphicsLayoutWidget()
        lay.addWidget(gw, 3)
        n = int(BUF_S * FS)
        buf = np.zeros((NCH, n), dtype=np.float32)
        tbuf = (np.arange(n, dtype=np.float64) / FS)
        write = {"i": 0, "t": 0.0}
        curves = []
        plots = []
        for ch in range(NCH):
            p = gw.addPlot(row=ch, col=0)
            p.setMouseEnabled(x=False, y=True)
            p.showGrid(x=args.grid, y=args.grid, alpha=0.2)
            c = p.plot(pen=pg.mkPen(pg.intColor(ch, NCH), width=1))
            if args.ds != "none":
                c.setClipToView(True)
                c.setDownsampling(auto=True, method=args.ds)
            c.setSkipFiniteCheck(True)
            curves.append(c)
            plots.append(p)
            if ch > 0:
                p.setXLink(plots[0])
        # 全バッファは 120 s 分の循環配列。表示は直近 10 s を切り出し
        ring_t = np.zeros(n)
        ring = np.zeros((NCH, n), dtype=np.float32)
        count = {"n": 0, "frames": 0, "t_last": time.perf_counter(), "samples": 0}
        freqs = [1 + ch for ch in range(NCH)]
        t_sim = {"t": 0.0, "wall0": time.perf_counter()}
        frame_times: list[float] = []

        def plot_tick_fn() -> None:
            tf = time.perf_counter()
            # 実時間に追随して 1 kHz 分のサンプルを生成
            target = int((tf - t_sim["wall0"]) * FS)
            k = target - count["samples"]
            if k > 0:
                ts = (count["samples"] + np.arange(k)) / FS
                idx = (count["samples"] + np.arange(k)) % n
                ring_t[idx] = ts
                for ch in range(NCH):
                    ring[ch, idx] = np.sin(2 * np.pi * freqs[ch] * ts)
                count["samples"] = target
            total = count["samples"]
            m = min(total, int(args.window * FS))
            if m > 1:
                idx = (total - m + np.arange(m)) % n
                x = ring_t[idx]
                for ch in range(NCH):
                    curves[ch].setData(x, ring[ch, idx])
                plots[0].setXRange(x[-1] - args.window, x[-1], padding=0)
            count["frames"] += 1
            frame_times.append(time.perf_counter() - tf)

        plot_tick = plot_tick_fn
        del buf, tbuf, write, count["n"]
    else:
        frame_times = []
        count = {"frames": 0}

    # ---- pyvistaqt ----
    plotter = None
    actor = None
    render_times: list[float] = []
    if not args.no_3d:
        t2 = time.perf_counter()
        try:
            import pyvista as pv
            from pyvistaqt import QtInteractor

            print(f"[import pyvista {pv.__version__} + pyvistaqt] {time.perf_counter() - t2:.1f}s")
            plotter = QtInteractor(win)
            lay.addWidget(plotter.interactor, 2)
            cyl = pv.Cylinder(radius=0.03, height=0.05, direction=(0, 0, 1), resolution=48)
            actor = plotter.add_mesh(cyl, color="orange", smooth_shading=True)
            plotter.add_axes()
            plotter.reset_camera()
        except Exception as exc:  # noqa: BLE001
            print(f"[3D FAILED] {type(exc).__name__}: {exc}")
            plotter = None

    win.show()
    app.processEvents()

    angle = {"a": 0.0}

    def tick() -> None:
        if plot_tick is not None:
            plot_tick()
        if plotter is not None and actor is not None:
            ts = time.perf_counter()
            angle["a"] += 3.0
            actor.orientation = (0.0, 0.0, angle["a"])
            plotter.render()
            render_times.append(time.perf_counter() - ts)

    timer = QTimer()
    timer.setInterval(16)
    timer.timeout.connect(tick)
    tstart = time.perf_counter()
    timer.start()

    def finish() -> None:
        dur = time.perf_counter() - tstart
        timer.stop()
        nframes = len(frame_times) or len(render_times)
        print(f"[run] {dur:.2f}s, ticks={nframes}, fps={nframes / dur:.1f}")
        if frame_times:
            a = np.array(frame_times) * 1000
            print(f"[pyqtgraph setData] mean={a.mean():.1f}ms p95={np.percentile(a, 95):.1f}ms "
                  f"max={a.max():.1f}ms")
        if render_times:
            a = np.array(render_times) * 1000
            print(f"[vtk render] mean={a.mean():.1f}ms p95={np.percentile(a, 95):.1f}ms "
                  f"max={a.max():.1f}ms")
        results["done"] = 1
        app.quit()

    QTimer.singleShot(int(args.seconds * 1000), finish)
    app.exec()
    if plotter is not None:
        plotter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
