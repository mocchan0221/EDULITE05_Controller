"""3D ビュー(モニタの `[グラフ | 3D]` の 3D 側)。

- 起動直後と PyVista が使えない環境では 2D ダイヤル(QPainter)を表示する。PyVista の import は
  共有フォルダ上で 40〜60 秒かかるため、シェルが別スレッドで先読みし、終わったら `ensure_3d()` を
  呼ぶ(失敗したら `set_preload_failed(reason)`)。
- 3D: rotor を出力角で回転、目標角のゴースト(半透明)、トルク矢印、モータ色のリング(識別用)。
  複数台は横並び。モデルは `app/model3d.py`(GLB。無ければ円柱で代用)。
- 色はすべてトークンから取る(モデルの部品色だけは `model3d.PART_COLORS`)。
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QStackedLayout, QVBoxLayout, QWidget

from edulite05.app import model3d
from edulite05.app.bridge import Bridge
from edulite05.app.i18n import tr
from edulite05.app.motor_colors import fallback_index
from edulite05.app.ui.button import Button
from edulite05.app.ui.label import Label
from edulite05.app.ui.style import make_font, theme_manager, tokens
from edulite05.app.ui.tokens import FS_BODY, FS_CAPTION, SP_PAD, SP_RELATED
from edulite05.app.viewmodels import MotorInfo, MotorViewState

MOTOR_SPACING = 0.07  # m
RING_INNER = 0.0235  # ハウジング半径 23 mm のすぐ外側
RING_OUTER = 0.0275
ARROW_RADIUS = 0.030  # 矢印の作用点(出力円盤の縁)
ARROW_MAX_LEN = 0.035
REFRESH_MS = 33


class DialView2D(QWidget):
    """複数モータを横並びにした 2D ダイヤル。出力角・目標角(破線)・トルク弧・モータ色のリング。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 160)
        self._motors: dict[int, tuple[str, float, float | None, float]] = {}
        self.torque_full_scale = 0.5
        self.color_of = fallback_index  # ID → 色の枠(Viewer3D が Bridge の割り当てに差し替える)
        self.sign = model3d.ROTOR_SIGN
        self._message = tr("operate.view3d.no_motor")
        theme_manager().changed.connect(self._on_theme)

    def _on_theme(self, _name: str = "") -> None:
        self.update()

    def set_motor(
        self, motor_id: int, name: str, angle: float, target: float | None, torque: float
    ) -> None:
        self._motors[motor_id] = (name, angle, target, torque)
        self.update()

    def clear(self) -> None:
        self._motors.clear()
        self.update()

    def motor_count(self) -> int:
        return len(self._motors)

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(t.bg_sunken))
        if not self._motors:
            p.setPen(QColor(t.text_tertiary))
            p.setFont(make_font(FS_BODY))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            p.end()
            return
        n = len(self._motors)
        cell_w = self.width() / n
        for i, (mid, (name, angle, target, torque)) in enumerate(sorted(self._motors.items())):
            cx = cell_w * (i + 0.5)
            r = max(30.0, min(cell_w * 0.40, self.height() * 0.34))
            cy = self.height() * 0.46
            self._draw_dial(p, QPointF(cx, cy), r, mid, name, angle, target, torque)
        p.end()

    def _draw_dial(
        self, p: QPainter, c: QPointF, r: float, mid: int, name: str, angle: float,
        target: float | None, torque: float,
    ) -> None:
        t = tokens()
        # モータ色のリング(3D と同じ識別)
        p.setPen(QPen(QColor(t.motor_color(self.color_of(mid))), max(3.0, r * 0.07)))
        p.setBrush(QColor(t.bg_card))
        p.drawEllipse(c, r, r)
        # 目盛(30° ごと)
        p.setPen(QPen(QColor(t.text_tertiary), 1))
        for k in range(12):
            a = math.radians(k * 30)
            inner = r * (0.84 if k % 3 == 0 else 0.90)
            p.drawLine(
                QPointF(c.x() + inner * math.sin(a), c.y() - inner * math.cos(a)),
                QPointF(c.x() + r * 0.95 * math.sin(a), c.y() - r * 0.95 * math.cos(a)),
            )
        # 出力ディスク
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(model3d.PART_COLORS["output_shaft"]))
        p.drawEllipse(c, r * 0.60, r * 0.60)
        # トルク弧(12 時から時計回りが正)
        frac = max(-1.0, min(1.0, torque / self.torque_full_scale))
        if abs(frac) > 0.005:
            pen = QPen(QColor(t.accent), max(3.0, r * 0.06))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(c.x() - r * 0.72, c.y() - r * 0.72, r * 1.44, r * 1.44)
            p.drawArc(rect, 90 * 16, int(-frac * 120 * 16 * self.sign))
        if target is not None:  # 目標(破線)
            self._needle(p, c, r, target, QColor(t.accent), dashed=True, width=2.0)
        self._needle(p, c, r, angle, QColor(t.text_primary), dashed=False, width=3.0)
        p.setBrush(QColor(t.bg_base))
        p.setPen(QPen(QColor(t.stroke_control), 1))
        p.drawEllipse(c, r * 0.07, r * 0.07)
        # 名前と角度
        p.setFont(make_font(FS_BODY, semibold=True))
        p.setPen(QColor(t.text_primary))
        p.drawText(
            QRectF(c.x() - r, c.y() + r * 1.12, r * 2, 20), Qt.AlignmentFlag.AlignCenter,
            tr("ui.motor_id", id=mid),
        )
        p.setFont(make_font(FS_CAPTION, mono=True))
        p.setPen(QColor(t.text_secondary))
        p.drawText(
            QRectF(c.x() - r * 1.4, c.y() + r * 1.12 + 20, r * 2.8, 18),
            Qt.AlignmentFlag.AlignCenter,
            tr("operate.view3d.dial_angle", rad=f"{angle:+.3f}", deg=f"{math.degrees(angle):+.1f}"),
        )

    def _needle(
        self, p: QPainter, c: QPointF, r: float, angle: float, col: QColor, *, dashed: bool,
        width: float,
    ) -> None:
        a = angle * self.sign
        tip = QPointF(c.x() + r * 0.92 * math.sin(a), c.y() - r * 0.92 * math.cos(a))
        pen = QPen(col, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(c, tip)


class _IdTag(QWidget):
    """3D の下の「■ID1」凡例(モータ色の四角 + 文字)。"""

    def __init__(
        self, motor_id: int, color_index: int | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.motor_id = motor_id
        self._color_index = fallback_index(motor_id) if color_index is None else color_index
        self._text = tr("ui.motor_id", id=motor_id)
        fm = QFontMetrics(make_font(FS_CAPTION, semibold=True))
        self.setFixedSize(SP_RELATED + 8 + SP_RELATED + fm.horizontalAdvance(self._text) + 8, 24)

    def paintEvent(self, _e: object) -> None:  # noqa: N802
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.motor_color(self._color_index)))
        p.drawRoundedRect(QRectF(SP_RELATED, self.height() / 2 - 4, 8, 8), 2, 2)
        p.setFont(make_font(FS_CAPTION, semibold=True))
        p.setPen(QColor(t.text_primary))
        p.drawText(
            QRectF(SP_RELATED + 8 + SP_RELATED, 0, self.width(), self.height()),
            Qt.AlignmentFlag.AlignVCenter,
            self._text,
        )
        p.end()


