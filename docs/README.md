# docs/ 索引

| フォルダ | 中身 | ルール |
|---|---|---|
| `vendor/` | メーカー一次資料のコピー(PDF・STEP・参照コード)。出典は `vendor/README.md` | **編集しない** |
| `research/` | 一次資料を読んで自分で要約・検証したメモ | 新事実が分かったら追記。出典(資料名+ページ / URL / 実機ログ)を必ず書く |
| `design/` | 構成案・実装計画・未決事項・タスク指示書 | 決定事項はここが正 |
| `manual.md` | 利用者マニュアル | 機能を変えたら合わせて直す |
| `style.md` | 利用者向け文書の文章規範(適用範囲・用語表) | 利用者向け文書はこれに従う |

## research/
| ファイル | 内容 |
|---|---|
| `el05_spec.md` | EL05 ハードウェア仕様(電気・機械・コネクタ・保護)、GUI 既定値の根拠 |
| `el05_protocol.md` | CAN 通信プロトコル完全版(全通信タイプ、パラメータ表、run_mode 手順、MIT プロトコル、CANopen、EN/CN 差異)|
| `motorstudio_features.md` | 公式 MotorStudio の機能一覧・画面・手順・問題点、公式 Python 版(EL-A3)の構成 |
| `can_transport.md` | PC ⇔ CAN の経路(公式 USB-CAN の AT フレーム、python-can 対応アダプタ、帯域計算、トランスポート設計)|
| `tech_stack.md` | GUI 技術スタック比較と推奨、STEP→メッシュ変換 |

## design/
| ファイル | 内容 |
|---|---|
| `architecture.md` | ソフトウェア構成案 |
| `implementation_plan.md` | Phase 別実装計画・担当モデル・実機チェックポイント |
| `open_questions.md` | ユーザー判断待ち・実機確認待ち・資料食い違いの採用判断 |
