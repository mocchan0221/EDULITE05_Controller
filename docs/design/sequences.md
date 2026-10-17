# シーケンスとスクリプトコンソール

`phase4_5.md` M の仕様と実装のまとめ。シーケンスは、YAML で書いた手順(`sequences/*.yaml`)を `MotorService` に対して自動で実行する機能である。スクリプトコンソールは、GUI から Python を実行する機能である(プラグイン `plugins/scripting`)。

## 1. 構成

| 場所 | 内容 | Qt |
|---|---|---|
| `src/edulite05/core/sequence/schema.py` | YAML の読込と**実行前の検証**(行番号つきの日本語エラー) | なし |
| `src/edulite05/core/sequence/runner.py` | `SequenceRunner`: ワーカースレッドで実行・中断・後始末 | なし |
| `src/edulite05/core/scripting.py` | コンソールの実行エンジン(ワーカー実行・出力・中断・名前空間) | なし |
| `src/edulite05/cli/sequence_cmd.py` | `python -m edulite05.cli run-sequence` | なし |
| `plugins/scripting/` | 「スクリプト」ページ(プラグイン。設定ページの「プラグイン」で無効にできる) | あり |
| `sequences/velocity_steps.yaml` | サンプル(低速の速度ステップ) | — |

モータへの指令はすべて `MotorService`(→ ControlLoop → SafetyGuard)のメソッド経由。生フレームは送らない。

## 2. YAML の書き方

```yaml
name: 速度ステップ(低速)       # 省略可(既定はファイル名)
description: 説明               # 省略可
motor: 127                      # 省略可(実行時の指定 --id / ページの選択が優先)
steps:
  - enable: velocity                      # 有効化。velocity / current / csp / pp / mit
  - start:                                # 運転開始(待たない)
      controller: velocity                # 1 台用のコントローラ名(運転ページに出るもの。jog は不可)
      params: {velocity: 0.5, accel: 10}  # Params のフィールド名(省略は既定値)
  - wait: 2                               # 秒
  - set_params: {velocity: 1.0}           # 運転中の目標を変える
  - stop                                  # 運転停止(有効のまま保持)
  - run:                                  # start + 秒数待つ + stop
      controller: velocity
      params: {velocity: -1.0}
      seconds: 2
  - record: start                         # 記録の開始。{action: start, path: a.csv} で保存先を指定
  - record: stop
  - log: 区切りのメッセージ
  - disable                               # 無効化
```

- 各手順は「動作: 内容」の 1 キー。内容の要らない `stop` / `disable` は文字列だけでもよい。
- 記録の保存先は `user_data/records/`(相対パスはここからの相対)。`path` 省略時は `seq_<名前>_<日時>.csv`。
- 運転の前に `enable` が必要で、`enable` のモードと `controller` の run_mode が一致している必要がある(例: `enable: velocity` の後に電流コントローラは不可)。試験信号(sine / step)の run_mode は `target` で決まる。

### 検証(実行前にすべて)

構文、未知のキー(「もしかして」つき)、型、`Params` のフィールド名と範囲、手順の順序(enable 前の start、二重の enable、運転中でない set_params、記録の二重開始など)、run_mode の整合、秒数(`run` の `seconds` は 0 より大きく 3600 以下、`wait` は 0 以上 3600 以下)。問題は**全件**を `ファイル名:行 N: 内容` で返し、1 件でもあれば実行しない(モータには触れない)。低出力(LOW)の上限を超える値は**警告**(実行時は SafetyGuard が上限に制限し、ログにも出る)。

## 3. 実行の約束

- **低出力(LOW)のみ**。出力段階が高出力(HIGH)のときは開始しない(`SequenceRunner(allow_high=True)` と明示したときだけ例外。CLI・GUI は渡さない)。
- 開始前の確認(接続・非常停止なし・低出力・モータが見つかり停止中)に通らなければ、**何も送らず**に失敗で終わる(他で動かしているモータを止めない)。
- 開始後は、完了・中断・エラー・非常停止の**どれでも**、記録を止めて `stop_and_disable`(運転停止 → 減速を待つ → 無効化)で終える。失敗したら `disable` を再試行する。非常停止のラッチ中は `estop()` が全モータを無効化済みなので何も送らない。
- 実行中は毎周期(20 ms)、非常停止・通信断・故障・モータ状態の想定外(予期しない無効化など)を見て、異常なら中断して後始末する。
- `abort()` はどのスレッドからでも呼べ、直ちに運転停止を送る。