class Viewer3D(QWidget):
    """`Viewer3D(bridge)`。状態: idle / loading / ready / failed / disabled。"""

    def __init__(self, bridge: Bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._state = "idle"
        self._reason = ""
        self._enabled3d = True
        self._states: dict[int, MotorViewState] = {}
        self._names: dict[int, str] = {}
        self._plotter: Any = None
        self._pv: Any = None
        self._model: model3d.Model | None = None
        self._scene: dict[int, dict[str, Any]] = {}
        self._last_drawn: dict[int, tuple[float, float | None, float]] = {}
        self._needs_render = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.stack_host = QWidget()
        self.stack = QStackedLayout(self.stack_host)
        self.dial = DialView2D()
        self.dial.color_of = bridge.motor_color_index
        self.stack.addWidget(self.dial)
        self.pv_host = QWidget()
        self.pv_layout = QVBoxLayout(self.pv_host)
        self.pv_layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.pv_host)
        lay.addWidget(self.stack_host, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(SP_PAD, SP_RELATED, SP_PAD, SP_RELATED)
        bar.setSpacing(SP_PAD)
        self.ghost_check = QCheckBox(tr("operate.view3d.ghost"))
        self.ghost_check.setChecked(True)
        self.arrow_check = QCheckBox(tr("operate.view3d.arrow"))
        self.arrow_check.setChecked(True)
        self.reset_btn = Button(tr("operate.view3d.reset_view"), "subtle", icon="rotate-ccw")
        self.id_tags = QHBoxLayout()
        self.id_tags.setSpacing(0)
        # 狭い幅でも最小幅を押し上げないよう、折り返して右寄せにする
        self.backend_label = Label("", "caption", wrap=True)
        self.backend_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.ghost_check)
        bar.addWidget(self.arrow_check)
        bar.addWidget(self.reset_btn)
        bar.addLayout(self.id_tags)
        bar.addStretch(1)
        bar.addWidget(self.backend_label)
        lay.addLayout(bar)

        self.reset_btn.clicked.connect(self.reset_view)
        self.ghost_check.toggled.connect(lambda _on: self._force_update())
        self.arrow_check.toggled.connect(lambda _on: self._force_update())
        bridge.state_updated.connect(self._on_states)
        bridge.motors_changed.connect(self._on_motors)
        theme_manager().changed.connect(self._on_theme)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._on_motors(bridge.motors())
        self._update_label()

    # ------------------------------------------------------------ 状態
    @property
    def mode(self) -> str:
        """"3d" または "dial"。"""
        return "3d" if self._state == "ready" else "dial"

    @property
    def state(self) -> str:
        return self._state

    @property
    def failure_reason(self) -> str:
        return self._reason

    def set_3d_enabled(self, enabled: bool) -> None:
        """False なら 3D を使わない(`--no-3d`)。説明文を「読み込み中」にしない。"""
        self._enabled3d = enabled
        if not enabled and self._state in ("idle", "loading"):
            self._state = "disabled"
        elif enabled and self._state == "disabled":
            self._state = "idle"
        self._update_label()

    def _update_label(self) -> None:
        if self._state == "ready":
            src = self._model.source if self._model else ""
            text = tr("operate.view3d.backend_3d", source=src)
        elif self._state == "failed":
            text = tr("operate.view3d.backend_failed", reason=self._reason)
        elif self._state == "disabled":
            text = tr("operate.view3d.disabled")
        elif self._state == "loading" or self._enabled3d:
            text = tr("operate.view3d.loading")
        else:
            text = tr("operate.view3d.backend_dial")
        self.backend_label.setText(text)
        is_3d = self._state == "ready"
        self.arrow_check.setEnabled(is_3d)
        self.reset_btn.setEnabled(is_3d)
        for i in range(self.id_tags.count()):
            w = self.id_tags.itemAt(i).widget()
            if w is not None:
                w.setVisible(is_3d)

    # ------------------------------------------------------------ 3D 初期化
    def ensure_3d(self) -> None:
        """PyVista の import・シーン構築(メインスレッドで呼ぶ)。失敗時は 2D のまま。"""
        if self._state not in ("idle", "failed") or not self._enabled3d:
            return
        self._state = "loading"
        self._update_label()
        try:
            import pyvista as pv
            from pyvistaqt import QtInteractor

            self._pv = pv
            self._model = model3d.load_model(pv)
            self._plotter = QtInteractor(self.pv_host, auto_update=False)
            self.pv_layout.addWidget(self._plotter.interactor)
            self._build_scene()
            self._apply_background()
            self.reset_view()
            self.stack.setCurrentIndex(1)
            self._state = "ready"
        except Exception as exc:  # noqa: BLE001  (import 失敗・GL 初期化失敗など全て 2D へ)
            self._state = "failed"
            self._reason = f"{type(exc).__name__}: {exc}"[:160]
            self._plotter = None
            self.stack.setCurrentIndex(0)
        self._update_label()

    def set_preload_failed(self, reason: str) -> None:
        self._state = "failed"
        self._reason = reason[:160]
        self._update_label()

    def _apply_background(self) -> None:
        if self._plotter is None:
            return
        t = tokens()
        self._plotter.set_background(t.bg_sunken, top=t.bg_card)

    def _on_theme(self, _name: str = "") -> None:
        self._apply_background()
        self._needs_render = True
        self.update()

    def _build_scene(self) -> None:
        """モータごとに stator / rotor / ゴースト / トルク矢印 / モータ色のリングを作る。"""
        pv, plotter, model = self._pv, self._plotter, self._model
        if plotter is None or model is None:
            return
        plotter.clear()
        self._scene.clear()
        self._last_drawn.clear()
        t = tokens()
        ids = sorted(self._names)
        for i, mid in enumerate(ids):
            x = -i * MOTOR_SPACING
            stator = [
                plotter.add_mesh(p.mesh, color=p.color, smooth_shading=True, specular=0.3)
                for p in model.stator
            ]
            rotor = [
                plotter.add_mesh(p.mesh, color=p.color, smooth_shading=True, specular=0.4)
                for p in model.rotor
            ]
            ghost = [
                plotter.add_mesh(p.mesh, color=t.accent, opacity=0.35, smooth_shading=True)
                for p in model.rotor
            ]
            ring_mesh = pv.Disc(
                center=(0.0, 0.0, -0.0003), inner=RING_INNER, outer=RING_OUTER,
                normal=(0, 0, 1), r_res=1, c_res=96,
            )
            ring_color = t.motor_color(self._bridge.motor_color_index(mid))
            ring = plotter.add_mesh(ring_mesh, color=ring_color)
            arrow_mesh = pv.Arrow(start=(0, 0, 0), direction=(0, 1, 0), scale=0.01)
            arrow = plotter.add_mesh(arrow_mesh, color=t.text_primary, smooth_shading=True)
            for a in (*stator, *rotor, *ghost, ring):
                a.position = (x, 0.0, 0.0)
            arrow.position = (x + ARROW_RADIUS, 0.0, -0.006)
            for a in ghost:
                a.visibility = False
            arrow.visibility = False
            self._scene[mid] = {
                "x": x, "rotor": rotor, "ghost": ghost, "arrow": arrow,
                "arrow_mesh": arrow_mesh, "ring": ring,
            }
        plotter.add_axes(line_width=2, labels_off=False)

    def reset_view(self) -> None:
        """出力面(-Z 側)をやや斜めから見る。"""
        if self._plotter is None:
            return
        n = max(1, len(self._scene))
        cx = -(n - 1) * MOTOR_SPACING / 2
        dist = 0.115 + 0.05 * (n - 1)
        focal = (cx, 0.0, 0.012)
        pos = (cx + 0.45 * dist, 0.55 * dist, -dist)
        self._plotter.camera_position = [pos, focal, (0.0, 1.0, 0.0)]
        self._plotter.render()

    # ------------------------------------------------------------ 更新
    def _on_motors(self, motors: list[MotorInfo]) -> None:
        self._names = {m.id: m.name for m in motors}
        self.dial.clear()
        for mid in list(self._states):
            if mid not in self._names:
                del self._states[mid]
        while self.id_tags.count():
            item = self.id_tags.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for mid in sorted(self._names):
            self.id_tags.addWidget(_IdTag(mid, self._bridge.motor_color_index(mid)))
        if self._state == "ready":
            self._build_scene()
            self.reset_view()
        self._update_label()

    def _on_states(self, states: dict[int, MotorViewState]) -> None:
        self._states = states

    def _force_update(self) -> None:
        self._last_drawn.clear()
        self._needs_render = True

    def _tick(self) -> None:
        if not self.isVisible():
            return
        self.dial.torque_full_scale = max(self._bridge.power_limits().torque, 0.1)
        for mid, s in self._states.items():
            self.dial.set_motor(
                mid, self._names.get(mid, str(mid)), s.position_multi,
                s.cmd_position if self.ghost_check.isChecked() else None, s.torque,
            )
        if self._state != "ready" or self._plotter is None:
            return
        changed = self._needs_render
        for mid, s in self._states.items():
            sc = self._scene.get(mid)
            if sc is None:
                continue
            key = (round(s.position_multi, 4), s.cmd_position, round(s.torque, 2))
            if self._last_drawn.get(mid) == key:
                continue
            self._last_drawn[mid] = key
            changed = True
            self._apply_motor(sc, s)
        if changed:
            self._needs_render = False
            self._plotter.render()

    def _apply_motor(self, sc: dict[str, Any], s: MotorViewState) -> None:
        deg = math.degrees(s.position_multi) * model3d.ROTOR_SIGN
        for a in sc["rotor"]:
            a.orientation = (0.0, 0.0, deg)
        show_ghost = self.ghost_check.isChecked() and s.cmd_position is not None
        gdeg = math.degrees(s.cmd_position or 0.0) * model3d.ROTOR_SIGN
        for a in sc["ghost"]:
            a.visibility = show_ghost
            if show_ghost:
                a.orientation = (0.0, 0.0, gdeg)
        arrow = sc["arrow"]
        # 矢印の長さは現在の出力段階のトルク上限を満点とする(低出力でも見える大きさ)
        full = max(self._bridge.power_limits().torque, 0.5)
        length = min(abs(s.torque) / full, 1.0) * ARROW_MAX_LEN
        visible = self.arrow_check.isChecked() and length > 0.0015
        arrow.visibility = visible
        if visible:
            q = round(length / 0.002) * 0.002  # メッシュ再生成は 2 mm 刻みに間引く
            direction = math.copysign(1.0, s.torque) * model3d.ROTOR_SIGN
            key = (q, direction)
            if sc.get("arrow_key") != key:
                sc["arrow_key"] = key
                new = self._pv.Arrow(
                    start=(0, 0, 0), direction=(0, direction, 0), scale=max(q, 0.002),
                    tip_length=0.35, tip_radius=0.12, shaft_radius=0.04,
                )
                sc["arrow_mesh"].copy_from(new)

    # ------------------------------------------------------------ スクリーンショット用
    def grab_3d_image(self) -> Any:
        """3D ビューの描画結果(numpy RGB 配列)。VTK のウィジェットは QWidget.grab() に写らない。"""
        if self._plotter is None:
            return None
        self._plotter.render()
        return self._plotter.screenshot(return_img=True)

    def shutdown(self) -> None:
        self._timer.stop()
        if self._plotter is not None:
            try:
                self._plotter.close()
            except Exception:  # noqa: BLE001
                pass
            self._plotter = None
            self._state = "failed"
