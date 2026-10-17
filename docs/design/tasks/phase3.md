# Phase 3 タスク指示書 — GUI を実物のコアにつなぐ

> 言語: ユーザーへの返答・報告は日本語で行う(CLAUDE.md「言語」)。サブエージェント間のやりとりは英語でもよい。

作成: Opus / 2026-10-02。実装担当: Sonnet(I と J を並列)。
前提: `CLAUDE.md`、`docs/design/ui_design.md`(画面仕様)、`app/bridge.py`(**画面とコアの境界。形は変えない**)、Phase 2 の `core/control/service.py`(MotorService)、`docs/research/hw_bench.md`(実機の制約: 送信間隔 ≥ 1 ms、主動上報中の応答欠落、上報周期 10+5N ms)。

ゴール: `run_sim.bat` は**本物のコア + シミュレータ**で、`run.bat` は**公式 USB-CAN の実機**で、v2 画面の全機能が動く。DemoBridge は `--demo` で残す(UI 開発用)。

## 1. 担当 I — 保守操作をコアに追加(`core/device/motor.py`、`core/control/service.py`、`sim/`)

MotorService に以下を追加(すべて `Future` を返す。**対象モータが DISABLED、E-STOP ラッチなし**が前提。違えば `ProtocolError`/`SafetyError`):

| メソッド | 内容(el05_protocol.md) |
|---|---|
| `set_zero(id)` | type 6(§2.7)。実行後に位置が 0 付近になることを確認 |
| `set_can_id(id, new_id) -> int` | type 7(§2.8)。応答(type 0、新 ID から)を確認し、Session/Service の登録を新 ID に付け替え |
| `set_report_period(id, period_ms)` | EPScan_time = (period_ms − 10) / 5(10 ≤ period、5 ms 刻み)。実機の式 10+5N |
| `set_can_timeout(id, seconds)` | canTimeout = round(seconds / 50e-6)、0 = 無効 |
| `set_baud(id, code)` | type 23(§2.13)。**再投入が必要**。ホスト側は変更しない |
| `switch_protocol(id, protocol)` | type 25(§2.15)。**再投入が必要** |
| `restore_protocol(target_id, source: "mit" \| "canopen")` | **モータ未選択でも可**(スキャンで見つからない前提)。MIT からは標準フレーム `FF FF FF FF FF FF 00 FD`(§4.11)、CANopen からは拡張 ID 0xFFF の切替フレーム(§6.4)。AT の標準フレーム表現は未検証(Q-T1)なので、`caps.standard_frames` を確認し、docstring と結果メッセージに「未検証」を明記 |
| `write_param(id, key, value)` / `read_all_params(id)` / `save_params(id)` | 運転系パラメータ(run_mode, iq_ref, spd_ref, loc_ref, limit_*)は拒否(運転 API 経由のみ)。save は type 22 |

シミュレータは各操作に el05_protocol.md どおり応答(ID 変更で以降の ID が変わる、プロトコル切替は「再投入」相当の `SimMotor.power_cycle()` で反映、など)。テストは `tests/integration/test_service_maintenance_sim.py`。

## 2. 担当 J — CoreBridge(`src/edulite05/app/core_bridge.py`)と起動の切替

`Bridge` を MotorService の上に実装する。

- **スレッド**: コアのイベント(コアのスレッドから来る)は、スレッド安全なキューに積み、Qt のメインスレッドの `QTimer`(16 ms)で取り出して signal を発行する。`state_updated` もこのタイマーで約 60 Hz。GUI スレッドはコアの Future を**待たない**(`add_done_callback` → キュー経由で結果をログ/トースト/状態に反映)
- **接続**: `connect_adapter(cfg)`: `{"adapter": "robstride_at", "port": "COM7"}` / `{"adapter": "sim", "n": 2}` → registry.create → Session → MotorService.open → 自動スキャン → `motors_changed`。`disconnect_adapter` は MotorService.close(全停止)。通信断(service.comm_lost / TransportError)は `ConnectionInfo.lost=True`。`reconnect()`
- **状態**: `MotorViewState` を MotorService.ui_state / MotorState / ClampNote から組み立てる(`ui_state` の値は ui_design.md §3 の 5 種)。`fault_details` は core/protocol/faults の説明 + 発生時刻・件数
- **制御**: `start_controller(name, assign, params)` は UI のコントローラ名 → core の `CONTROLLERS` へ対応付け(UI の `pages/operate/modes.py` の run_mode 表ではなく `controller.run_mode` を正とする。G の報告: torque 目標の試験信号は run_mode 0)。`enable(ids, run_mode)` / `disable` / `stop_controller` / `update_controller_params` / `estop` / `release_estop` / `set_power_tier` は MotorService へ
- **テレメトリ**: `available_channels` / `scope_data` は TelemetryHub(相対時刻 = t − clock.now())。CSV 記録は運転ページの recorder を TelemetryHub の `Recorder`(全レート)に切り替える(Bridge に `start_recording(path, channels)` / `stop_recording()` を追加してよい — 追加したら DemoBridge にも実装)
- **パラメータ**: `param_definitions` はプロファイル(`profiles/el05.toml`)から。`demo_params.py` は DemoBridge 専用に残す。`read_all_params` / `write_param` / `save_params` は I の MotorService API へ(I が未完成の間は Motor を直接呼ぶ暫定実装でよいが、最終的に MotorService 経由)
- **ログ・フレーム**: BusManager.subscribe で TX/RX フレームを受け、`frame_logged` は**最大 200 件/秒に間引き**(落とした件数を表示)。イベント(service.*、motor.retry、control.clamped …)を日本語ログに
- **統計**: `stats_updated` に BusStats + LoopStats(送受信 fps、ループ周期・ジッタ、再送数、パースエラー、タイムアウト)
- **ツール**: `run_tool(tool, motor_id, args)` → I の MotorService 保守 API
- **起動**: `app/main.py`: `--sim [N]` → CoreBridge + SimTransport(既定 2 台、ID 127 と 1)、`--demo` → DemoBridge、指定なし → CoreBridge(未接続で起動し、接続フォームで COM ポートを選ぶ。CH340 を既定選択)。`run_sim.bat` は `--sim`
- **テスト**: `tests/gui/test_core_bridge_sim.py`(pytest-qt + Sim): 接続 → スキャン → 有効化 → 各モード運転 → 運転停止 → 無効化 → E-STOP → 解除、パラメータ読書、故障注入 → 表示 → クリア、通信断。既存 GUI テストは DemoBridge のまま通ること

## 3. 受入と確認(Opus)
- 全テスト(GUI 含む)パス、ruff クリーン
- `run_sim.bat` で全画面を操作したスクリーンショットを `docs/design/ui_mock_v2/real/` に(シミュレータ・本物のコア)
- 実機(Opus): GUI で COM7 に接続 → スキャン → 状態表示・パラメータ読出(読み出しのみ)→ ユーザー許可範囲の低速 JOG/速度を GUI から 1 回