## 4. CLI

```
python -m edulite05.cli run-sequence sequences/velocity_steps.yaml --sim --id 127 --yes
python -m edulite05.cli run-sequence FILE --port COM7 --id 127 --yes     # 実機(モータを固定してから)
```

`cli/motion.py` と同じ約束: `--yes` が無いと何も送らず終了コード 1 / 出力段階は低出力のみ(`--tier high` は不可)/ Ctrl+C で中断して後始末 / 終了時に `MotorService.close()`・`Session.close()` が全モータへ stop(三重の保険)。YAML に問題があれば行番号つきで表示して終了コード 1。

## 5. 「スクリプト」ページ

ナビの「スクリプト」(Ctrl+5。保守の次・設定の前。`order` = 50。これより後のページ = サンプルの三角波(60)などは、ナビの順にスクリプトの後ろへ並び、Ctrl+6 以降になる)。上部に常設の注記「指令はすべて SafetyGuard を通ります」。セグメントで [コンソール | シーケンス] を切り替える(区画ごとに主ボタンは 1 つ)。

- **コンソール**: 出力ペイン + 入力欄。Enter で実行、Shift+Enter で改行、↑ ↓ で履歴(プラグイン設定に最大 50 件を保存)。実行は**ワーカースレッド**なので GUI は止まらない。実行中は主ボタンが「中断」に変わる。最後の式の値は表示し、Future なら完了を待って結果を表示する。
  - 名前空間: `service`(MotorService)/ `telemetry` / `session` / `controllers`(名前 → クラス)/ `params`(名前 → Params クラス)/ `make(name, **params)` / `ids()` / `sleep(s)` / `wait(future, timeout)` / `run_sequence(path, motor=None)` / `RunMode` / `PowerTier` / `UiState`。`print` は出力ペインへ。
  - 待つときは `time.sleep` ではなく `sleep()` / `wait()` を使う(中断で起きる)。`while True:` のような Python の処理は中断で止まる。
  - **中断**: スクリプトを止め、全モータを運転停止にする(有効のまま保持)。
  - **非常停止**(Pause / Esc / ヘッダーのボタン)はシェルが `MotorService.estop()` を同期で呼ぶので、スクリプトの実行中でも常に働く。ラッチ後にスクリプトが指令を出しても `SafetyError` で拒否される(ページはスクリプトとシーケンスも中断する)。
- **シーケンス**: `sequences/*.yaml` の一覧(「ほかのファイル…」で任意の場所)とモータを選ぶ → 検証結果(概要・問題・注意)→ 手順の一覧(進捗に合わせて ○ → ▶ → ✓)と実行ログ → [シーケンスを実行] / [中断]。押せない理由(未接続・非常停止中・高出力・コンソール実行中・モータが停止中でない・ファイルに問題)はツールチップに出る。

## 6. プラグインにした理由

任意の Python を実行できる機能なので、利用者が設定ページで**無効にできる**ように `plugins/scripting/` に置いた(組込みは無効にできない)。無効にすると、次回の起動から `activate` が呼ばれず、ページの実装(`page.py`)も import されない(`plugin.py` 自体は読み込まれる。`plugin_guide.md` §1)。実行の中身(検証・実行・コンソールエンジン)は Qt を使わない `core/` にあり、CLI とテストが同じものを使う。プラグインは画面の配線だけ(`add_page` 1 つ)。UI 文字列は `tr()` で `ja.toml` の `[script]` を引く(プラグイン専用のカタログは無い)。

## 7. 制限

- 1 台のモータに対する手順のみ(2 台連動などの複数スロットのコントローラは不可)。
- 実行中のシーケンスは 1 つだけ(ページから)。コンソールの `run_sequence()` も同じ `SequenceRunner`。
- コンソールの中断が反映されるのは、実行が Python のコードに戻ったときである。C の待ち(ブロッキング I/O)の最中は、そこから戻るまで中断できない(`sleep()` / `wait()` は中断で戻るので、この制限を受けない)。
