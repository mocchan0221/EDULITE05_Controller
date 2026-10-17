"""画面とコアの境界 `Bridge`。**この形を Phase 3 でも維持する**(phase0.md 0-5)。

パネルは `Bridge` の signal とメソッド *だけ* を使い、コア層・モックを直接触らない。
モックは `DemoBridge`(demo_bridge.py)、Phase 3 で `CoreBridge` に差し替える。

注意: QObject と ABCMeta のメタクラスが衝突するため ABC は使わず、未実装メソッドは
`NotImplementedError` にしている。

phase0.md の仕様に対する追加(Phase 3 の CoreBridge でも実装する):
- 現在値の問い合わせ(`connection_info` / `motors` / `selected_motor` / `power_tier` / `states`):
  パネルが signal を取り逃した場合の初期化用
- `backend_kind`(ステータスバーの「モック / シミュレータ / 実機」表示用)
- `param_definitions`(パラメータ表の静的定義。Phase 1 でプロファイル TOML から供給)
- `clear_fault` / `clear_scope` / `run_tool`(故障クリア・グラフクリア・ツール操作)
- `start_controller` の keyword-only 引数 `continuous` / `period_ms`

UI v2(ui_design.md §3・§11)での変更:
- `MotorViewState.ui_state`(offline/disabled/enabled/running/fault)で UI 状態を渡す
- `enable(ids, run_mode)` は有効化時の run_mode を受け取る。`disable(ids)` が旧 `stop`
- `start_controller` は有効(enabled)なモータからのみ。`stop_controller` は「運転停止」
  (速度・電流は 0 へ、位置・MIT は現在位置保持へ移行し、有効のまま `enabled` に戻る)
- 非常停止ラッチ: `estop()` で全台を無効化してラッチ + 出力段階を LOW へ。`estop_latched()` /
  `estop_changed(bool)` / `release_estop()`。解除しても全モータは `disabled` のまま
- `fault_details(motor_id)`(故障の名称・説明・測定値・閾値・原因・対処・件数・発生時刻)
- 通信断: `ConnectionInfo.lost`。`reconnect()` で直前の設定のまま再接続

Phase 3 での追加:
- 記録: `start_recording(path, channels)` / `stop_recording()` / `recording_rows()`。運転ページの
  ⏺ 記録はこれを呼ぶ(CoreBridge は TelemetryHub の Recorder で全レート、DemoBridge は状態更新ごと)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal

from edulite05.app.motor_colors import MotorColorRegistry
from edulite05.app.viewmodels import (
    ChannelInfo,
    ConnectionInfo,
    FaultDetail,
    MotorInfo,
    MotorViewState,
    ParamDef,
    PowerLimits,
)


class Bridge(QObject):
    """GUI ⇔ コアの抽象インターフェース。"""

    connection_changed = Signal(object)  # ConnectionInfo
    motors_changed = Signal(list)  # list[MotorInfo]
    # dict[int, MotorViewState] … 約 60 Hz。Signal(dict) は QVariantMap 変換(キーが文字列限定)で
    # 失敗するため Signal(object) にしている
    state_updated = Signal(object)
    power_tier_changed = Signal(str)  # "low" | "high"
    selected_motor_changed = Signal(int)
    log_message = Signal(object)  # LogEntry
    frame_logged = Signal(object)  # FrameEntry
    params_updated = Signal(int, list)  # motor_id, list[ParamValue]
    stats_updated = Signal(object)  # BusStats
    estop_changed = Signal(bool)  # 非常停止ラッチの変化(True = ラッチ中)
    # `run_tool` の完了通知: (ツール名, motor_id, 成功か, 結果の文言またはエラー文)。
    # 運転ページの「原点に設定」が結果をトーストにするために使う
    tool_finished = Signal(str, int, bool, str)

    # ---- モータの色(初登録順で固定。ui_design.md §7.2) ----
    @property
    def colors(self) -> MotorColorRegistry:
        reg = self.__dict__.get("_motor_colors")
        if reg is None:
            reg = self.__dict__["_motor_colors"] = MotorColorRegistry()
        return reg

    def motor_color_index(self, motor_id: int) -> int:
        """モータ色の枠(`tokens().motor_color(index)` へ渡す)。グラフ・チップ・3D・診断で共通。"""
        return self.colors.slot(motor_id)

    def clamp_note(self, motor_id: int) -> str:
        """SafetyGuard が指令を制限している間はその内容(無ければ空)。グラフの状態帯が参照する。"""
        return ""

    # ---- 現在値の問い合わせ ----
    @property
    def backend_kind(self) -> str:
        """"mock" / "simulator" / "hardware"。"""
        raise NotImplementedError

    def connection_info(self) -> ConnectionInfo:
        raise NotImplementedError

    def motors(self) -> list[MotorInfo]:
        raise NotImplementedError

    def selected_motor(self) -> int:
        raise NotImplementedError

    def power_tier(self) -> str:
        raise NotImplementedError

    def states(self) -> dict[int, MotorViewState]:
        raise NotImplementedError

    def available_channels(self) -> list[ChannelInfo]:
        raise NotImplementedError

    def scope_data(
        self, names: list[str], window_s: float
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """直近 `window_s` 秒の (時刻[s], 値)。時刻は「現在」を 0 とした相対秒(≤ 0)。"""
        raise NotImplementedError

    def power_limits(self) -> PowerLimits:
        raise NotImplementedError

    def estop_latched(self) -> bool:
        """非常停止がラッチ中か。ラッチ中は有効化・運転開始・HIGH への切替を受け付けない。"""
        raise NotImplementedError

    def fault_details(self, motor_id: int) -> list[FaultDetail]:
        """発生中の故障の詳細(無ければ空)。"""
        raise NotImplementedError

    def param_definitions(self) -> list[ParamDef]:
        raise NotImplementedError

    # ---- 操作 ----
    def connect_adapter(self, cfg: dict[str, Any]) -> None:
        raise NotImplementedError

    def disconnect_adapter(self) -> None:
        raise NotImplementedError

    def scan(self) -> None:
        raise NotImplementedError

    def select_motor(self, motor_id: int) -> None:
        raise NotImplementedError

    def reconnect(self) -> None:
        """直前の接続設定のまま再接続する(通信断バナーの [再接続])。"""
        raise NotImplementedError

    def enable(self, ids: list[int], run_mode: int) -> None:
        """モータの出力を ON(type 3)にして `run_mode`(0x7005)を設定する。有効化 = 出力 ON。"""
        raise NotImplementedError

    def disable(self, ids: list[int]) -> None:
        """モータの出力を OFF(type 4)。実行中のコントローラも終了する(旧 `stop`)。"""
        raise NotImplementedError

    def estop(self) -> None:
        """非常停止(常に全台)。全モータを即時無効化し、ラッチして出力段階を LOW に戻す。"""
        raise NotImplementedError

    def release_estop(self) -> None:
        """非常停止のラッチを解除する。全モータは `disabled` のまま(自動再開しない)。"""
        raise NotImplementedError

    def clear_fault(self, ids: list[int]) -> None:
        raise NotImplementedError

    def set_power_tier(self, tier: str) -> bool:
        """段階を変更する。HIGH へは全モータ停止中のみ可(False を返して拒否)。"""
        raise NotImplementedError

    def start_controller(
        self,
        name: str,
        assign: dict[str, int],
        params: object,
        *,
        continuous: bool = True,
        period_ms: int = 5,
    ) -> None:
        """運転開始(指令の送出開始)。有効(enabled)なモータからのみ受け付ける。

        `assign` はスロット名 → モータ ID。単一モータは {"motor": id}、2台連動(bilateral)は
        {"leader": id, "follower": id}(コントローラの `slots` の順)。
        """
        raise NotImplementedError

    def update_controller_params(self, name: str, assign: dict[str, int], params: object) -> None:
        raise NotImplementedError

    def stop_controller(self, name: str, assign: dict[str, int]) -> None:
        """運転停止(非常停止ではない): 速度・電流は 0 へ、位置・MIT は現在位置保持へ移行し、
        有効のまま `enabled` に戻る。"""
        raise NotImplementedError

    def read_all_params(self, motor_id: int) -> None:
        raise NotImplementedError

    def write_param(self, motor_id: int, index: int, value: float) -> None:
        raise NotImplementedError

    def save_params(self, motor_id: int) -> None:
        raise NotImplementedError

    def clear_scope(self) -> None:
        raise NotImplementedError

    # ---- 記録(CSV) ----
    def start_recording(self, path: Path | str, channels: list[str] | None = None) -> None:
        """CSV 記録を開始する。`channels` は `available_channels()` の名前(None なら全チャネル)。"""
        raise NotImplementedError

    def stop_recording(self) -> Path | None:
        """記録を止めて閉じる。保存したパス(書いた行が無ければ None。ファイルも残さない)。"""
        raise NotImplementedError

    def recording_rows(self) -> int:
        """記録中に書いた行数(記録中でなければ 0)。"""
        raise NotImplementedError

    def run_tool(self, tool: str, motor_id: int, args: dict[str, Any]) -> None:
        """ツール操作(CAN ID 変更・ゼロ点設定・主動上報・タイムアウト・ボーレート・プロトコル)。
        完了すると `tool_finished` が発行される。"""
        raise NotImplementedError

    def inject_fault(self, motor_id: int, fault_key: str) -> None:
        """(シミュレータ/モックのみ)故障を注入する。既定は何もしない。"""
        return None

    def inject_comm_loss(self) -> None:
        """(シミュレータ/モックのみ)通信断を再現する。既定は何もしない。"""
        return None
