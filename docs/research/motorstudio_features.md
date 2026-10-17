# RobStride MotorStudio（上位机）機能調査 — EL05 用 GUI ツール設計のための一次資料まとめ

作成日: 2026-10-01
目的: RobStride EduLite 05（EL05）用の自作 GUI ツールを作るにあたり、公式上位机（MotorStudio / motor_tool）が持つ全機能・画面・操作手順を棚卸しし、改善点と再利用できる設計アイデアを整理する。アプリケーションコードは含まない。

## 出典の略号

| 略号 | ファイル | ページ表記 |
|---|---|---|
| **S-EN** | `docs/vendor/motorstudio/Instructions for using the Studio_241122.pdf`（英語、13 p、Typora 出力、2024-11-22） | PDF ページ |
| **S-ZH** | `docs/vendor/motorstudio/灵足时代上位机使用说明书_241122.pdf`（中国語原本、13 p） | PDF ページ |
| **EL-EN** | `docs/vendor/el05/EL05User Manual260713.pdf`（80 p） | **PDF ページ**（本文の印刷ページ番号＝PDF ページ − 1） |
| **EL-ZH** | `docs/vendor/el05/EL05使用说明书260713.pdf`（58 p） | PDF ページ |
| **GH** | https://github.com/RobStride/MotorStudio （releases / issues、2026-10-01 に GitHub API で取得） | — |
| **A3** | `docs/vendor/reference_code/el_a3_sdk/MotorStudio/`（EL-A3 アーム用 Python ツール） | ファイル名 |

> 注: S-EN / S-ZH は Read ツールでは「パスワード保護」と判定されるが、`pdfinfo` では Encrypted: no。テキストは `pdftotext`、スクリーンショットは `pdfimages` / `pdftoppm` で抽出して確認した。S-ZH はフォントの ToUnicode がなく中国語テキストが抽出できないため、ページ画像を目視した。

---

## 1. 概要・バージョン・接続

### 1.1 2 系統の「上位机」が存在する

調べた資料には、見た目も構成もまったく違う 2 系統の公式ツールが出てくる。

| 系統 | 見た目 | 確認できたバージョン | 記載元 | 特徴 |
|---|---|---|---|---|
| **(A) MotorStudio「灵足时代 / Lingzu」フル版** | ダークテーマ。メニュー型（Device / Configure / Analyze / Help）、ドッキングパネル | 画面タイトルは `灵足时代 v0.0.1`（S-ZH p1）と `Lingzu v0.0.2`（S-EN p1） | S-EN、S-ZH | デバイスツリー、ログ/通信タブ、スライダ付きモーションパネル、EStop ボタン。GH の配布物 `0.0.6-CH.zip` / `0.0.6-EN.zip` / `motorstudio0.0.8.zip`（約 116〜145 MB）がこれにあたると推定 |
| **(B) 「simple motor tool」簡易版（motorstudio 0.0.x）** | Qt5 ウィジェットのライトテーマ。左・中央・右の 3 カラムを 1 画面に固定配置 | `motorstudio v0.0.7`（EL-ZH p11/p21）、`motorstudio 0.0.11`（EL-EN p15/p30） | EL-EN 3 章、EL-ZH 3 章 | 主動上報、OTA（Erase/Stop）、USB-CAN モジュール設定、マルチデバイス接続、プロトコル切替ボタン。GH の `simple_motor*`・`motor_tool*`（約 20 MB）がこれにあたると推定 |

**EL05 マニュアルが説明しているのは (B) のほう**。EL05 対応が明記されているのも (B) の系列（GH v1.0.2「更新 EL05」）。自作ツールは (B) の機能を基準にし、(A) の良い UI 要素（スライダ、連続送信、ツリー、ログ/通信ビュー）を取り込むのがよい。

### 1.2 GitHub リリース（RobStride/MotorStudio）— 2026-10-01 時点

バイナリはダウンロードしていない。リポジトリにソースはなく、README と `使用说明书-Instructions/`（S-EN / S-ZH と同じ PDF）だけが置かれている。

