# Phase 4–5 タスク指示書 — 機能の仕上げと拡張性

> 言語: ユーザーへの返答・報告は日本語で行う(CLAUDE.md「言語」)。サブエージェント間のやりとりは英語でもよい。

作成: Opus / 2026-10-02。実装担当: Sonnet。ユーザー不在でも進められる範囲(実機は読み出しと許可済みの低速確認のみ)。
前提: `CLAUDE.md`、`architecture.md` §4.8(プラグイン)、`ui_design.md` §5.1(グラフ)・§10(拡張点)、Phase 3 の CoreBridge。

## 依存関係
- 今すぐ着手可(Phase 3 とファイルが重ならない): **O**(バイラテラル制御)、**N**(追加トランスポート・リプレイ)
- Phase 3(J)完了後: **K**(グラフ強化)、**L**(プラグイン基盤)→ **M**(スクリプト・シーケンス)、**P**(英語カタログ)、**Q**(利用者マニュアル)

## O — バイラテラル制御(core のみ)
- `core/control/controllers/bilateral.py`: `Bilateral(Controller)`、slots = ("leader", "follower")、run_mode = OPERATION(MIT)
- 対称型(位置対称)バイラテラル: 各モータに MIT 指令 `p_set = 相手の位置(+オフセット)`、`v_set = 相手の速度`、`kp = K`、`kd = D`、`t_ff = 0`。オプションで力順送(相手のトルク推定 × 係数)と、片方向(マスタ・スレーブ)モード
- Params: `stiffness K`(既定 5)、`damping D`(既定 0.2)、`offset`(rad、開始時の位置差を保持するか 0 にそろえるか選択)、`mode`: symmetric / master_slave
- 開始時は位置差を記録し、既定ではその差を保持(急に引き寄せない)。差を 0 に近づける場合は速度制限付きで徐々に
- SafetyGuard の最悪トルク制限がそのまま効くこと(LOW では 0.5 N·m)
- テスト: Sim 2 台で、片方に外力(SimMotor.ext_torque)を与えるともう片方が追従する、オフセット保持、LOW での制限
- UI の「2 台連動」(P1 の簡易追従)は、Phase 3 完了後に K/L の担当が core の Bilateral へ差し替える

## N — 追加トランスポートとリプレイ(core/transport のみ)
- `core/transport/pycan.py`: `PythonCanTransport`(python-can の interface: slcan / gs_usb / candle / pcan / socketcan / virtual)。python-can は**任意依存**(`[project.optional-dependencies] pycan = ["python-can>=4.5"]`、`uv sync --extra pycan`)。import できなければ registry に「python-can 未導入」と表示して選べないようにする。標準フレームも対応
- `core/transport/replay.py`: `ReplayTransport(path, speed=1.0, loop=False)`: framelog の RX を元の時刻間隔で再生(TX は捨てる)。GUI の接続フォームで「ログを再生」を選べるよう registry に登録(options: path, speed)
- テスト: python-can の `virtual` interface で送受信、リプレイの時間間隔と終端
- **実機の COM7 では使わない**

## K — グラフ強化(`pages/operate/graph_view.py` 等。Phase 3 完了後)
- トリガ: チャネル・レベル・立上り/立下り、単発取得(条件成立で前後 N 秒を保持して停止)
- 2 点 Δ カーソル(一時停止中):Δt, Δ値、傾き
- FFT 表示(選択チャネル、窓関数 Hann)
- ステップ応答評価: StepTest 実行中/後に、立上り時間(10–90 %)、オーバーシュート、整定時間(±2 %)を表示
- 状態の時間帯表示: グラフ下に ui_state/モード/クランプ発生の帯
- UI の「2 台連動」を core の Bilateral に接続

## L — プラグイン基盤(Phase 3 完了後)
- `plugin_api/`: `Plugin`(id, name, version, requires_gui, activate(ctx), deactivate())、`PluginContext`(service/session、ui: UiRegistry | None、settings、log)、`UiRegistry`(ui_design.md §10: add_page / add_mode / add_monitor_view / add_maintenance_item / add_settings_section / add_more_action)、core 側の登録口(controllers / transports / telemetry channels / profiles)
- `PluginManager`: ① `edulite05.builtin`(組込みページ・モード — **今の PAGES 一覧と operate のモード一覧をこの API 経由の登録に置き換える**)② プロジェクト直下 `plugins/<name>/plugin.py`(置くだけ)③ entry point `edulite05.plugins`。読込失敗はそのプラグインだけ無効化してログ
- 設定ページの「プラグイン」を有効化: 一覧、有効/無効、読込エラー表示
- 雛形 `plugins/example_triangle/`: 三角波の試験信号 Controller を追加(add_mode)+ 小さな情報ページ(add_page)
- `docs/design/plugin_guide.md`(日本語): 作り方、API、注意(安全: すべての指令は MotorService 経由)

## M — スクリプトコンソールとシーケンス(L の後)
- プラグインとして実装: 「スクリプト」ページ = Python コンソール(`service`, `telemetry`, `controllers` を名前空間に公開、出力表示、履歴)。実行はワーカースレッド、GUI をブロックしない、非常停止は常に有効
- シーケンス: YAML(`sequences/*.yaml`)で手順(enable / run controller with params for N s / wait / record start/stop / disable)を定義 → ページから実行・中断、CLI `run-sequence FILE --yes`(LOW のみ)
- サンプル: `sequences/velocity_steps.yaml`

## P — 英語カタログ(L の後でも前でも可、i18n のみ)
- `app/i18n/en.toml`(ja.toml の全キー)、設定ページの言語切替を有効化(再起動で反映でよい)、キー欠落を検出するテスト

## Q — 利用者マニュアル(最後)
- `docs/manual.md`(日本語): 起動方法、画面の見方、各モードの使い方、安全(出力段階・非常停止)、CLI、トラブルシューティング(hw_bench.md の知見: 取りこぼし、ログはローカルに)