| Tag | 公開日 | アセット（ダウンロード URL） | リリースノート要約 |
|---|---|---|---|
| **v1.0.3**（最新） | 2026-02-09 | [motor_toolV14L.zip](https://github.com/RobStride/MotorStudio/releases/download/v1.0.3/motor_toolV14L.zip)（21.4 MB） | 「更新上位机 / update motorstudio」（詳細なし） |
| v1.0.2 | 2025-09-18 | [motor_tool_win_v00122.zip](https://github.com/RobStride/MotorStudio/releases/download/v1.0.2/motor_tool_win_v00122.zip) | 中英切替ボタン追加、**EL05 対応** |
| v1,0,1 | 2025-09-18 | [motor_tool.AppImage](https://github.com/RobStride/MotorStudio/releases/download/v1%2C0%2C1/motor_tool.AppImage)（36 MB） | Linux 版（AppImage）、英語版追加 |
| v1.0.0 | 2025-08-20 | [motor_tool_v0010_00619.zip](https://github.com/RobStride/MotorStudio/releases/download/v1.0.0/motor_tool_v0010_00619.zip) | 画面ズーム機能、UI 改善 |
| V0.0.8 | 2025-02-27 | [motorstudio0.0.8.zip](https://github.com/RobStride/MotorStudio/releases/download/V0.0.8/motorstudio0.0.8.zip)（116 MB）、[simple_motorstudio_0.0.8_CHS.zip](https://github.com/RobStride/MotorStudio/releases/download/V0.0.8/simple_motorstudio_0.0.8_CHS.zip) | 簡易版に**マルチモータ接続**を追加 |
| V24.12.18 | 2024-12-18 | [simple_motor_tool1213_En.zip](https://github.com/RobStride/MotorStudio/releases/download/V24.12.18/simple_motor_tool1213_En.zip)、[simple_motor_tool121_CHn.zip](https://github.com/RobStride/MotorStudio/releases/download/V24.12.18/simple_motor_tool121_CHn.zip) | ①**主動上報の ON/OFF と周期変更**、②**USB-CAN モジュールのボーレート変更** |
| v0.0.6 | 2024-11-18 | 0.0.6-CH.zip / 0.0.6-EN.zip / ddl_missing_Solution_{Ch,En}.zip | DLL 欠落への対処パッケージ付き |

- Issue #2（2025-11, open）: Ubuntu 22.04 で `motor_tool: undefined symbol: __cxa_throw_bad_array_new_length, version Qt_5` が出て起動しない → **(B) は Qt5 製**で、Linux 版はバイナリ互換の問題を抱えている（GH）。
- Issue #1（2025-10, open）: Mac 版の要望（GH）。
- 公式サイトの配布ページ（https://robstride.com/download）は JS で描画されるため、中身は取得できなかった。S-EN p1 / EL-EN p14 は「www.robstride.com のダウンロードセンターから入手」と書いている。

### 1.3 ハードウェア構成と接続フロー

- **アダプタ**: 公式 USB-CAN モジュール（CH340 USB シリアル）。CH340 ドライバが必要で、デフォルトは **AT モード**。別のアダプタへ移植する場合は「第 3 章を参照して開発」とある（S-EN p1, S-ZH p1, EL-EN p14）。
- **シリアルフレーム（AT プロトコル）**: `41 54`（"AT"）＋ 4 バイト拡張 ID ＋ DLC ＋ データ ＋ `0D 0A`（S-EN p1/p8, S-ZH p9, EL-EN p30–31）。
  - 4 バイト ID は `(29bit CAN ID << 3) | 0b100`（bit2 が拡張フレームフラグ）。例: `90 07 e8 0c` → 右 3 bit を落とすと `0x1200FD01` = 通信タイプ 18 / host 0xFD / motor 0x01（S-ZH p9, EL-EN p31）。S-EN p8 は `43 07 e8 0c` と誤記している。
  - 例フレーム `41 54 90 07 e8 0c 08 05 70 00 00 01 00 00 00 0d 0a` は、type 18 で index 0x7005（run_mode）に 1（PP）を書き込むもの（index 表 EL-EN p47 と照合した）。
- **USB-CAN モジュールの DIP スイッチ**: SW1=ON で Boot モードになり接続できない。SW2=ON で 120 Ω 終端が入る（EL-EN p14）。
- **シリアルボーレート**: 接続ダイアログの Baudrate は **921600 固定（グレーアウト）**（S-EN p3, S-ZH p3）。
- **CAN ビットレート**: ドライバ仕様は 1 Mbps（EL-EN p13）。(B) では「CAN Baud rate」でモジュール側の CAN ビットレートを確認・変更でき、表示例は「The module baud rate is 1Mkbps」（EL-EN p30）。v0.0.7 では「请升级最新固件以获取修改波特率功能」（最新ファームにしないと変更不可）と表示される（EL-ZH p21）。
- **Host ID**: ツールは host CAN ID **0xFD** を使う（S-ZH p9 の例、EL-EN p49 の読み出し例）。モータ側のパラメータ 0x200b CAN_MASTER（既定 0）とは別もの。
- **接続手順**
  - (A): Device → Connect →「Detect Devices / 探测设备」ダイアログで Module = `USB-CAN (AT Command)`、COM = `USB-SERIAL CH340 (COMx)`（↻ で再列挙）、Baudrate 921600、Motor Type（英語版のみ。例 `Robstride-03-60N.m-QDD`）を選んで Connect（S-EN p3, S-ZH p3）。ログには「正在通过串口COM11探测设备 → 检测到设备: [MCU ID: 0x3313…658a, CAN ID: 127] → 切换当前设备: Lingzu Motor 127 → 发送L91指令LOAD_PARAM至CAN(127) → 全部参数加载成功!」と出る（S-ZH p1 のスクリーンショット）。失敗時は「<错误> USB-CAN模块配置失败，请检查设备!」。
  - (B): COM を選ぶ → Refresh COM → Open COM（ボタン表記が Close Serial Port に変わる）→ Detection Devices → 検出したデバイスがコンボに `CAN:127 id:6b02…` の形で並び、下に緑字で `motor type RS00` などのモータ型式が出る。ステータスバーには `Detected equipment,mcuId:0X6b02221483b2681 canId:127`（EL-EN p16/p30, EL-ZH p13）。
  - **スキャンの中身**: 通信タイプ 0（デバイス ID 取得。応答に 64bit MCU ID を含む）で全 ID を探索していると推定（EL-EN p40 の type 0 定義と、画面に MCU ID が出ることからの推定）。

---

## 2. 機能一覧（Feature inventory）

凡例: (A)=フル版、(B)=簡易版。「通信」は EL05 プライベートプロトコルの通信タイプ（EL-EN p40–49）と index（0x70xx）。**推定**と書いたものは資料に明記がなく、画面や送信データから推測したもの。

| # | 機能 | 画面・操作 | 使用する通信タイプ / パラメータ | 備考・出典 |
|---|---|---|---|---|
| 1 | シリアルポート列挙・開閉 | (A) 接続ダイアログの COM と ↻、(B) Refresh COM / Open COM / Close Serial Port | — | S-EN p3, EL-EN p16 |
| 2 | デバイス探索（スキャン） | (A) Connect、(B) Detection Devices → デバイスコンボ | type 0（推定）。MCU ID と CAN ID を表示 | EL-EN p16/p30 |
| 3 | 型式表示・選択 | (A) Motor Type コンボ（手動）、(B) 自動表示「motor type RS00 / 03」 | 推定: 0x0000 Name やバージョン情報から判定 | S-EN p3, EL-EN p16。EL05 マニュアルのスクショでは RS00 と表示され、FW ファイル名も `rs00_0.0.3.6.bin`（EL-EN p18–20） |
| 4 | 複数デバイスの切替 | (A) ツリー（Lingzu Motor 127 → Info / Performance / Param）と「Activated Device」コンボ、(B) デバイスコンボ | — | S-EN p1–2 |
| 5 | マルチデバイス接続 | (B) Open Multi-device / 打开多设备连接 | — | **アップグレード専用**。型式ごとにパラメータ表が違うため。終わったら閉じて再検索する（EL-EN p20–21, GH V0.0.8） |
| 6 | デバイス情報 | (A) Device Info、ツリーの Info / Performance | 0x1000–0x1007（Boot/App バージョン、ビルド日時、CodeName） | S-EN p1–2 |
| 7 | CAN ID 変更 | (A) Change CAN ID [spin]＋✓、(B) Set ID [edit] | **type 7**（CAN_ID 設定）。パラメータ表の 0x200a CAN_ID（0–127）でも可 | S-EN p3, EL-EN p17, EL-EN p43 |
| 8 | 機械ゼロ設定 | (A) Reset Zero Position、(B) Set Zero Position / 设零位 | **type 6** | 電源を切ると失われる（掉电丢失）。「現在位置を 0 にする」（S-EN p3, S-ZH p3）。EL05 では CSP と運控モードで可、**PP モードでは拒否**。新ファームでは目標位置も 0 に更新されるので動かない（EL-EN p51） |
| 9 | 電機初期化（関節位置プリセット） | (A 中国語 v0.0.1 のみ) 电机初始化 [右前1 ▼] ✓ | 推定: 0x2009 motor_baud「电机index，标记电机关节位置」 | 四足ロボットの関節位置ラベル（右前1 など）。S-ZH p3 のツールバー画像、S-ZH p5 |
| 10 | 磁気エンコーダ校正 | (A) Encoder Calibration、(B) Encoder Calibration / 磁编标定 | 非公開（推定: 専用コマンド）。結果は 0x2005 MechOffset、0x301d phaseOrder など | 基板の再組付けや三相線の入替え後に必要。faultSta bit7「Encoder uncalibrated」（S-EN p3, EL-EN p17, EL-EN p31） |
| 11 | 主動上報（active report）ON/OFF | (B) Start Auto Report / 开始上报 | **type 24**。上報されるのは type 2 フレーム | 既定 OFF、既定周期 10 ms（EL-EN p17, p50, GH V24.12.18） |
| 12 | 上報周期設定 | (B) Set Report Time [10] ms | **type 18 → 0x7026 EPScan_time**（マニュアル: 1=10 ms、+1 ごとに +5 ms。実測は 10 + 5·N ms で N=0 → 10 ms） | 最小 10 ms（EL-EN p17, p49） |
| 13 | ファームウェア更新（OTA） | (A) Update → bin 選択 → 確認 → 進捗 → 自動再起動。(B) Open File → **Start Upgrade**（「Device has entered upgrade mode」表示）→ **Start Update** → 進捗バー → 「Upgrade Successfully」。ボタンは Erase ↔ Stop the upgrade ↔ Start the upgrade とトグルし、「Erasing flash, please wait..」と表示 | 非公開（Boot 経由） | FW 名の `rs-0x` が型式を表す。途中で止まったら中止するか電源を入れ直してやり直す。**失敗しても内部プログラムは消えない**（EL-EN p17–19, S-EN p13, S-ZH p13） |
| 14 | パラメータ表の読込（リフレッシュ） | (A) Configure → Param Table（開くと LOAD_PARAM）、(B) Refresh Para Table / 参数表刷新 →「Update parameter table successfully!」 | 非公開の **L91 命令 LOAD_PARAM**（S-ZH p1 のログ）。(B) の送信データから **通信タイプ 19（0x13）** を使っていると推定（EL-EN p30 のスクショ `41 54 98 07 eb fc …` → 0x1300FD7F） | **待機中（standby）でないと更新できない**（S-EN p3, EL-EN p20） |
| 15 | パラメータ読出し（Upload） | (A) Upload、(B) Read Para / 读参数 | 同上 | 観測値（observation）もここで取得される（S-EN p2, EL-EN p20） |
| 16 | パラメータ書込み（Download） | (A) Download、(B) Write Para / 写参数 | 同上（書込み系） | 表の「Current Value」セルを直接編集してから書き込む（S-EN p3, EL-EN p20） |
| 17 | 工場出荷状態に戻す | (A) Restore factory、(B) Factory Reset / 恢复出厂 | 非公開 | 「最新ファームの既定値」に戻す（EL-EN p20） |
| 18 | パラメータのエクスポート | (A) Export parameters、(B) Export ParaTable / 导出 | — | ローカルファイルに保存。**インポート機能はない**（S-EN p2, EL-EN p20） |
| 19 | 警告・故障のクリア | (A) Clear warning（例: 過温）、(B) Clear Motor Fault / 清故障 | **type 4**（停止フレームで Byte0=1 にすると fault をクリア）。故障通知は type 21 | S-EN p2, EL-EN p15 |
| 20 | 故障表示 | 専用 UI は資料に出てこない。パラメータ表の 0x3022 faultSta / 0x3023 warnSta / 0x3024 drv_fault、0x3038–0x303f fault1–8（log failure）を見る | faultSta bit14=ロック過負荷、bit7=エンコーダ未校正、bit3=過電圧（60 V 超）、bit2=低電圧（12 V 未満）、bit1=ドライバ IC 故障、bit0=過温（NTC 135 ℃ 超） | EL-EN p31, p28 |
| 21 | オシロスコープ | (A) Analyze → Oscilloscope：Frequency [100Hz]、Channel、Start / Stop、Output waveform。(B) oscilloscope タブ：**8 チャネル**（赤・緑・青・黄・マゼンタ・シアン・橙・紫のコンボ、既定「无/none」）、Output frequency [500] HZ、Apply、start、**Data Storage** | 非公開。推定: 0x2000–0x2003 echoPara1–4 がチャネル選択（値=パラメータ番号。最大値 74/91/103/110 がファームごとの表の項目数と一致）、0x2004 echoFreHz（1–10000、既定 500）が出力周波数。(B) の停止時の送信データ `41 54 50 1f e8 0c …` は **通信タイプ 10**（0x0A03FD01）と読める（EL-ZH p21）→ オシロ制御と推定 | 観測できるのは Id/Iq、温度、出力軸速度、ロータ（エンコーダ）位置、出力軸位置など、パラメータ表の任意の項目（S-EN p7, EL-EN p29–30, EL-ZH p20–21）。(A) はチャネル数不明、echoPara が 4 個なので 4ch の可能性あり |
| 22 | 波形データの保存 | (A) Output waveform（チェック）、(B) Data Storage / 数据保存 | — | 保存形式は資料に記載なし（S-EN p2, EL-EN p30） |
| 23 | ログ・通信モニタ | (A) 下部 D 欄：Log / Communication タブ、Clear、Export。(B) 下部テキスト欄＋ Clear testEdit / 清空、「COM send Data / 发送数据」欄に最後に送った AT フレームを 16 進表示 | — | S-EN p1–2, EL-EN p30 |
| 24 | JOG | (A) Quick Move：Max Speed [5.0000] rad/s、JOG Move [+] [−]（押している間だけ回転）。(B) jog+ / jog- | 推定: 速度モード（type 18 で run_mode=2、spd_ref） | (B) は **1 rad/s 固定**（EL-EN p33）。(A) は最大速度を設定できる（S-EN p9） |
| 25 | 有効化・停止 | (A) 各モードの右端トグルスイッチ＋ EStop / 紧急停止。(B) Enable / 运行、Stop / 停止 | **type 3**（enable）、**type 4**（stop） | S-EN p10, EL-EN p33 |
| 26 | 運転モード切替 | (A) Control Mode コンボ、(B) Run Mode コンボ＋タブ（MIT / PP / Velocity / Current / CSP） | type 18 → 0x7005 run_mode（0=運控、1=PP、2=速度、3=電流、5=CSP） | 切替は停止中に行う必要がある（EL-EN p47, p52） |
| 27 | ゼロ点モード（ゼロ復帰） | (A) Control Mode = Zero Position / 零点模式 → トグルで「緩やかに機械ゼロへ戻る」 | 推定: CSP で 0 rad を指令 | S-EN p10, S-ZH p10 |
| 28 | 運控モード（MIT / Operation control） | (A) Torque(Nm)・Position(rad)・Velocity(rad/s)・Kp・Kd の 5 項目。各項目にスライダ＋spin＋「0」リセット、Continuously（連続送信）、<< Start >>。(B) MIT タブ：5 項目のテキスト入力、Continuous Send / Single Send | **type 1**（Byte0–7 に pos/vel/Kp/Kd、ID の data2 に torque を入れる）。応答は type 2 | t_ref = Kd·(v_set−v)+Kp·(p_set−p)+t_ff（EL-EN p52）。S-EN p9–10, EL-EN p33 |
| 29 | 電流モード | (A) Iq指令(A) [1.0000] << Send >>。(B) Current タブ：Iq Cmd 1 [1.0] >>、Iq Cmd 2 [0.0] >>、Iq Cmd [0.0] >> | run_mode=3、**0x7006 iq_ref**（EL05 は −11〜11 A） | Iq Cmd 1/2 の 2 値はステップ試験用と推定（EL-EN p34–35） |
| 30 | 速度モード | (A) 電流制限(A) [10.3604] スライダ、速度指令(rad/s) [0.0000]、連続送信、<< 開始 >>。(B) Velocity タブ：Current Limit(A) [27.0] >>、Speed Step(rad/s/s) [10.0] >>、Velocity(rad/s) [0.0] >> | run_mode=2、**0x7018 limit_cur**、**0x7022 acc_rad**（速度モード加速度）、**0x700A spd_ref** | EL-EN p35。(B) の既定 27 A は EL05 の上限 11 A を超えている（→ 5 章） |
| 31 | 位置モード CSP | (A) 速度設定(rad/s) スライダ、位置指令(rad) [0.0000]、連続送信。(B) CSP タブ：Velocity Limit(rad/s) [5.0] >>、Angle Command(rad) [0.0] >> | run_mode=5、**0x7017 limit_spd**、**0x7016 loc_ref** | EL-EN p38–39, S-EN p12 |
| 32 | 位置モード PP（插补位置模式） | (A) 速度設定 [10.0000]、加速度設定(rad/s²) [10.00]、位置指令 [0.0000]、連続送信、<< 開始 >>。(B) PP タブ：Speed(rad/s) [2.0] >>、Acc.(rad/s/s) [10.0] >>、Angle Cmd(rad) [0.0] >> | run_mode=1、**0x7024 vel_max**、**0x7025 acc_set**、loc_ref。減速度は acc_stutus=1 で別設定になり、値は 0x702E に入る | 速度を 0 にするとその場で停止し、再開するには速度と位置を送り直す（EL-EN p36–37, p52） |
| 33 | 正弦波自動テスト | (A) 電流/速度/CSP の各パネルに「Sin signal automated test」トグル＋ Amplitude / Frequency(Hz)。(B) 右下に共通の「Sine Wave Test」：Amplitude [1.0] Apply、Frequency(Hz) [1.0] Start | 現在のモードの目標値を sin で計画する（ホスト側で生成） | 電流(A)・速度(rad/s)・位置(rad) の正弦波。S-EN p11–12, EL-EN p34–39 |
| 34 | 連続送信・単発送信 | (A)「Continuously / 连续发送」チェック、(B) MIT タブの Continuous Send / Single Send、他のタブは「>>」で単発 | — | 送信周期は資料に記載なし |
| 35 | CAN 通信タイムアウト保護 | パラメータ表 0x200c CAN_TIMEOUT（0=無効） | index 0x7028 canTimeout でも可 | 指令が途絶えると reset モードに入る。**20000 = 1 s**（S-ZH p9, EL-EN p31）。S-EN p8 の「2000 is 1 second」は誤り |
| 36 | プロトコル切替（Private ↔ CANopen ↔ MIT） | (B) 「Canopen to Private Protocol」ボタン（下部右）、パラメータ表 0x2022 protocol_1 | **type 25**（再起動後に反映）。CANopen からは拡張フレームの切替フレーム、MIT からは標準フレームの Command 8 で戻す | EL-EN p30, p51。CANopen の ID は CAN ID と同じ（EL-EN p52） |
| 37 | モータの CAN ボーレート変更 | パラメータ表 0x2009 motor_baud（「Baud rate flag bit」） | **type 23**（再起動後に反映） | EL-EN p22, p46。GUI の専用ボタンは資料に出てこない |
| 38 | USB-CAN モジュール設定 | (B) USB-CAN Module 欄：**Device Read**（→「Connected module」）、**CAN Type**（Extended / Standard Frame）、**CAN Baud rate**（モジュールの CAN レート） | モジュールへの AT コマンド（非公開） | EL-EN p30, EL-ZH p21, GH V24.12.18 |
| 39 | パラメータ保存（Flash） | 資料に明示のボタンはない。Write Para 後に保存されると推定 | **type 22**（type 18 の書込みは電源断で消える） | EL-EN p45–46, p50 |
| 40 | ゼロフラグ zero_sta | パラメータ表 0x2021 / 0x7029 | 0: 起動時の位置範囲 0〜2π、1: −π〜π | 変更には type 22 の保存が必要（EL-EN p50） |
| 41 | 初期化校正（iq_test） | パラメータ 0x702D iq_test=1 | 初期化時間が延び、初期基準がより正確になる | EL-EN p52 |
| 42 | コギング（齿槽）校正 | 「上位機で実施」とあるが、専用ボタンは資料のスクショに写っていない | iq_test=1 → 再起動 → 無負荷で校正 → max_alve≠0 を確認 → 0x702C alveolous_open=1（運控モードで有効） | EL-EN p52。(B) の新しい版に機能があると推定 |
| 43 | 言語切替 | (B) 「中文」ボタン（EL-EN p30）、(A) 右上の歯車（設定）アイコン | — | GH v1.0.2 |
| 44 | ヘルプ | (A) Help → 使用説明を開く、About | — | S-EN p2 |
| 45 | 画面ズーム | (B v1.0.0 以降) | — | GH v1.0.0 |

### 2.1 パラメータ表（上位機の「功能码」表）の構成

**上位機の表は、type 17/18 の 0x70xx index とは別の「功能码」空間**（0x0000〜0x304e）を使っている。

| 範囲 | 属性 | 主な項目（EL05 マニュアル版、EL-EN p21–29） |
|---|---|---|
| 0x0000–0x0001 | R/W 文字列（緑表示） | Name, BarCode |
| 0x1000–0x1007 | RO 文字列 | BootCodeVersion / BuildDate / BuildTime, AppCodeVersion（例: 0.0.3.6）, AppGitVersion, AppBuildDate / Time, AppCodeName |
| 0x2000–0x2023 | 設定・R/W（青／シアン表示、Flash に保存） | echoPara1–4, echoFreHz, MechOffset, status4 (MechPos_init), limit_torque, I_FW_MAX, motor_baud, CAN_ID, CAN_MASTER, CAN_TIMEOUT, status1/2/3/6（予約）, cur_filt_gain, cur_kp, cur_ki, spd_kp, spd_ki, loc_kp, spd_filt_gain, limit_spd, limit_cur, loc_ref_filt_gain, add_elec_offset, position_offset, chasu_angle_offset, spd_step_value, vel_max, acc_set, zero_sta, protocol_1, add_offset |
| 0x3000–0x304e | RO 観測値（淡青表示、リアルタイム） | timeUse0–3, encoderRaw, mcuTemp(×10), motorTemp(×10), vBus(mV), adc offset/raw, VBUS, cmdId / cmdIq / cmdlocref / cmdspdref / cmdTorque / cmdPos / cmdVel, rotation, modPos, mechPos, mechVel, elecPos, ia / ib / ic, timeout, phaseOrder, iqf, boardTemp, iq, id, faultSta, warnSta, drv_fault, drv_temp, Uq, Ud, dtc_u/v/w, v_bus, torque_fdb, rated_i, limit_i, spd_ref, spd_reff, zero_fault, chasu_*, fault1–8, ElecOffset, mcOverTemp, Kt_Nm/Amp, Tqcali_Type, low_position, theta_mech_1, instep, position, … |

- **ファームによって表の中身が違う**: S-EN（p4–7）は旧配置（0x2006 chasu_offset、0x2007 ElecOffset、0x2009 CAN_ID、0x200e GearRatio …）。S-ZH（p4–8）と EL-EN は新配置（0x2009 motor_baud、0x200a CAN_ID …）。**表の定義はハードコードせず、ファームのバージョンごとの定義ファイルにする必要がある**。
- 注意書き: トルク制限・保護温度・過温時間はむやみに変更しないこと（S-EN p3, EL-EN p21）。
- S-EN の表は PDF 変換で列がずれており、S-ZH p4–8 の表が正しい。

---

## 3. 画面レイアウト（スクリーンショットから）

### 3.1 (A) フル版（Lingzu v0.0.2 / 灵足时代 v0.0.1）— S-EN p1, S-ZH p1/p9

```
┌ A: メニュー [Device][Configure][Analyze][Help]   タイトル "Lingzu v0.0.2"   ⚙ − □ × ┐
├ B: サブツールバー（選んだモジュールで中身が変わる）                                  ┤
│   Device   : Connect | Disconnect | Device Info | Encoder Calibration |              │
│              Change CAN ID [1]✓ | Reset Zero Position | Update                      │
│   Analyze  : Oscilloscope | Frequency [100Hz]⇅ | ○Channel | ▶Start | ■Stop |        │
│              □Output waveform                                                       │
├──────────────┬───────────────────────────────┬───────────────────────────────────┤
│ C: ツリー     │ F: タブ領域 [Param Table ×]   │ E: Activated Device [Lingzu Motor 127▼] │
│ Name / Value │   [Oscilloscope ×]            │    [⛔ EStop]                           │
│ ▼Lingzu Moto │   表: Code/Name/Type/Prop/    │  ┌Quick Move──────────────────┐        │
│   Connected  │   Max/Min/Value               │  │Max Speed [5.0000]⇅ rad/s   │        │
│  ▶Info       │   グラフ: pyqtgraph 風の       │  │JOG Move [ + ] [ − ]         │        │
│  ▶Performance│   時系列（1 本の赤線）         │  └────────────────────────────┘        │
│  ▶Param      ├───────────────────────────────┤  ┌Motion Control───────────────┐       │
│              │ D: [Log][Communication]       │  │Control Mode [Zero Position▼] ◯━ │    │
│              │   🗑Clear  ⎘Export ▾           │  │Return to zero position        │     │
│              │   番号付き・時刻付きログ        │  └────────────────────────────┘       │
└──────────────┴───────────────────────────────┴───────────────────────────────────┘
```

- **接続ダイアログ**「Detect Devices / 探测设备」: Module `USB-CAN (AT Command)`、COM `USB-SERIAL CH340 (COM6)` ↻、Baudrate `921600`（無効化表示）、Motor Type `Robstride-03-60N.m-QDD`（英語版のみ）、「Click here to install ch340 driver」、[Connect] [Cancel]（S-EN p3, S-ZH p3）。
- **パラメータ表**: 列は Code / Name / Type / Property / Maximum / Minimum / Value。色分けは緑=R/W 文字列、灰=RO、青=設定（保存される）、緑系=観測値（S-EN p3–4）。
- **Control Mode の一覧**: Zero Position（零点模式）/ MIT Control（运控模式）/ Current Mode / Speed Mode / Position Mode(CSP) / Position Mode(PP)（S-EN p9–13）。
- **各モードの既定値**（画像から読み取り）

| モード | 項目と既定値 | 出典 |
|---|---|---|
| MIT | Torque(nm)=0.0000、Position(rad)=0.0000、Velocity(rad/s)=0.0000（スライダは中央が 0）、Kp=0.0000、Kd=0.0000（スライダは左端が 0）、各項目に「0」リセットボタン、□Continuously、[<< Start >>] | S-EN p9–10, S-ZH p10–11 |
| Current | Sin test トグル、Amplitude=1.00、Frequency=1.00Hz、Iq指令(A)=1.0000、[<< Send >>] | S-EN p11, S-ZH p11 |
| Speed | Sin test、Amplitude=1.00、Frequency=1.00Hz、電流制限(A)=10.3604（スライダ）、速度指令(rad/s)=0.0000、□連続送信、[<< 開始 >>] | S-ZH p12 |
| CSP | Sin test、Amplitude=0.00、Frequency=0.00Hz、Velocity Setting(rad/s)（スライダ）、Position Command(rad)=0.0000、□Continuously（Start ボタンは画像外） | S-EN p12, S-ZH p12 |
| PP | Velocity Setting=10.0000 rad/s、Acceleration Setting=10.00 rad/s²、Position Command=0.0000 rad、□Continuously、[<< Start >>] | S-EN p13, S-ZH p13 |

### 3.2 (B) 簡易版（motorstudio 0.0.11 英語 / v0.0.7 中国語）— EL-EN p15/p30, EL-ZH p11/p21

```
┌ motorstudio 0.0.11 ─────────────────────────────────────────────────────── − □ × ┐
│┌Connect Motor─┐┌[Parameter Setting][oscilloscope]──────────────┐┌Run and Debug Area┐│
││[COM7      ▼] ││ 表: FunctionCode/Name/ParameterType/Attribute ││[Enable]  [Stop]  ││
││[Refresh COM] ││     /MaxValue/MinValue/CurrentValue           ││[jog+ ]  [jog- ]  ││
││[Open COM]    ││                              ┌─────────────┐  ││Run Mode [     ▼] ││
││[CAN:127 id:▼]││                              │ 中文        │  ││[MIT|PP|Velocity| ││
││[Detection    ││                              │ Refresh Para│  ││ Current|CSP ◀▶]  ││
││  Devices]    ││                              │ Read Para   │  ││ タブ別の入力 + >>││
││motor type RS00│                              │ Write Para  │  ││                  ││
│├Motor Config──┤│                              │ Factory Reset│ ││┌Sine Wave Test─┐││
││[Encoder Calib]│                              │ Export Para │  │││Amplitude[1.0] │││
││[Start Auto   ││                              │ Open Multi- │  │││      [Apply]  │││
││   Report]    ││                              │   device    │  │││Frequency(Hz)  │││
││[Set Report   ││                              └─────────────┘  │││ [1.0] [Start] │││
││  Time][10]ms ││ 「Update parameter table successfully!」(緑) ││└───────────────┘││
││[Set ID][1]   ││ ┌ログ/メッセージ欄────────┐[Clear Motor Fault]││                  ││
││[Set Zero Pos]││ └─────────────────────────┘[Clear testEdit]   ││                  ││
│├OAT──────────┤└───────────────────────────────────────────────┘└──────────────────┘│
││[Erase]/[Stop the upgrade]  Project/bin/pmsm_app.bin  [Open File] [Start Update] ││
││[■■■■■ 100%]  "Detected equipment,mcuId:0X6…"                                      ││
│ COM send Data [41549807ebfc0881263b482422b0060d0a]        [Canopen to Private Protocol]│
│ USB-CAN Module [Device Read] Connected module [CAN Type][Extended Frame▼] Extended Frame│
│                [CAN Baud rate][        ▼] The module baud rate is 1Mkbps                │
│ ステータスバー: Detected equipment,mcuId:0X6b02221483b2681 canId:127                     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Motor Configuration の既定値**: Report Time=10 ms、Set ID=1（EL-EN p17）。
- **OAT（OTA のこと。原文表記ママ）**: 既定のパスは `Project/bin/pmsm_app.bin`。状態メッセージは「Erasing flash, please wait..」「The equipment has entered th(e upgrade mode)」「Detected equipment,mcuId:…」（EL-EN p17–19）。
- **パラメータ表**: 緑=R/W 文字列、薄緑=RO 文字列、シアン=Setup/設定、太字=R/W の数値。表示例は AppCodeVersion 0.0.3.6（2025-04-17 ビルド）、echoPara 最大値 110、echoFreHz 500、MechOffset 65.417381（EL-EN p21）。v0.0.7 の表示例は AppCodeVersion 0.4.1.3、echoPara 最大値 103、echoFreHz 100（EL-ZH p20）。
- **oscilloscope タブ**: 右側に Channel selection。「Output frequency setting: 5(00)」、8 色のコンボ（既定 `无/none`。v0.0.7 の例では ch1=cmdVel）、Output frequency [500] HZ、[Apply] [start] [Data Storage]。グラフは Qt の独自描画で、軸は 0–10 / 0–1 の初期表示（EL-EN p30, EL-ZH p21）。
- **Run and Debug の各タブの既定値**

| タブ | 項目と既定値 | 出典 |
|---|---|---|
| MIT | Torque(Nm) 0.0、Angle(rad) 0.0、Velocity(rad/s) 0.0、KP 0.0、KD 0.0、[Continuous Send] [Single Send] | EL-EN p33 |
| PP（插补位置模式） | Speed(rad/s) 2.0 >>、Acc.(rad/s/s) 10.0 >>、Angle Cmd(rad) 0.0 >> | EL-EN p36, EL-ZH p11 |
| Velocity | Current Limit(A) 27.0 >>、Speed Step(rad/s/s) 10.0 >>、Velocity(rad/s) 0.0 >> | EL-EN p15, p35 |
| Current | Iq Cmd 1(Amp) 1.0 >>、Iq Cmd 2(Amp) 0.0 >>、Iq Cmd(Amp) 0.0 >> | EL-EN p34 |
| CSP | Velocity Limit(rad/s) 5.0 >>、Angle Command(rad) 0.0 >> | EL-EN p38 |
| Sine Wave Test | Amplitude 1.0 [Apply]、Frequency(Hz) 1.0 [Start] | EL-EN p34 |

- v0.0.7（中国語）は 0.0.11 と比べて「Open Multi-device」「CAN Type」「Canopen to Private Protocol」「中文」がなく、USB-CAN 欄は「设备查询」「波特率设置」だけ（EL-ZH p11/p21）。

---

## 4. マニュアルに書かれた操作手順（GUI ワークフローの原型）

| # | ワークフロー | 手順 | 出典 |
|---|---|---|---|
| W1 | 接続 | CH340 ドライバを入れる → DIP SW1=OFF、SW2=ON（終端）→ COM を選ぶ → Refresh → Open → Detection Devices → デバイスを選ぶ（型式が表示される） | EL-EN p14, p16 |
| W2 | パラメータ編集 | **待機中であることを確認** → Refresh Para Table（成功メッセージを待つ）→ 青（設定）の Current Value を編集 → Write Para →（Read Para で読み直して確認）→ 必要なら Export | EL-EN p20, S-EN p3 |
| W3 | ファームウェア更新 | Open File（`rs0x_*.bin`。型式が一致すること）→ Start Upgrade →「entered upgrade mode」を待つ → Start Update → 100% と「Upgrade Successfully」→ 自動で再起動。止まったら Stop するか電源を入れ直して再実行 | EL-EN p17–19 |
| W4 | エンコーダ校正 | 無負荷にする → Encoder Calibration → 完了後に faultSta bit7 が消えたか確認 | EL-EN p17, p31 |
| W5 | 運控（MIT） | 運控モードを選ぶ → Enable（motor_mode になる）→ 5 つのパラメータを設定 → Start または連続送信（type 2 の応答が返る）→ Stop | EL-EN p33, S-EN p10 |
| W6 | 電流モード | 電流モードを選ぶ → Enable → Iq Cmd を入力 → >> → Stop | EL-EN p34 |
| W7 | 速度モード | 速度モードを選ぶ → Enable → **先に Current Limit と Speed Step を設定**（設定しなければ既定値）→ 最後に Velocity を送る → Stop | EL-EN p35 |
| W8 | PP | 插补位置モードを選ぶ → Enable → **先に Speed と Acc を設定** → Angle Cmd を送る。Speed=0 でその場停止、再開は速度と位置を送り直す → Stop | EL-EN p36–37 |
| W9 | CSP | 位置モードを選ぶ → Enable → **先に Velocity Limit を設定** → Angle Command を送る → Stop | EL-EN p38 |
| W10 | 正弦波テスト（電流・速度・PP・CSP 共通） | モードを選ぶ → Enable → Amplitude / Frequency → Apply（确定）→ Start → Stop | EL-EN p34–39 |
| W11 | JOG | (A) Max Speed を設定 →「運転」→ JOG +/− を押す。(B) jog± は 1 rad/s | S-EN p9, EL-EN p33 |
| W12 | ゼロ設定 | 停止中、または CSP / MIT で Set Zero Position（PP では不可）。電源断で消える点に注意（永続化には保存が必要と推定） | EL-EN p51, S-EN p3 |
| W13 | オシロ | チャネルを選ぶ → 出力周波数を設定 → Apply → start → stop → Data Storage | EL-EN p29–30 |
| W14 | 主動上報 | Set Report Time（ms）→ Start Auto Report → type 2 フレームが周期的に届く | EL-EN p17 |
| W15 | プロトコル切替 | protocol_1 を書き換えるか type 25 を送る → **電源を入れ直す** → 戻すときは CANopen なら拡張フレーム、MIT なら標準フレームの Command 8 | EL-EN p51 |
| W16 | コギング校正 | iq_test=1 → 再起動 → 無負荷で上位機から校正 → max_alve≠0 を確認 → alveolous_open=1 | EL-EN p52 |
| W17 | 故障からの復帰 | faultSta を確認 → Clear Motor Fault（type 4 の clear）→ 必要なら再度 Enable | EL-EN p31, p15 |

---

## 5. 公式ツールの問題点・不足機能（自作ツールで改善できる点）

1. **アダプタが公式の CH340/AT モジュールに限られる**。SLCAN、SocketCAN、PCAN、Kvaser、candleLight などが使えない。接続層を差し替えられる構造にする（A3 の SocketCAN/SLCAN 切替が参考になる）。
2. **型式に応じた安全値になっていない**。(B) の速度モードの Current Limit 既定は **27 A** だが、EL05 の上限は **11 A**（limit_cur 0–11 A、ドライバ最大相電流 11 Apk。EL-EN p13, p47）。スライダ範囲、既定値、確認ダイアログを型式プロファイルで管理すべき。
3. **パラメータ表がファームごとに違う**のにバージョン対応が見えない（S-EN と S-ZH / EL-EN で番号が違う）。**インポートがなく**（エクスポートだけ）、差分表示・バックアップ・復元・一括書込みの前チェックもない。
4. **パラメータ表の読み書きは非公開コマンド（L91、type 19/10 と推定）**。自作では公開されている type 17/18/22 と 0x70xx index で同じことを実現し、必要なら非公開コマンドの解析を別途行う。
5. **Flash 保存（type 22）が UI にない**。type 18 の書込みやゼロ設定が電源断で消えることが UI からわからない。「一時適用」と「保存」を明確に分ける。
6. **故障表示が生の数値だけ**（faultSta などの bit を自分で読む必要がある）。bit をデコードした表示、履歴（fault1–8）、type 21 故障フレームの通知、ワンクリックの clear と再有効化がほしい。
7. **オシロの仕様が不明確**。チャネル数（A と B で異なる）、実効サンプルレート、欠損の有無がわからない。保存形式も不明。トリガ、カーソル、FFT、ステップ応答の評価（立ち上がり時間・オーバーシュート）、CSV/Parquet 出力がない。
8. **連続送信の周期が設定・表示されない**。送信レートと受信 FPS、バス負荷、エラーカウンタを表示すべき（A3 の CAN FPS 表示が参考になる）。
9. **JOG が 1 rad/s 固定**（B）。速度・加速度・トルク制限を設定できる JOG と、ソフトリミット（角度範囲）がほしい。
10. **安全機構が弱い**。(B) には EStop ボタンがなく、キーボードショートカット（Space で停止など）、ウォッチドッグ（CAN_TIMEOUT の自動設定）、切断時の自動停止がない。
11. **コギング校正・初期化校正・PP 減速度・zero_sta・プロトコル切替・モータ CAN ボーレート変更**は、マニュアルに「上位機で可能」とあるが、画面には専用 UI がない（または見当たらない）。ウィザード形式で提供すべき。
12. **マルチデバイスはアップグレード専用**。複数モータを同時に監視・制御できない。
13. **プラットフォーム**: Linux AppImage が Qt5 の ABI 問題で起動しない（Issue #2）、Mac 版がない（Issue #1）。
14. **ドキュメントの誤記・訳の乱れ**: CAN_TIMEOUT「2000=1 s」（S-EN p8）と正しくは 20000、拡張 ID の例 `43 07…`（S-EN p8）、EL-EN p15 のモジュール名 B/C の取り違え、0x3024/0x3025 の説明の誤訳（EL-EN p26）など。自作ツールには検証済みのパラメータ辞書を持たせる。
15. **ログ・自動化**: スクリプト実行、コマンド履歴の再生、試験シーケンス（ステップ・チャープ・sin スイープ）の定義、試験結果のレポート化がない。
16. **UI**: (B) は固定レイアウトで、表の列名が見切れる（`unctionCod`、`meterT`）。ドッキング・ズーム・テーマ・国際化（日本語）に対応するとよい。

---

## 6. EL-A3 用 Python MotorStudio（参考実装）の構成と再利用できるアイデア

所在: `docs/vendor/reference_code/el_a3_sdk/MotorStudio/`（約 7.4k 行）。7 軸アーム（6 関節＋グリッパ）のデバッグ用。

### 6.1 構成

| レイヤ | ファイル | 内容 |
|---|---|---|
| エントリ | `main.py` | argparse（`--can`, `--sim`, `--urdf`, `--meshes`, `--log-level`）、PyInstaller の frozen 対応（`sys._MEIPASS`）、`QT_API=pyqt6`、ThemeManager で QSS を全体に適用 |
| メインウィンドウ | `main_window.py` | `QMainWindow`＋`QDockWidget` を 4 つ（上: ツールバー、左: 3D ビューア、右: `QTabWidget` の機能パネル、下: ログ `QTextEdit`）。パネルとワーカーを **Qt シグナル/スロットだけでつなぐ**配線ハブ。UI 更新は **20 Hz に間引き**（`UI_UPDATE_INTERVAL_S=0.05`）。監視は別ウィンドウ（`MonitoringWindow`） |
| バックエンド | `backend/arm_worker.py` | `QThread`。**50 Hz のポーリングループ**＋**`queue.Queue` のコマンドキュー**（`submit_command("enable")` のように文字列コマンドを入れ、`_execute_command` で振り分け）。重い問い合わせは 1/10（約 5 Hz）に間引き。`pyqtSignal` は joints / velocities / efforts / status / motor_feedback / error / connected / enabled / log / can_fps / scan_result など。**シミュレーションモード**（1 次遅れの模擬）あり |
| | `backend/data_buffer.py` | numpy の固定長リングバッファ（500 サンプル × 7 ch × pos/vel/torque/temp）、`get_data()` で時系列順に取り出し、`export_csv()` |
| | `backend/calibration_worker.py` | 多姿勢でトルクを集め、Pinocchio で慣性同定。hpp-fcl で自己干渉チェック、JSONL に逐次保存して**中断から再開**できる |
| | `backend/trajectory_recorder.py` | ティーチングの記録・再生（dataclass → JSON） |
| ウィジェット | `widgets/toolbar_panel.py` | バックエンド選択（**SocketCAN / SLCAN**）、CAN IF と bitrate（1M/500K/250K）、シリアルポートとボーレート（2M〜115.2K）、SLCAN の CAN bitrate（1M〜125K）、接続・有効化・非常停止・状態表示、テーマ/言語の切替 |
| | `widgets/monitoring_panel.py` | **pyqtgraph** の `PlotWidget` を 2×2（位置・速度・トルク・温度）、7 ch の色分け、QTimer 20 Hz で再描画、Pause / Clear / CSV Export |
| | `widgets/diagnostics_panel.py` | モータ状態表（7×8）、**モータスキャン**（ID 1–7 に対して FW バージョンと電圧を問い合わせ、オンライン判定）、パラメータ読み書き（motor_id、index、値）、ゼロ設定、zero_sta の検証と一括設定（type 22 で保存）、CAN 統計 |
| | `widgets/joint_control_panel.py` | 関節ごとのスライダ＋`QDoubleSpinBox` |
| | `widgets/viewer_3d.py` | **PyVista / pyvistaqt `QtInteractor`（VTK）** で URDF と STL を表示、`vtkCellPicker` で**リンクをドラッグして関節角を操作**。pyvista がなければ 3D を無効にして続行 |
| | その他 | trajectory（MoveJ/MoveL）、teaching（ゼロトルクや重力補償つきの手動ティーチング）、gripper、gamepad（20 Hz ポーリング）、calibration |
| ユーティリティ | `utils/i18n.py` | キーを引くと `{"zh","en"}` を返す辞書と `tr()`、`retranslate_ui()` で実行中に言語を切り替え |
| | `utils/theme_manager.py` / `style.py` | シングルトンの `QObject` がシグナル（theme_changed / language_changed）を出す。ダーク/ライトの QSS（775 行） |
| | `utils/can_utils.py` | SocketCAN IF の検出・状態・bitrate、`ip link` の特権実行、シリアルポート検出 |
| | `utils/urdf_loader.py`, `joint_drag_controls.py` | 軽量な URDF パーサ＋FK、ドラッグ角度の計算 |
| SDK | `el_a3_sdk/can_driver.py`, `slcan_can_driver.py`, `protocol.py` | RobStride プライベートプロトコルのドライバ（enable / disable(clear_fault) / set_zero / motion_control / read・write_parameter / set_run_mode / save_parameters / query_firmware_version / fault_detail / can_fps / bus_health）。**SLCAN（Lawicel `T<id><dlc><data>\r`）版と SocketCAN 版が同じ公開 API を持つ** |

依存: `pyqt6`, `pyqtgraph`, `pyvista`, `pyvistaqt`, `numpy<2`, `pyyaml`, `pyserial`（slcan/windows）、`pin`（dynamics、任意）（`el_a3_sdk/setup.py`）。

### 6.2 単一モータツールに流用できるアイデア

1. **I/O スレッドと UI の分離**: `QThread` ワーカー＋コマンドキュー＋シグナルによる通知、UI 更新の間引き。この骨格はそのまま使える。ただし 50 Hz ポーリングはオシロには遅いので、受信スレッドで**すべての type 2 / type 17 フレームにタイムスタンプを付けてリングバッファへ入れ**、描画だけを 20–60 Hz にするのがよい。
2. **トランスポートの抽象化**: SocketCAN / SLCAN / (公式 AT-USB-CAN) を同じ API のドライバで差し替える。**EL05 用には公式 USB-CAN の AT フレーム（41 54 … 0D 0A、921600 bps）ドライバを追加**すれば、公式アダプタもそのまま使える。
3. **シミュレーションモード**: ハードなしで UI やワークフローを開発・テストできる。
4. **リングバッファ＋CSV Export、pyqtgraph の多ペイン表示、Pause/Clear**: オシロ機能の土台になる。チャネルを任意のパラメータにできるようにすれば、公式 (B) の 8ch セレクタより柔軟になる。
5. **スキャン＋診断パネル**: ID 範囲をスキャンして FW と電圧を表示し、パラメータを読み書きし、zero_sta の検証と保存を行う流れは、単一モータでも ID 変更や故障解析の画面として使える。
6. **i18n（`tr()` と retranslate）とテーマ管理のシングルトン**: 中・英・日の 3 言語にするのは容易。
7. **ドッキング型のメインウィンドウとタブ型の機能パネル**: 機能をプラグイン（タブ）として追加でき、拡張しやすい。
8. **PyInstaller 前提のリソースパス解決**: Windows 向け配布に使える。
9. 3D ビューア（PyVista）は単一モータには過剰。必要なら出力軸の角度を示す 2D ダイヤルや、STEP（`el05.stp`）からメッシュを作った簡易 3D 表示にとどめる。
10. 注意点: コマンドを文字列の if-elif で振り分けているので拡張性が低い。自作ツールでは Command オブジェクトやレジストリ方式にする。書込み結果の確認（読み戻し）やエラーの型付けも弱いので強化する。
