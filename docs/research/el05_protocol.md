# RobStride EduLite 05 (EL05) 通信プロトコル仕様

GUI 制御ツール実装用。本書だけで独自（私有）プロトコル・MIT プロトコル・CANopen 切替を実装できることを目標にまとめた。
ハードウェア仕様は [el05_spec.md](./el05_spec.md) を参照。

## 0. 出典と表記ルール

| 略号 | ファイル | 版 |
|---|---|---|
| **EN** | `docs/vendor/el05/EL05User Manual260713.pdf` | v1.0 (2025-11-25) |
| **CN** | `docs/vendor/el05/EL05使用说明书260713.pdf` | **v1.1 (2026-04-10)** ← 新しい。**食い違いは CN 優先** |
| REF-MD | `docs/vendor/reference_code/el_a3_sdk/docs/电机通信协议汇总.md` | 二次資料 |
| SDK | `docs/vendor/reference_code/el_a3_sdk/el_a3_sdk/{protocol.py,can_driver.py,utils.py}` | |
| PYS | `docs/vendor/reference_code/python_sample/robstride_dynamics/{protocol.py,table.py,bus.py}` | |
| ROS | `docs/vendor/reference_code/el_a3_ros_driver/robstride_can_driver.{hpp,cpp}` | |
| USBCONV | `docs/vendor/reference_code/can_usb_conversion/mainwindow.cpp` | |

- ページ番号は PDF フッタに印刷された番号（EN = PDF ページ − 1、CN = PDF ページ − 2）。
- 16 進は `0x..`。バイト列は送信順に左から `B0 B1 ... B7`。
- 「（推定）」はマニュアルに明記がなく、他の記述・参考コードから推定したもの。

---

## 1. CAN 物理層・フレーム構成

### 1.1 物理設定（EN p.39 / CN p.29, EN p.12 / CN p.9）

| 項目 | 値 |
|---|---|
| 規格 | CAN 2.0（クラシック CAN。CAN FD の記載なし） |
| ビットレート | **1 Mbps（既定）**。type 23 で 500k / 250k / 125k に変更可（再投入後有効） |
| フレーム | 独自プロトコル: **29bit 拡張フレーム**、DLC は常に 8 |
| | MIT プロトコル: 11bit 標準フレーム |
| | CANopen: 標準 CANopen（11bit）。切替フレームのみ拡張 |
| ファーム更新時 | 1 Mbps 必須（CN p.13） |

### 1.2 29bit 拡張 ID のビット割当（EN p.39 / CN p.29）

```
 bit 28..24 | bit 23..8 (16bit)          | bit 7..0
 通信タイプ  | データ領域2 (data area 2)   | 宛先アドレス
 (5bit)     |                            | (8bit)
```

```
CAN_ID = (comm_type & 0x1F) << 24 | (data_area2 & 0xFFFF) << 8 | (dest & 0xFF)
```

データ領域 2 の使い方（通信タイプ依存）:

| 方向 | bit 23..16 | bit 15..8 | bit 7..0 |
|---|---|---|---|
| ホスト→モータ（一般） | 0x00（type 7 では新 CAN ID） | **ホスト CAN ID** | **モータ CAN ID** |
| ホスト→モータ（type 1） | ← フィードフォワードトルク u16（bit23..8 全体） → | | モータ CAN ID |
| モータ→ホスト（type 2/24） | bit23..22 = モード、bit21..16 = 故障 | **モータ CAN ID** | **ホスト CAN ID** |
| モータ→ホスト（type 17） | 0x00 = 読出成功 / 0x01 = 失敗 | モータ CAN ID | ホスト CAN ID |
| モータ→ホスト（type 21） | 0x00 | モータ CAN ID | ホスト CAN ID |
| モータ→ホスト（type 0 応答） | 0x00（推定） | モータ CAN ID | **0xFE** |

### 1.3 ID の慣例

| 項目 | 値 | 出典 |
|---|---|---|
| ホスト ID | マニュアル例は **0xFD**（MotorStudio も 0xFD） | EN p.48–49, p.29–30 / CN p.20, p.36 |
| モータ CAN ID 範囲 | 0–127（0x200A `CAN_ID` の Max 127）、工場出荷 1 | EN p.21 / CN p.16 |
| CAN_MASTER（flash, 0x200B） | 0–127、参考値 0 | EN p.21 |
| type 0 応答の宛先 | 0xFE 固定 | EN p.39 / CN p.29 |
| マニュアル例のモータ ID | 0x7F | 各表 |

- PYS はホスト ID 0xFF（「ホスト ID はアクチュエータ ID より大きい方が良い」とコメント）。SDK/ROS は 0xFD。**GUI 既定は 0xFD**。
- モータ→ホストのフレームは宛先（bit7..0）が自分のホスト ID のものを受理し、モータ ID は **bit15..8** から取ること（SDK/ROS も同じ）。

### 1.4 公式 USB-CAN モジュール（AT モード）のシリアル形式（EN p.13, p.29–30 / CN p.10, p.20）

```
41 54 | ID3 ID2 ID1 ID0 | DLC | D0 .. D7 | 0D 0A
"AT"  | (CAN_ID29 << 3) | 0x04 をビッグエンディアン 4 バイト | 8 | データ | CR LF
```

- 例（EN p.29 / CN p.20）: `41 54 90 07 E8 0C 08 05 70 00 00 01 00 00 00 0D 0A`
  - `0x9007E80C` → `& ~0x4` して `>> 3` → `0x1200FD01` = type 18、ホスト 0xFD、モータ 1
  - データ `05 70 00 00 01 00 00 00` = index 0x7005 (run_mode) ← 1 (PP)
- `0x04` は IDE（拡張）ビット。STM32 bxCAN の TIxR レジスタ配置（EXID = bit31..3, IDE = bit2, RTR = bit1）と一致（USBCONV の実装も同じ）。
- **標準フレーム（MIT モード用）の AT 形式はマニュアルに記載なし。** bxCAN 配置に従えば `STID << 21`、IDE=0 と推定。要実機確認。
- 115200 等のシリアル設定・初期化コマンドも記載なし（CH340、既定 AT モードとのみ記載）。

### 1.5 スケーリング共通式（EN p.54–55 / CN p.40–41 サンプルコード）

```c
// 送信: float → unsigned int
u = (int)((clamp(x, x_min, x_max) - x_min) * ((1 << bits) - 1) / (x_max - x_min));
// 受信: unsigned int → float
x = u * (x_max - x_min) / ((1 << bits) - 1) + x_min;
```

**EL05 のレンジ定数（CN p.40 のマクロ。EN p.54–55 の T_MIN/T_MAX = ±5.5 は RS05 からのコピーミス）**

| 定数 | EL05 値 | 備考 |
|---|---|---|
| P_MIN / P_MAX | **−12.57 / +12.57 rad**（≈ ±4π） | 「type 2 変更」で ±4π 周期ループ（EN p.50 / CN p.37） |
| V_MIN / V_MAX | **−50 / +50 rad/s** | |
| KP_MIN / KP_MAX | **0 / 500** | |
| KD_MIN / KD_MAX | **0 / 5** | |
| T_MIN / T_MAX | **−6 / +6 N·m** | CN p.40、EN/CN 表 p.40/p.30 も ±6 |

- 0 N·m / 0 rad / 0 rad/s は 16bit で `0x7FFF`（32767）。**トルク 0 を `0x0000` にすると −6 N·m 指令になる**ので注意（CN p.30 の例 `00 00` は単なるプレースホルダ）。

---

## 2. 独自（私有）プロトコル 通信タイプ一覧

| type | hex | 名称 | 方向 | 応答 | 出典 |
|---|---|---|---|---|---|
| 0 | 0x00 | デバイス ID / MCU UID 取得 | H→M | type 0（宛先 0xFE, 64bit UID） | EN p.39 / CN p.29 |
| 1 | 0x01 | 運控（Operation control）指令 | H→M | type 2 | EN p.40 / CN p.29–30 |
| 2 | 0x02 | モータフィードバック | M→H | — | EN p.40–41 / CN p.30 |
| 3 | 0x03 | モータ有効化（Enable/Run） | H→M | type 2 | EN p.41 / CN p.30 |
| 4 | 0x04 | モータ停止（Disable）/ 故障クリア / バージョン読出 | H→M | type 2 | EN p.41–42 / CN p.31 |
| 6 | 0x06 | 機械ゼロ点設定 | H→M | type 2 | EN p.42 / CN p.31 |
| 7 | 0x07 | CAN ID 変更（即時有効） | H→M | type 0 | EN p.42 / CN p.32 |
| 17 | 0x11 | 単一パラメータ読出 | H↔M | type 17 | EN p.43 / CN p.32 |
| 18 | 0x12 | 単一パラメータ書込（電源断で消失） | H→M | type 2 | EN p.43 / CN p.32–33 |
| 21 | 0x15 | 故障フィードバック | M→H | — | EN p.44 / CN p.33 |
| 22 | 0x16 | パラメータ保存（flash） | H→M | type 2 | EN p.44 / CN p.33–34 |
| 23 | 0x17 | ボーレート変更（再投入後有効） | H→M | type 0 | EN p.45 / CN p.34 |
| 24 | 0x18 | 主動上報 ON/OFF | H→M | 上報フレーム（§2.14） | EN p.45–46 / CN p.34–35 |
| 25 | 0x19 | プロトコル切替（再投入後有効） | H→M | type 0 | EN p.46 / CN p.35 |
| (26) | 0x1A | バージョン読出（**参考コードのみ**。EL05 マニュアルは type 4 を使用） | H→M | type 2 | REF-MD / SDK |

type 5, 8–16, 19, 20 はマニュアルに記載なし。

以下、ID 例は **ホスト 0xFD、モータ 0x7F** とする。

### 2.1 Type 0: デバイス ID 取得（EN p.39 / CN p.29）

| | ID | データ |
|---|---|---|
| 要求 | `0x0000FD7F`（bit15..8 = ホスト ID） | `00 00 00 00 00 00 00 00` |
| 応答 | `0x00007FFE`（bit15..8 = モータ ID, bit7..0 = **0xFE**） | B0–B7 = **64bit MCU 固有 ID** |

- UID のバイト順は記載なし（表示用に B0..B7 をそのまま 16 進表示すればよい）。
- ID スキャン: モータ ID 1–127 に順に送信し、type 0 応答を待つ（PYS `scan_channel` と同方式）。

### 2.2 Type 1: 運控モード制御指令（EN p.40 / CN p.29–30, p.38）

| フィールド | 位置 | 形式 | レンジ |
|---|---|---|---|
| フィードフォワードトルク t_ff | **ID bit23..8** | u16 | 0–65535 ↔ −6 ～ +6 N·m |
| 目標角度 p_set | B0–B1 | u16 **BE** | 0–65535 ↔ −12.57 ～ +12.57 rad |
| 目標角速度 v_set | B2–B3 | u16 BE | 0–65535 ↔ −50 ～ +50 rad/s |
| Kp | B4–B5 | u16 BE | 0–65535 ↔ 0 ～ 500 |
| Kd | B6–B7 | u16 BE | 0–65535 ↔ 0 ～ 5 |

- 応答: type 2。
- 制御則（EN p.52 / CN p.38）: `t_ref = Kd·(v_set − v_actual) + Kp·(p_set − p_actual) + t_ff` → 内部で iq 指令に換算し電流ループで出力。
- 例: p=0, v=0, Kp=1, Kd=1, t_ff=0 → ID `0x017FFF7F`, データ `7F FF 7F FF 00 83 33 33`
- マニュアルの動作例（EN p.52 / CN p.38）:
  - t_ff=0, v=1, Kd=1, p=0, Kp=0 → 無負荷で 1 rad/s 回転（負荷ありなら Kd を上げる）
  - t_ff=0, v=0, Kd=1, p=0, Kp=0 → ダンピングモード（**外力で回すと発電 → 過電圧注意**）
  - t_ff=0, v=0, Kd=1, p=5, Kp=1 → 位置 5 rad へ移動（Kd なしだと振動）
- EN の「Byte2: Torque」は誤訳。CN は「bit23~8: 力矩」。

### 2.3 Type 2: フィードバックフレーム（EN p.40–41 / CN p.30）

**ID**

| bit | 内容 |
|---|---|
| 28..24 | 0x02 |
| 23..22 | モード状態: **0 = Reset（停止/リセット）, 1 = Cali（校正）, 2 = Motor（運転）** |
| 21 | 未校正（uncalibrated） |
| 20 | ストール過負荷故障（堵转过载） |
| 19 | 磁気エンコーダ故障 |
| 18 | 過温 |
| 17 | 三相電流故障（過電流） |
| 16 | 低電圧故障 |
| 15..8 | **モータ CAN ID** |
| 7..0 | ホスト CAN ID |

**データ（全フィールド ビッグエンディアン。CN「以上数据高字节在前，低字节在后」）**

| B | 内容 | 変換 |
|---|---|---|
| B0–B1 | 現在角度 u16 | `u·25.14/65535 − 12.57` rad |
| B2–B3 | 現在角速度 u16 | `u·100/65535 − 50` rad/s |
| B4–B5 | 現在トルク u16 | `u·12/65535 − 6` N·m |
| B6–B7 | 温度 ×10 | `u/10` ℃（モータ巻線/NTC 温度と推定。負温度の扱いは記載なし → int16 として解釈推奨） |

- 角度は **±4π の周期ループ**（「type 2 変更」EN p.50 / CN p.37）。マルチターン表示は GUI 側で unwrap（前回値との差が 4π を超えたら ∓8π≒25.14 を加算）。正確な多回転位置は 0x7019 `mechPos`（float, 負荷端・回転数込み）を type 17 で読む。
- 電源投入時の角度原点範囲は `zero_sta`（0x7029）に依存: 0 → 0～2π、1 → −π～π。
- EN の故障ビット表は「bit20: Uncalibrated」が重複する誤記。CN が正（上表）。
- 送信契機: type 1/3/4/6/18/22 への応答、および主動上報（§2.14）。

**type 2 故障フィールド（bit21..16）の正規化（ID >> 16 & 0x3F）**

| bit (シフト後) | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| 意味 | 低電圧 | 三相電流 | 過温 | 磁気エンコーダ | ストール過負荷 | 未校正 |

### 2.4 Type 3: 有効化（EN p.41 / CN p.30）

| ID | データ | 応答 |
|---|---|---|
| `0x0300FD7F` | `00 ×8` | type 2（モード = 2 になる） |

- マニュアルのサンプル `motor_enable()` は data を host id にした直後に 0 で上書きしている（EN p.56 / CN p.41）が、他の全コマンドと同様 bit15..8 = ホスト ID とすべき。

### 2.5 Type 4: 停止 / 故障クリア（EN p.41 / CN p.31）

| ID | データ | 意味 |
|---|---|---|
| `0x0400FD7F` | `00 00 00 00 00 00 00 00` | 停止（通常時はデータ領域を 0 にすること） |
| `0x0400FD7F` | `01 00 00 00 00 00 00 00` | **故障クリア**（Byte[0]=1） |

応答: type 2（モード = 0 Reset）。

### 2.6 バージョン番号読出フレーム（EN p.41–42 / CN p.31）

| | ID | データ |
|---|---|---|
| 要求（EL05 マニュアル） | **type 4**: `0x0400FD7F` | `00 C4 00 00 00 00 00 00` |
| 応答 | **type 2 の ID**（`0x02xx7FFD`、bit23..16 はモード/故障） | `B0=0x00, B1=0xC4, B2=0x56, B3–B6 = バージョン（上位→下位）`, B7 不明 |

- 注意 1: type 4 を使うため**運転中に送ると停止する可能性**がある。停止状態でのみ送ること。
- 注意 2: 応答は type 2 と同じ ID なので、`B0..B2 == 00 C4 56` で判別する（送信直後の時間窓内に限定）。
- 注意 3: REF-MD/SDK は同じデータを **type 26 (0x1A)** で送る（`0x1A00FD7F`）。SDK の解析は `data[2:7]`（0x56 を含む 5 バイト）を版数としており、マニュアルの `B3–B6` と 1 バイトずれる。
- **推奨**: まず type 4 方式（EL05 マニュアル準拠）で試し、応答が無ければ type 26 を試す。版数表記（例: 1.0.5.0.4 のような 5 桁）と 4 バイトの対応は要実機確認。

### 2.7 Type 6: 機械ゼロ点設定（EN p.42, p.50 / CN p.31, p.37）

| ID | データ | 応答 |
|---|---|---|
| `0x0600FD7F` | `01 00 00 00 00 00 00 00` | type 2 |

- **CSP と運控モードでのみ有効。PP モードでは無視される。**
- 新ファームでは CSP/運控でゼロ設定すると目標位置も即 0 に更新され、モータは動かない。旧ファームでは目標と実位置に大差が生じて**急に動く**ことがある → GUI は「停止状態でのみゼロ設定」を推奨。
- ゼロ点が flash に自動保存されるか、type 22 が必要かは記載なし（要実機確認）。

### 2.8 Type 7: CAN ID 変更（EN p.42 / CN p.32）

| ID | データ | 応答 |
|---|---|---|
| `0x07 <新ID> <ホスト> <旧ID>` 例: 0x7F → 0x01 は `0x0701FD7F` | `00 ×8` | type 0（新 ID から、UID 付き） |

- 即時有効。保存（type 22）が必要かは記載なし → 変更後に新 ID 宛で type 22 を送ることを推奨（要確認）。
- PYS `write_id` も `extra = (new_id << 8) | host_id` で同じ。

### 2.9 Type 17: 単一パラメータ読出（EN p.43, p.48–49 / CN p.32, p.36）

| | ID | データ |
|---|---|---|
| 要求 | `0x1100FD7F` | `B0–B1 = index (LE)`, `B2–B3 = 00 00`, `B4–B7 = 00` |
| 応答 | `0x11 <状態> <モータID> <ホスト>` 例 `0x11007FFD` | `B0–B1 = index (LE)`, `B2–B3 = 00`, `B4–B7 = 値（LE。1 バイト型は B4）` |

- 応答 bit23..16: **0x00 = 成功, 0x01 = 失敗**。
- 例（loc_kp 読出）: 要求 `1E 70 00 00 00 00 00 00` → 応答 `1E 70 00 00 00 00 F0 41`（= 30.0f）。
- 型に応じて B4–B7 を解釈すること（uint8 → B4、uint16 → B4–B5 LE、uint32/float → B4–B7 LE）。SDK は常に float として解釈しておりバグ。
- 読出対象は §5.2 の 0x70xx 表（「可读写单个参数列表」）。0x0000–0x30xx の上位機パラメータ表を type 17 で読めるかは明記なし（§8 参照）。

### 2.10 Type 18: 単一パラメータ書込（EN p.43 / CN p.32–33）

| ID | データ | 応答 |
|---|---|---|
| `0x1200FD7F` | `B0–B1 = index (LE)`, `B2–B3 = 00 00`, `B4–B7 = 値 (LE)` | type 2 |

- 電源断で消失。type 22 で保存できるのは **0x20xx の上位機パラメータ、および 0x702A, 0x702B**（CN p.32。EN は 0x20 のみ記載）。zero_sta (0x7029) 変更も type 22 保存が必要（CN p.37）。
- uint8 パラメータ（run_mode 等）は **B4 に整数値**を入れる（float で書かないこと。SDK のコメント参照）。
- 例: run_mode ← 5（CSP）: `05 70 00 00 05 00 00 00`
- 例: loc_ref ← 1.0 rad: `16 70 00 00 00 00 80 3F`
- 例: limit_spd ← 5.0 rad/s: `17 70 00 00 00 00 A0 40`

### 2.11 Type 21: 故障フィードバック（EN p.44 / CN p.33）

| ID | データ |
|---|---|
| `0x15 00 <モータID> <ホスト>` | `B0–B3 = fault (uint32)`, `B4–B7 = warning (uint32)`（バイト順記載なし。PYS/SDK は LE） |

fault ビット（0 = 正常、非 0 = 故障）:

| bit | 内容 |
|---|---|
| 16 | A 相電流サンプリング過電流 |
| 14 | ストール過負荷アルゴリズム保護 |
| 9 | 位置初期化故障 |
| 8 | ハードウェア識別故障 |
| 7 | エンコーダ未校正 |
| 5 | C 相電流サンプリング過電流 |
| 4 | B 相電流サンプリング過電流 |
| 3 | 過電圧（> 60 V） |
| 2 | 低電圧（< 12 V） |
| 1 | ドライバ IC 故障 |
| 0 | モータ過温（サーミスタ > 135 ℃） |

warning ビット: bit0 = モータ過温警告（既定 135 ℃）。

- 上記と同じビット定義は 0x3022 `faultSta` / 0x3023 `warnSta`（EN p.30 / CN p.20–21）および MIT の故障応答（§4.8）でも使われる。
- 送信契機（自発か要求応答か）はマニュアルに明記なし。PYS は任意コマンドの応答として受信し得る前提。
- EN の表は bit14 を 2 回書くなど乱れている。CN が正。

### 2.12 Type 22: パラメータ保存（EN p.44 / CN p.33–34）

| ID | データ | 応答 |
|---|---|---|
| `0x1600FD7F` | `01 02 03 04 05 06 07 08` | type 2 |

- 停止状態で送ることを推奨（パラメータ表更新は待機状態で行う旨、EN p.19 / CN p.15）。

### 2.13 Type 23: ボーレート変更（EN p.45 / CN p.34）

| ID | データ | 応答 |
|---|---|---|
| `0x1700FD7F` | `01 02 03 04 05 06 F_CMD 00`（**F_CMD = B6**） | type 0 |

| F_CMD | ボーレート |
|---|---|
| 0x01 | 1 Mbps |
| 0x02 | 500 kbps |
| 0x03 | 250 kbps |
| 0x04 | 125 kbps |

- **再投入後に有効。** ホスト側のビットレートも合わせて変更すること。flash の 0x2009 `motor_baud`（参考値 1）が対応すると推定。
- MIT プロトコル中はボーレート変更不可（独自プロトコルに戻して変更、EN p.70 / CN p.51）。

### 2.14 Type 24: 主動上報（EN p.45–46, p.49 / CN p.34–36）

| ID | データ |
|---|---|
| `0x1800FD7F` | `01 02 03 04 05 06 F_CMD 00`（F_CMD = B6: **0x00 = OFF（既定）, 0x01 = ON**） |

- ON 時の既定周期 10 ms。周期は 0x7026 `EPScan_time`（uint16）で変更: **周期 = 10 ms + N × 5 ms**（実機測定: N=0 → 10 ms, N=1 → 15 ms ...。マニュアルの「1 = 10 ms」は誤り。§8.0）。MotorStudio の最小は 10 ms（EN p.16）。
- 上報フレーム: §4.2.1 では「type 2」、type 24 の表では **ID の通信タイプ = 0x18** と記載（CN p.34–35）。データ/ID レイアウトは type 2 と同一（モード、故障、モータ ID、ホスト ID、角度・速度・トルク・温度）。**GUI は type 2 と 0x18 の両方を同じデコーダで受理すること。**

### 2.15 Type 25: プロトコル切替（EN p.46, p.50 / CN p.35, p.37）

| ID | データ | 応答 |
|---|---|---|
| `0x1900FD7F` | `01 02 03 04 05 06 F_CMD 00`（F_CMD = B6） | type 0 |

| F_CMD | プロトコル |
|---|---|
| 0 | 独自（私有）プロトコル（既定） |
| 1 | CANopen |
| 2 | MIT |

- **再投入後に有効。** flash の 0x2022 `protocol_1` でも変更可（上位機から）。
- 戻し方: MIT → 標準フレームの「指令 8」（§4.11）、CANopen → 拡張フレーム ID `0xFFF` の切替フレーム（§6.6）。

---

## 3. 制御モード（run_mode）と操作シーケンス

### 3.1 run_mode（0x7005, uint8）（EN p.46 / CN p.35）

| 値 | モード | 指令パラメータ | 制限パラメータ |
|---|---|---|---|
| **0** | 運控モード（Operation control / MIT 相当 5 パラメータ）**電源投入時の既定** | type 1 フレーム | limit_torque（推定） |
| **1** | 位置モード PP（Profile Position, 台形/T カーブ計画） | loc_ref (0x7016) | vel_max (0x7024), acc_set (0x7025), dcc_set (0x702E) |
| **2** | 速度モード | spd_ref (0x700A) | limit_cur (0x7018), acc_rad (0x7022) |
| **3** | 電流モード | iq_ref (0x7006) | — |
| **5** | 位置モード CSP（Cyclic Synchronous Position） | loc_ref (0x7016) | limit_spd (0x7017), limit_cur（推定） |

- 4 は未定義。EN の表は「3: Operation mode Current mode」と崩れているが CN は「3: 电流模式」。
- **運転中にモードを切り替えないこと。必ず type 4 で停止してから切替**（EN p.1 注意事項 2）。

### 3.2 共通前処理（GUI 推奨手順）

1. type 0 で存在確認・UID 取得。
2. type 4（B0=0）で停止 → type 2 のモード = 0 を確認。
3. 故障ビットがあれば type 4（B0=1）でクリア → 再確認。
4. type 18 で run_mode を書く。
5. type 3 で有効化 → type 2 のモード = 2 を確認。
6. モード別の制限値 → 指令値を type 18 で書く（下記）。
7. 停止は type 4。

### 3.3 モード別シーケンス（EN p.51–54 / CN p.38–39）

**運控モード (0)**
1. （電源投入直後は既に mode 0）type 3 有効化
2. type 1 を周期送信（応答 type 2）
3. type 4 停止

**電流モード (3)**
1. type 18: run_mode ← 3
2. type 3 有効化
3. type 18: iq_ref ← 目標電流（−11 ～ 11 A）

**速度モード (2)**
1. type 18: run_mode ← 2
2. type 3 有効化
3. type 18: limit_cur ← 最大電流（0 ～ 11 A）
4. type 18: acc_rad ← 加速度（既定 20 rad/s²）
5. type 18: spd_ref ← 目標速度（−50 ～ 50 rad/s）

**位置モード CSP (5)**
1. type 18: run_mode ← 5
2. type 3 有効化
3. type 18: limit_spd ← 最大速度（0 ～ 50 rad/s）
4. type 18: loc_ref ← 目標位置（rad）を逐次送信

**位置モード PP (1)**
1. type 18: run_mode ← 1
2. type 3 有効化
3. type 18: vel_max ← 最大速度（既定 10 rad/s）
4. type 18: acc_set ← 加速度（既定 10 rad/s²）
5. type 18: loc_ref ← 目標位置
- **運転中の速度・加速度変更は不可。** 緊急停止は動作中に vel_max ← 0（現在の速度・加速度計画で停止）。
- MotorStudio: 速度 0 を送ると現在位置で停止、再開は速度と位置を再送（EN p.36 / CN p.26）。
- 減速度個別設定: `acc_stutus` を 1 にすると加速度と減速度を分離し、減速度は 0x702E `dcc_set`。0 のとき acc_set が加減速両方（EN p.51 / CN p.37–38）。**acc_stutus の index は記載なし**（0x702A の可能性、要確認）。

**GUI 実装上の注意（推定を含む）**
- CSP/PP で有効化直後に loc_ref の旧値（既定 0）へ向かって動く可能性がある。有効化前後に「現在位置（0x7019 mechPos もしくは type 2 角度）を loc_ref に書く」ことを推奨。停止中の書込みが保持されるかは要実機確認。
- 制限値はマニュアル順（有効化後）に書くが、指令値より必ず先に送る。

---

## 4. MIT プロトコル（type 25 / 0x2022 で F_CMD=2 に切替後、再投入）（EN p.70–78 / CN p.51–56）

### 4.1 フレーム構成

- CAN 2.0 **11bit 標準フレーム**、1 Mbps 既定（ボーレートは独自プロトコルに戻して変更）。
```
 bit 10..8 : モード種別（コマンド種別）
 bit  7..0 : ID（送信時はモータ CAN ID、応答1は主機 ID）
 STD_ID = (kind << 8) | id
```

| kind | 用途 | 例 ID（モータ 0x7F） |
|---|---|---|
| 0 | 特殊コマンド（有効化・停止・MIT 動的パラメータ・ゼロ・クリア・モード設定・ID 変更・プロトコル変更・主機 ID・保存・上報） | `0x07F` |
| 1 | 位置モード制御（指令 10） | `0x17F` |
| 2 | 速度モード制御（指令 11） | `0x27F` |
| 3 | パラメータ読出（指令 14） | `0x37F` |
| 4 | パラメータ書込（指令 15） | `0x47F` |

### 4.2 応答指令 1: 状態フィードバック（EN p.70–71 / CN p.51）

ID = **主機 ID（既定 0x0FD）**

| B | 内容 |
|---|---|
| B0 | モータ CAN ID |
| B1–B2 | 角度 u16（B1 上位）: 0–65535 ↔ −12.57 ～ 12.57 rad |
| B3, B4[7:4] | 速度 u12 = `B3<<4 \| B4>>4`: 0–4095 ↔ −50 ～ 50 rad/s |
| B4[3:0], B5 | トルク u12 = `(B4&0x0F)<<8 \| B5`: 0–4095 ↔ −6 ～ 6 N·m |
| B6[7:6] | モード 0 = Reset, 1 = Cali, 2 = Motor |
| B6[5] | 1 = 故障あり |
| B6[4] | 1 = 警告あり |
| B6[3:0], B7 | 巻線温度 u12 = `(B6&0x0F)<<8 \| B7`、℃ × 10 |

- マニュアルは「[0~4096]」と書くが 12bit なので 0–4095（スケール分母は 4095）。
- REF-MD は温度を「Byte6~7」と簡略化しており、B6 上位のモード/故障/警告ビットを無視している → 誤り。

### 4.3 応答指令 2: MCU 識別（EN p.71 / CN p.51）

ID = モータ ID、データ = 64bit MCU 固有 ID。

### 4.4 kind 0 特殊コマンド一覧（ID = `0x000 | motor_id`）

| 指令 | 機能 | データ B0..B7 | 応答 | 出典 |
|---|---|---|---|---|
| 1 | 有効化 | `FF FF FF FF FF FF FF FC` | 応答 1 | EN p.71 / CN p.51–52 |
| 2 | 停止 | `FF FF FF FF FF FF FF FD` | 応答 1 | EN p.71 / CN p.52 |
| 4 | ゼロ点設定（位置モード以外） | `FF FF FF FF FF FF FF FE` | 応答 1 | EN p.72 / CN p.52 |
| 5 | 故障クリア / 故障読出 | `FF FF FF FF FF FF F_CMD FB`（F_CMD=0xFF: クリア、その他: 故障値を返す） | クリア時 応答 1 / 読出時 §4.8 | EN p.72 / CN p.52 |
| 6 | 運転モード設定 | `FF FF FF FF FF FF F_CMD FC`（0 = MIT（既定）, 1 = 位置(CSP), 2 = 速度） | 応答 1 | EN p.73 / CN p.53 |
| 7 | モータ CAN ID 変更 | `FF FF FF FF FF FF <新ID> FA` | 応答 2 | EN p.73 / CN p.53 |
| 8 | プロトコル変更（再投入後有効） | `FF FF FF FF FF FF F_CMD FD`（0 = 独自, 1 = CANopen, 2 = MIT） | 応答 2 | EN p.73–74 / CN p.53–54 |
| 9 | 主機 CAN ID 変更 | `FF FF FF FF FF FF <主機ID> 01` | 応答 2 | EN p.74 / CN p.54 |
| 12 | パラメータ保存（FW ≥ 1.0.5.0.4） | `FF FF FF FF FF FF FF F8` | 応答 2 | EN p.75 / CN p.54 |
| 13 | 主動上報（FW ≥ 1.0.5.0.4） | `FF FF FF FF FF FF F_CMD F9`（0 = OFF 既定, 1 = ON） | 応答 1 | EN p.75 / CN p.54–55 |

**重要な衝突**: 指令 1/6 はともに末尾 `FC`、指令 2/8 はともに末尾 `FD`。**B6 = 0xFF なら有効化/停止、B6 = 0x00–0x02 ならモード設定/プロトコル変更**になる。停止のつもりで `... 00 FD` を送ると「独自プロトコルへ切替」になるので、GUI は定数フレームを厳密に使うこと。

### 4.5 指令 3: MIT 動的パラメータ（EN p.71–72 / CN p.52）

ID = `0x000 | motor_id`

| フィールド | ビット配置 | レンジ |
|---|---|---|
| 目標角度 u16 | B0（上位）B1（下位） | −12.57 ～ 12.57 rad |
| 目標速度 u12 | `B2<<4 \| B3>>4` | −50 ～ 50 rad/s |
| Kp u12 | `(B3&0x0F)<<8 \| B4` | 0 ～ 500 |
| Kd u12 | `B5<<4 \| B6>>4` | 0 ～ 5 |
| 目標トルク u12 | `(B6&0x0F)<<8 \| B7` | −6 ～ 6 N·m |

- 応答: 応答 1。標準的な mini-cheetah 形式と同一のビット配置。
- 例: p=0, v=0, Kp=1, Kd=1, τ=0 → `7F FF 7F F0 08 33 37 FF`
- データが偶然 `FF FF FF FF FF FF FF Fx` になる組合せ（角度・速度・Kp 等すべて最大）は特殊コマンドと衝突するため、GUI は角度を 65534 以下にクランプすると安全（推定上の配慮）。

### 4.6 指令 10: 位置モード制御（EN p.74 / CN p.54）

ID = `0x100 | motor_id`、データ: `B0–B3 = 目標位置 float (rad, LE)`, `B4–B7 = 目標速度 float (rad/s, LE)`。
例: 位置 5 rad・速度 5 rad/s → `00 00 A0 40 00 00 A0 40`。応答 1。

### 4.7 指令 11: 速度モード制御（EN p.75 / CN p.54）

ID = `0x200 | motor_id`、データ: `B0–B3 = 目標速度 float (rad/s, LE)`, `B4–B7 = 電流制限 float (A, LE)`。
例: 5 rad/s・2 A → `00 00 A0 40 00 00 00 40`。応答 1。

### 4.8 故障状態応答（指令 5 で F_CMD ≠ 0xFF）（EN p.72–73 / CN p.53）

ID = 主機 ID（0x0FD）、`B0 = モータ ID`, `B1–B4 = fault 値`（ビット定義は §2.11 と同一、バイト順記載なし → LE と推定）。

### 4.9 指令 14/15: パラメータ読出/書込（FW ≥ 1.0.5.0.4）（EN p.75–76 / CN p.55）

| | ID | データ |
|---|---|---|
| 読出要求 | `0x300 \| motor_id` | `index(LE) 00 00 00 00 00 00` 例 `05 70 00 00 00 00 00 00` |
| 読出応答 | `0x300 \| 7F`（表記上。主機 ID になるかは不明確） | `index(LE) 00 00 値(LE)`（1 バイト型は B4） |
| 書込要求 | `0x400 \| motor_id` | `index(LE) 00 00 値(LE)` 例 `29 70 00 00 ...` |
| 書込応答 | `0x400 \| 7F`（同上） | 要求のエコー |

- パラメータ表は独自プロトコルと共通（§5.2）。

### 4.10 MIT プロトコルでのモード操作（EN p.76–78 / CN p.55–56）

- **MIT モード（既定）**: 指令 1 有効化 → 指令 3 を周期送信 → 指令 2 停止。
- **速度モード**: 指令 6（F_CMD=2）→ 指令 1 → 指令 11（最大電流・目標速度）→ 指令 2。
- **位置モード（CSP）**: 指令 6（F_CMD=1）→ 指令 1 → 指令 10（最大速度・目標位置）→ 指令 2。

### 4.11 独自プロトコルへの戻し方

標準フレーム ID `0x000|motor_id`、データ `FF FF FF FF FF FF 00 FD`（指令 8, F_CMD=0）→ 応答 2 → **電源再投入**。

---

## 5. パラメータ表

### 5.1 属性の凡例

| 表記 (CN / EN) | 意味（推定を含む） |
|---|---|
| 读/写 / Read/Write | 読み書き可 |
| 只读 / Read only | 読出専用（観測値） |
| 配置 / disposition | 構成パラメータ（上位機の青色欄 = モータ内部保存） |
| 设定 / Settings | 設定専用（MotorStudio の専用操作で変更: 校正・ID・ボーレート等） |

- MotorStudio 表示: 青色 = モータ内部保存パラメータ（Current Value 欄で編集可）、水色 = 観測パラメータ（EN p.19 / CN p.15）。
- **「当前值（供参考）」は例示値で、EL05 の実値ではない**可能性が高い（例: limit_torque 17、limit_cur 23、rated_i 8、limit_i 27 は RS02/RS03 系の値。vBus 24195 mV と VBUS 36 V も不整合）。GUI は必ず実機から読み出すこと。
- CN p.15 のスクリーンショット（別ファームと思われる）では 0x2005 MechOffset −50～50、0x2006 chasu_off…、0x2007 status1 −10～10 のように**表と配置が異なる**。ファーム版で 0x20xx の割当が変わる可能性あり。

### 5.2 実行時パラメータ（type 17/18 で読み書き、MIT 指令 14/15 も同じ）（CN p.35–36 / EN p.46–48）

| index | 名称 | 説明 | 型 | Byte | 単位 / 範囲 / 既定 | R/W |
|---|---|---|---|---|---|---|
| 0x7005 | run_mode | 0 運控, 1 PP, 2 速度, 3 電流, 5 CSP | uint8 | 1 | — | W/R |
| 0x7006 | iq_ref | 電流モード Iq 指令 | float | 4 | −11 ～ 11 A | W/R |
| 0x700A | spd_ref | 速度モード 速度指令 | float | 4 | −50 ～ 50 rad/s | W/R |
| 0x700B | limit_torque | トルク制限 | float | 4 | 0 ～ 6 N·m | W/R |
| 0x7010 | cur_kp | 電流ループ Kp | float | 4 | 既定 0.17 | W/R |
| 0x7011 | cur_ki | 電流ループ Ki | float | 4 | 既定 0.012 | W/R |
| 0x7014 | cur_filt_gain | 電流フィルタ係数 | float | 4 | 0 ～ 1.0、既定 0.1 | W/R |
| 0x7016 | loc_ref | 位置モード角度指令（PP/CSP） | float | 4 | rad | W/R |
| 0x7017 | limit_spd | 位置モード (CSP) 速度制限 | float | 4 | 0 ～ 50 rad/s | W/R |
| 0x7018 | limit_cur | 速度・位置モード 電流制限 | float | 4 | 0 ～ 11 A | W/R |
| 0x7019 | mechPos | 負荷端 多回転機械角 | float | 4 | rad | R |
| 0x701A | iqf | iq フィルタ値 | float | 4 | −11 ～ 11 A | R |
| 0x701B | mechVel | 負荷端 回転速度 | float | 4 | −50 ～ 50 rad/s | R |
| 0x701C | VBUS | 母線電圧 | float | 4 | V | R |
| 0x701E | loc_kp | 位置ループ Kp | float | 4 | 既定 40（読出例では 30） | W/R |
| 0x701F | spd_kp | 速度ループ Kp | float | 4 | 既定 6 | W/R |
| 0x7020 | spd_ki | 速度ループ Ki | float | 4 | 既定 0.02 | W/R |
| 0x7021 | spd_filt_gain | 速度フィルタ値 | float | 4 | 既定 0.1 | W/R（EN: W） |
| 0x7022 | acc_rad | 速度モード加速度 | float | 4 | 既定 20 rad/s² | W/R（EN: W） |
| 0x7024 | vel_max | PP モード速度 | float | 4 | 既定 10 rad/s | W/R（EN: W） |
| 0x7025 | acc_set | PP モード加速度 | float | 4 | 既定 10 rad/s² | W/R（EN: W） |
| 0x7026 | EPScan_time | 上報周期。**10 + 5·N ms**(実測。マニュアルは「1 = 10 ms」) | uint16 | 2 | 既定 0(推定。N=0 でも 10 ms) | W/R |
| 0x7028 | canTimeout | CAN タイムアウト閾値。20000 = 1 s（50 µs/count）、0 = 無効 | uint32 | 4 | 既定 0 | W/R |
| 0x7029 | zero_sta | ゼロ点フラグ。0 → 0～2π、1 → −π～π | uint8 | 1 | 既定 0 | W/R |
| 0x702B | add_offset | ゼロ位オフセット | float | 4 | 既定 0 | W/R |
| 0x702C | alveolous_open | コギング補償スイッチ | uint8 | 1 | 既定 0 | W/R |
| 0x702D | iq_test | モータ初期化校正スイッチ | uint8 | 1 | 既定 0 | W/R |
| 0x702E | dcc_set | PP モード減速度（通常は無効） | float | 4 | 既定 10 rad/s² | W/R |

- 表にない index: 0x7007–0x7009, 0x700C–0x700F, 0x7012–0x7013, 0x7015, 0x701D（PYS に `rot_cnt` とコメントアウトあり）, 0x7023, 0x7027, **0x702A**（CN p.32 で type 22 保存対象と記載されるが表に無い）。
- 機能説明（EN p.49–51 / CN p.36–38）:
  - **iq_test**=1: 初期化時間を延ばし初期基準をより正確にする。
  - **コギング校正**: iq_test=1 にして再起動 → 無負荷で上位機からコギング校正 → 成功すると `max_alve` が非 0 → alveolous_open=1 で**運控モードで**有効（max_alve の index は記載なし）。
  - CANopen の ID は独自プロトコルの CAN ID と同一。

### 5.3 上位機パラメータ表（0x0000–0x304E）（CN p.15–19 / EN p.20–28）

アクセス手段（type 17/18 で直接読めるか等）は §8 の未解決事項を参照。

**識別・バージョン（String）**

| index | 名称 | 型 | 属性 | 参考値 | 備考 |
|---|---|---|---|---|---|
| 0x0000 | Name | String | R/W | ÿÿÿ… | |
| 0x0001 | BarCode | String | R/W | ÿÿÿ… | |
| 0x1000 | BootCodeVersion | String | R | 0.1.5 | |
| 0x1001 | BootBuildDate | String | R | Mar 16 2022 | |
| 0x1002 | BootBuildTime | String | R | 20:22:09 | |
| 0x1003 | AppCodeVersion | String | R | 0.0.0.1 | モータプログラム版数 |
| 0x1004 | AppGitVersion | String | R | 7b844b0fM | |
| 0x1005 | AppBuildDate | String | R | Apr 14 2022 | |
| 0x1006 | AppBuildTime | String | R | 20:30:22 | |
| 0x1007 | AppCodeName | String | R | Lingzu_motor | |

**構成パラメータ（0x20xx, flash 保存可）**

| index | 名称 | 型 | 属性 | Max | Min | 参考値 | 備考 |
|---|---|---|---|---|---|---|---|
| 0x2000 | echoPara1 | uint16 | 配置 | 74 | 5 | 5 | |
| 0x2001 | echoPara2 | uint16 | 配置 | 74 | 5 | 5 | |
| 0x2002 | echoPara3 | uint16 | 配置 | 74 | 5 | 5 | |
| 0x2003 | echoPara4 | uint16 | 配置 | 74 | 5 | 5 | |
| 0x2004 | echoFreHz | uint32 | R/W | 10000 | 1 | 500 | |
| 0x2005 | MechOffset | float | 设定 | 7 | −7 | 4.619583 | 磁気エンコーダ角度オフセット |
| 0x2006 | status4 | float | R/W | 50 | −50 | 4.52 | 予約 |
| 0x2007 | limit_torque | float | R/W | 17 | 0 | 17 | トルク制限（**変更禁止推奨**） |
| 0x2008 | I_FW_MAX | float | R/W | 33 | 0 | 0 | 弱め界磁電流、既定 0 |
| 0x2009 | motor_baud | uint8 | 设定 | 20 | 0 | 1 | ボーレートフラグ |
| 0x200A | CAN_ID | uint8 | 设定 | 127 | 0 | 1 | 本ノード ID |
| 0x200B | CAN_MASTER | uint8 | 设定 | 127 | 0 | 0 | CAN ホスト ID |
| 0x200C | CAN_TIMEOUT | uint32 | R/W | 100000 | 0 | 0 | CAN タイムアウト閾値（20000 = 1 s）、既定 0 |
| 0x200D | status2 | int16 | R/W | 1500 | 0 | 800 | 予約 |
| 0x200E | status3 | uint32 | R/W | 1000000 | 1000 | 20000 | 予約 |
| 0x200F | status1 | float | R/W | 64 | 1 | 7.75 | 予約（7.75 は RS01/02/05 の減速比と同値） |
| 0x2010 | Status6 | uint8 | R/W | 1 | 0 | 1 | 予約 |
| 0x2011 | cur_filt_gain | float | R/W | 1 | 0 | 0.9 | 電流フィルタ |
| 0x2012 | cur_kp | float | R/W | 200 | 0 | 0.025 | 電流 Kp |
| 0x2013 | cur_ki | float | R/W | 200 | 0 | 0.0258 | 電流 Ki |
| 0x2014 | spd_kp | float | R/W | 200 | 0 | 2 | 速度 Kp |
| 0x2015 | spd_ki | float | R/W | 200 | 0 | 0.021 | 速度 Ki |
| 0x2016 | loc_kp | float | R/W | 200 | 0 | 30 | 位置 Kp |
| 0x2017 | spd_filt_gain | float | R/W | 1 | 0 | 0.1 | 速度フィルタ |
| 0x2018 | limit_spd | float | R/W | 200 | 0 | 2 | 位置モード速度制限 |
| 0x2019 | limit_cur | float | R/W | 23 | 0 | 23 | 位置・速度モード電流制限 |
| 0x201A | loc_ref_filt_gain | float | R/W | 100 | 0 | 0 | 予約 |
| 0x201B | add_elec_offset | float | R/W | 100 | 0 | 0 | 予約 |
| 0x201C | position_offset | float | R/W | 27 | 0 | 0 | 高速段オフセット |
| 0x201D | chasu_angle_offset | float | R/W | 27 | 0 | 0 | 低速端（出力側）オフセット |
| 0x201E | spd_step_value | float | R/W | 150 | 0 | — | 速度モード加速度 |
| 0x201F | vel_max | float | R/W | 20 | 0 | — | PP モード速度 |
| 0x2020 | acc_set | float | R/W | 1000 | 0 | — | PP モード加速度 |
| 0x2021 | zero_sta | float | R/W | 100 | 0 | 0 | ゼロ点フラグ（0x7029 は uint8、こちらは float と型が異なる） |
| 0x2022 | protocol_1 | uint8 | R/W | — | — | 0 | プロトコルフラグ（0 独自 / 1 CANopen / 2 MIT） |
| 0x2023 | add_offset | float | R/W | 7 | −7 | 0 | オフセット補償 |

**観測パラメータ（0x30xx, 読出専用）**

| index | 名称 | 型 | 参考値 | 備考 |
|---|---|---|---|---|
| 0x3000 | timeUse0 | uint16 | 5 | |
| 0x3001 | timeUse1 | uint16 | 0 | |
| 0x3002 | timeUse2 | uint16 | 10 | |
| 0x3003 | timeUse3 | uint16 | 0 | |
| 0x3004 | encoderRaw | int16 | 11396 | 磁気エンコーダ生値 |
| 0x3005 | mcuTemp | int16 | 337 | MCU 内部温度 ×10 |
| 0x3006 | motorTemp | int16 | 333 | モータ NTC 温度 ×10 |
| 0x3007 | vBus(mv) | uint16 | 24195 | 母線電圧 mV |
| 0x3008 | adc1Offset | int32 | 2084 | ADC ch1 ゼロ電流バイアス |
| 0x3009 | adc2Offset | int32 | 2084 | ADC ch2 ゼロ電流バイアス |
| 0x300A | adc1Raw | uint16 | 1232 | ADC サンプル値 1 |
| 0x300B | adc2Raw | uint16 | 1212 | ADC サンプル値 2 |
| 0x300C | VBUS | float | 36 | 母線電圧 V |
| 0x300D | cmdId | float | 0 | id ループ指令 A |
| 0x300E | cmdIq | float | 0 | iq ループ指令 A |
| 0x300F | cmdlocref | float | 0 | 位置ループ指令 rad |
| 0x3010 | cmdspdref | float | 0 | 速度ループ指令 rad/s |
| 0x3011 | cmdTorque | float | 0 | トルク指令 N·m |
| 0x3012 | cmdPos | float | 0 | MIT プロトコル角度指令 |
| 0x3013 | cmdVel | float | 0 | MIT プロトコル速度指令 |
| 0x3014 | rotation | int16 | 1 | 回転数（周回カウント） |
| 0x3015 | modPos | float | 4.363409 | モータ（ロータ）非計数機械角 rad |
| 0x3016 | mechPos | float | 0.777679 | 負荷端 計数込み機械角 rad |
| 0x3017 | mechVel | float | 0.036618 | 負荷端速度 rad/s |
| 0x3018 | elecPos | float | 4.714761 | 電気角 |
| 0x3019 | ia | float | 0 | U 相電流 A |
| 0x301A | ib | float | 0 | V 相電流 A |
| 0x301B | ic | float | 0 | W 相電流 A |
| 0x301C | timeout | uint32 | 31600 | タイムアウトカウンタ値 |
| 0x301D | phaseOrder | uint8 | 0 | 校正方向フラグ |
| 0x301E | iqf | float | 0 | iq フィルタ値 A |
| 0x301F | boardTemp | int16 | 359 | 基板温度 ×10 |
| 0x3020 | iq | float | 0 | iq 生値 A |
| 0x3021 | id | float | 0 | id 生値 A |
| 0x3022 | faultSta | uint32 | 0 | 故障状態（ビットは §2.11） |
| 0x3023 | warnSta | uint32 | 0 | 警告状態 |
| 0x3024 | drv_fault | uint16 | 0 | ドライバ IC 故障値 1 |
| 0x3025 | drv_temp | int16 | 48 | ドライバ IC 故障値 2（名称は温度） |
| 0x3026 | Uq | float | 0 | q 軸電圧 |
| 0x3027 | Ud | float | 0 | d 軸電圧 |
| 0x3028 | dtc_u | float | 0 | U 相出力デューティ |
| 0x3029 | dtc_v | float | 0 | V 相出力デューティ |
| 0x302A | dtc_w | float | 0 | W 相出力デューティ |
| 0x302B | v_bus | float | 24.195 | 閉ループ内 Vbus |
| 0x302C | torque_fdb | float | 0 | トルクフィードバック N·m |
| 0x302D | rated_i | float | 8 | モータ定格電流（参考値は EL05 ではない） |
| 0x302E | limit_i | float | 27 | モータ最大電流制限（同上） |
| 0x302F | spd_ref | float | 0 | 速度期待値 |
| 0x3030 | spd_reff | float | 0 | 速度期待値 2 |
| 0x3031 | zero_fault | float | 0 | 位置判定パラメータ |
| 0x3032 | chasu_coder_raw | float | 0 | 位置判定パラメータ |
| 0x3033 | chasu_angle | float | 0 | 位置判定パラメータ |
| 0x3034 | as_angle | float | 0 | 位置判定パラメータ |
| 0x3035 | cs_angle | float | 0 | 位置判定パラメータ |
| 0x3036 | chasu_age_out | float | 0 | 位置判定パラメータ |
| 0x3037 | motormechpositio | float | — | 位置判定パラメータ |
| 0x3038 | fault1 | uint32 | 0 | 故障ログ |
| 0x3039 | fault2 | uint32 | 0 | 故障ログ |
| 0x303A | fault3 | uint32 | 0 | 故障ログ |
| 0x303B | fault4 | uint32 | 0 | 故障ログ |
| 0x303C | fault5 | uint32 | 0 | 故障ログ |
| 0x303D | fault6 | uint32 | 0 | 故障ログ |
| 0x303E | fault7 | uint32 | 0 | 故障ログ |
| 0x303F | fault8 | uint32 | 0 | 故障ログ |
| 0x3040 | ElecOffset | float | 0 | 電気角オフセット |
| 0x3041 | mcOverTemp | int16 | 0 | 過温閾値 |
| 0x3042 | Kt_Nm/Amp | float | 0 | トルク係数 |
| 0x3043 | Tqcali_Type | uint8 | 0 | モータ種別 |
| 0x3044 | low_position | float | 0 | 位置判定パラメータ |
| 0x3045 | theta_mech_1 | float | 0 | type 2 低速角度 |
| 0x3046 | instep | float | 0 | 保護判定パラメータ（EN: Position value） |
| 0x3047 | position | float | 39.987045 | 位置値 |
| 0x3048 | chasu_angle_init | float | 4.744603 | 位置判定パラメータ |
| 0x3049 | chasu_angle_out | float | 42.701424 | 位置判定パラメータ |
| 0x304A | motormechinit | float | 39.987045 | 位置判定パラメータ |
| 0x304B | mech_angle_init2 | float | 4.443005 | 位置判定パラメータ |
| 0x304C | mech_angle_rotat | int16 | 0 | 位置判定パラメータ |
| 0x304D | pos_cnt1 | uint16 | 0 | システムパラメータ |
| 0x304E | （名称文字化け「¨d」） | uint8 | 168 | システムパラメータ |

- MotorStudio「オシロスコープ」はこれらの観測値（Id/Iq、温度、出力速度、ロータ位置、出力位置など）を出力周波数を指定して表示する（EN p.28–29 / CN p.19）。PYS は 0x3016/0x3017/0x302C/0x2005 を type 17 で読んでいる（→ 0x20xx/0x30xx も type 17 で読める可能性が高い）。

---

## 6. CANopen（参考。EN p.62–69 / CN p.46–50）

- EDS ファイルは公式サイトのダウンロードセンターから取得（EN p.62）。COB-ID（SDO 0x600+ID 等）の記載はなく、標準 CiA301 に従うと推定。
- ノード ID = 独自プロトコルの CAN ID（EN p.51 / CN p.37）。

### 6.1 状態機械（EN p.64–65 / CN p.47–48）
- 起動時 SWITCH_ON_DISABLED。Controlword (0x6040) を 6 → 7 → 15 で段階遷移、または 15 で直接 OPERATION_ENABLE。
- 通常停止: 0x6040 ← 1（SWITCH_ON_DISABLED へ）。非常停止: 0x6040 ← 11（**サージ電圧に注意**）。
- 故障クリア: 0x6040 の変更で通常故障をクリア（具体値の記載なし）。
- ウォッチドッグ: 0x6099 サブインデックス 1。20000 = 1 s、既定 0（無効）。受信途絶で無効化。
- **モード変更は必ず無効状態で行う。**

### 6.2 オブジェクト
| index | 名称 | 属性 | 型 | 単位 |
|---|---|---|---|---|
| 0x603F | Error_code | R | UINT16 | — |
| 0x6040 | Controlword | RW | UINT16 | — |
| 0x6041 | Statusword | R | UINT16 | — |
| 0x6060 | Modes_of_operation | RW | INT8 | — |
| 0x6061 | Modes_of_operation_display | R | INT8 | — |
| 0x6062 | Position_demand_value | R | INT32 | pulse（1 rev = 16384） |
| 0x6064 | Position_actual_value | R | INT32 | pulse（1 rev = 16384） |
| 0x6067 | Position_window | RW | UINT32 | pulse |
| 0x6068 | Position_window_time | RW | UINT16 | ms |
| 0x606B | Velocity_demand_value | R | INT32 | 0.1 rpm |
| 0x606C | Velocity_actual_value | R | INT32 | 0.1 rpm |
| 0x6071 | Target_torque | RW | INT16 | 0.1 % 負荷率（1000 = 2 N·m） |
| 0x6077 | Torque_actual_value | R | INT16 | 0.1 % 負荷率（1000 = 2 N·m） |
| 0x6078 | Current_actual_value | R | INT16 | mA |
| 0x6079 | DC_link_circuit_voltage | R | INT32 | mV |
| 0x607A | Target_position | RW | INT32 | pulse |
| 0x6081 | Profile_velocity | RW | UINT32 | 0.1 rpm |
| 0x6083 | Profile_acceleration | RW | UINT32 | 0.1 rpm/s |
| 0x60FF | Target_velocity | RW | INT32 | 0.1 rpm |
| 0x6099 sub1 | CAN ウォッチドッグ | RW | — | 20000 = 1 s |

### 6.3 モード（0x6060）
| 値 | モード | 手順 |
|---|---|---|
| 6 | ホーミング（ゼロ設定） | 無効状態で 6 を設定すると現在位置をゼロに。0x6040 ← 15 でゼロ位置保持 |
| 1 | PP | 無効状態で 1、必須: Target_torque, Profile_velocity, Profile_acceleration（任意: Position_window/_time）→ 0x6040 ← 15 → Target_position |
| 5 | CSP（**CiA402 標準の 8 ではなくベンダ独自の 5**） | 無効状態で 5、必須: Target_torque, Profile_velocity → 15 → Target_position |
| 3 | 速度 | 無効状態で 3、必須: Target_torque → 15 → Target_velocity |
| 4 | トルク | 無効状態で 4 → 15 → Target_torque |

- Target_torque は各位置/速度モードでの**最大トルク絶対値**として使われる。
- 「1000 = 2 N·m」は定格 1.8 N·m と一致せず、正確な換算は要確認。

### 6.4 独自プロトコルへ戻す（EN p.69 / CN p.49–50）
- **拡張フレーム ID = 0x00000FFF**、データ `01 02 03 04 05 06 F_CMD (00)`（F_CMD = B6: 0 独自 / 1 CANopen / 2 MIT）
- 応答: 11bit ID = モータ ID、データ = 64bit MCU UID。**再投入後有効。**

---

## 7. その他の機能

| 機能 | 手順 | 出典 |
|---|---|---|
| ゼロ点設定 | 独自: type 6（B0=1）。MIT: 指令 4。CANopen: モード 6。PP では不可 | §2.7 |
| ゼロ点範囲 | 0x7029 zero_sta（0: 0～2π、1: −π～π）→ type 22 保存 | EN p.49 / CN p.37 |
| ゼロオフセット | 0x702B add_offset（type 22 保存可） | CN p.32, p.36 |
| CAN ID 変更 | 独自: type 7。MIT: 指令 7 | §2.8, §4.4 |
| 主機 ID 変更 | MIT: 指令 9。独自: 0x200B CAN_MASTER（手段は §8） | |
| ボーレート変更 | type 23（再投入後）。0x2009 motor_baud | §2.13 |
| 主動上報 | type 24 + 0x7026 EPScan_time。MIT: 指令 13 | §2.14 |
| 保存 | type 22。MIT: 指令 12 | §2.12 |
| 有効化/停止/故障クリア | type 3 / type 4 (B0=0) / type 4 (B0=1)。MIT: 指令 1 / 2 / 5 (F_CMD=0xFF) | |
| UID 取得 | type 0（応答 B0–B7）。MIT: 応答指令 2 | |
| CAN 断保護 | 0x7028 canTimeout（実行時）/ 0x200C CAN_TIMEOUT（flash）。非 0 で指定時間受信なし → Reset モード | EN p.30, p.48 / CN p.20, p.36 |
| エンコーダ校正 | MotorStudio「Encoder Calibration」（プロトコル非公開） | EN p.16 |
| ファーム更新 | MotorStudio（OTA 手順は `docs/vendor/common/OTA协议说明.pdf` 参照、本書対象外）。1 Mbps 必須 | CN p.13 |
| 工場出荷リセット | MotorStudio「Factory Reset」（プロトコル非公開） | EN p.19 |

---

## 8. 既知の注意点・曖昧点

### 8.1 EN / CN の食い違い（CN v1.1 を優先）

| 項目 | EN (v1.0) | CN (v1.1) | 採用 |
|---|---|---|---|
| type 1/2 角度レンジ | 本文テキストで「(-4~4)」と π 欠落（図は −4π～4π） | −12.57f～12.57f | ±12.57 |
| type 1 トルク位置 | 「Byte2: Torque」 | bit23~8: 力矩 | ID bit23..8 |
| type 2 bit20 | 「Uncalibrated」重複 | 堵转过载 | ストール過負荷 |
| 型 2 のバイト順 | 「If the value is higher than 10…」（誤訳） | 以上数据高字节在前 | 全フィールド BE |
| サンプル T_MIN/T_MAX | ±5.5 | ±6 | ±6 |
| 4.2.3 P_MIN | 「12.57f」（符号欠落） | −12.57f | −12.57 |
| type 18 保存対象 | 0x20xx | 0x20xx, 0x702A, 0x702B | CN |
| 0x7021/0x7022/0x7024/0x7025 | W | W/R | W/R |
| 0x3046 instep | Position value | 电机保护判定参数 | CN |
| 5.8 速度モード Target_torque | velocity mode の最大トルク | 位置模式下…（コピーミス） | EN の意味 |
| 制御基板最大温度 | 103 ℃ | 135 ℃ | GUI は 103 ℃ で警告 |

### 8.2 参考コードとの差異

| 参考 | 内容 | EL05 での扱い |
|---|---|---|
| REF-MD / SDK | バージョン読出を **type 26 (0x1A)** とする | マニュアルは type 4。両対応 |
| REF-MD | 過温 **145 ℃** | EL05 は 135 ℃ |
| REF-MD | MIT 応答の温度を Byte6~7 とする | B6 上位 4bit はモード/故障/警告。12bit 温度 |
| REF-MD | RS05 定格 1.6 N·m | SPEC では 1.8 N·m（EL05 には無関係） |
| SDK docs | EL05 最大電流 10 Apk | マニュアルは 11 Apk |
| SDK | type 17 応答を常に float 解釈 | 型ごとに解釈 |
| SDK | バージョンを data[2:7] で解釈 | マニュアルは B3–B6 |
| SDK `FaultBit` | type 2 のビット（0: 低電圧, 1: 相電流）と type 21 のビット（3: 過電圧…）を混在 | 別々に定義すること（§2.3 / §2.11） |
| PYS `table.py` | **EL05 の定義なし**。rs-05 を 33 rad/s・17 N·m、rs-00 を 50 rad/s・17 N·m など現行マニュアルと不一致 | 使用しない。EL05 は ±12.57 / ±50 / ±6 / 500 / 5 |
| PYS | スケーリング `(u/0x7FFF − 1)·max`（非対称） | マニュアル式 `(x−min)·65535/span` |
| PYS | type 21 のストールビットを warning 語から読む | fault 語の bit14 |
| PYS | ホスト ID 0xFF | 0xFD 推奨 |
| ROS / SDK | EL05 = ±6 N·m, ±50 rad/s, ±12.57 rad, Kp 0–500, Kd 0–5、type 1 の t_ff を ID bit23..8 に格納 | マニュアルと一致 |
| REF-MD | 旧ファーム（RS00–04 の一部）は P レンジ ±12.5 | EL05 の記載なし。±12.57 |

### 8.0 実機で確認済み(2026-10-02、EL05 ID 127、生ログ `user_data/hw1_probe_frames.log`、詳細 `hw_bench.md`)

| 項目 | 結果 |
|---|---|
| バージョン読出 | **type 4 + data `00 C4 00…` が正**。応答は type 2 の ID(`02007FFD`)で data `00 C4 56 0A 05 00 02 07`。B3–B6 = `0A 05 00 02`、B7 = `07`(版数表記との対応は未確定。10 進なら 10.5.0.2)。**type 26 は EL05 では版数を返さず通常のフィードバックが返る** |
| バージョン応答の扱い | type 2 と同じ ID なので、そのまま状態として解釈すると角度 −12.49 rad・温度 51.9 ℃ のような誤値になる → `data[0:3] == 00 C4 56` のフレームは状態更新に使わない |
| 主動上報 | **通信タイプ 24(0x18)** で届く(`18007FFD …`)。周期 9.88 ms(EPScan_time = 0 のとき 10 ms。マニュアルの「1 = 10 ms」と異なり 0 でも 10 ms)。OFF 指令自体には type 2 が 1 回返る |
| type 17 読出 | 0x70xx 表の 25 項目は成功。**0x702C alveolous_open / 0x702D iq_test / 0x702E dcc_set は読出失敗(bit23..16 = 0x01)**。未定義 0x7007–0x7009, 0x700C, 0x7023, 0x7027, 0x702A も失敗。**0x701D は成功(raw `03 00 00 00`、意味不明。PYS のコメントでは rot_cnt)** |
| 出荷時の値(マニュアル既定値と異なるもの) | cur_kp 0.125(表 0.17)、cur_ki 0.0158(表 0.012)、spd_filt_gain 0.5(表 0.1)、acc_rad 10(表 20)、loc_kp 30(表 40)、limit_spd 50、limit_cur 11、limit_torque 6 |
| 停止中の loc_ref | loc_ref (0x7016) = mechPos (0x7019) = 2.038 rad(停止中は現在位置に追従している)→ CSP/PP の有効化直後に急に動く心配は小さい(要 HW-2 で確認) |
| 送信間隔 | **フレーム間隔 0.5 ms 未満で取りこぼす**(公式 USB-CAN 経由)。最小 1 ms 空ける(hw_bench.md) |
| 主動上報周期 | EPScan_time N → **10 + 5·N ms**(N=0 → 10 ms、N=1 → 15 ms) |
| 母線電圧・温度 | VBUS 24.49 V(24 V 電源)、温度 29.0 ℃(type 2 の B6–B7 = 0x0122 = 290) |
| 位置の折り返し | **type 2 フィードバックの位置は ±4π で折り返す**(実位置 13.02 rad → −12.11 rad = 13.02 − 8π)。**mechPos (0x7019) は折り返さない多回転値**(13.02 rad)。2026-10-02 速度モード 0.3 rad/s で 4π をまたいで確認(hw_bench.md HW-3b)。運控(MIT)の p_set が内部でどちらの位置と比べられるかは**未確認**(open_questions Q-P12)。**ホスト側の扱い**: 折り返し位置だけを展開した多回転値は、起動時に ±4π の外にいると k·8π ずれる(CLI で多回転 −12.99 / mechPos +13.02 を確認)ので、mechPos で較正する(`Motor.sync_multi`)。MIT は不明の間、両解釈で最悪トルクを見積もり ±4π 付近は kp = 0(architecture.md §4.3.2、`profiles/el05.toml` の `[position]`) |
| type 22(保存) | **0x70xx は保存されない**(EPScan_time で確認、電源再投入で元に戻る)。応答は type 2 ではなく **type 0(UID、宛先 0xFE)** が約 4 ms 後に返る(hw_bench HW-6) |
| 電流モードの換算 | **実電流 iqf ≈ iq_ref / 9**(Δiqf/ΔIq ≈ 1/9.7)。iq_ref 1 A → 出力 約 0.07 N·m。トルク読み値 = 約 0.65 N·m/A × iqf。limit_spd・limit_torque は電流モードを制限しない(hw_bench HW-6) |

### 8.3 未解決・要実機確認(8.0 で解決したものは取り消し線)

1. **0x0000–0x30xx の上位機パラメータを type 17/18 で直接読み書きできるか**（PYS は 0x2005/0x3016/0x3017/0x302C を type 17 で読んでいる）。String 型（0x0000–0x1007）は 4 バイトに収まらず、MotorStudio の取得方法は非公開。
2. 0x20xx の実際の割当・範囲が EL05 ファームで表と一致するか（表の参考値は他機種のもの。CN p.15 スクショは別配置）。
3. 0x702A の意味（`acc_stutus` か）、`max_alve` の index。
4. ~~主動上報フレームの通信タイプが 2 か 0x18(24) か。~~ → **24**(§8.0)
5. ~~バージョン読出の正式な通信タイプ（4 か 26 か）~~ → **type 4**(§8.0)。版数バイトの対応は未確定
6. type 6 ゼロ点・type 7 ID 変更の flash 自動保存の有無。
7. type 21 が自発送信か要求応答か、要求方法。
8. 公式 USB-CAN（AT モード）の標準フレーム表現、シリアル速度、初期化手順。
9. MIT 指令 14/15 応答 ID の下位 8bit（モータ ID か主機 ID か）。
10. iq_ref / limit_cur の電流基準（ピークか rms か）。
11. 温度フィールドの符号（負温度）。
12. CANopen の Target_torque 換算（1000 = 2 N·m）と COB-ID、Statusword ビット定義。
13. モータ内蔵 CAN 終端の有無。

---

## 9. 実装チェックリスト

- [ ] CAN 1 Mbps、29bit 拡張、DLC 8。ID = `type<<24 | data2<<8 | dest`。
- [ ] ホスト ID 0xFD 既定。受信は `ID & 0xFF == host`（type 0 応答は 0xFE）で受理、モータ ID は `(ID>>8)&0xFF`。
- [ ] スケーリング定数: P ±12.57, V ±50, T ±6, Kp 0–500, Kd 0–5。16bit は分母 65535、12bit（MIT）は 4095。送信時クランプ。
- [ ] type 1: t_ff を ID bit23..8、データは BE。トルク 0 = 0x7FFF。
- [ ] type 2 / 0x18 を同一デコーダで処理（モード bit23..22、故障 bit21..16、データ BE、温度 /10）。角度は ±4π ループ → unwrap。
- [ ] type 17/18: index LE、B2–B3 = 0、値 LE。型ごとの幅（uint8 は B4）。応答 bit23..16 で成否判定。
- [ ] run_mode 切替は必ず停止（type 4）→ 書込 → 有効化（type 3）→ 制限 → 指令。
- [ ] PP では type 6 ゼロ設定不可、運転中の速度変更不可（非常停止は vel_max ← 0）。
- [ ] CSP/PP 有効化時の飛びつき防止（loc_ref ← 現在位置）。
- [ ] 故障クリア = type 4 B0=1。type 21 / 0x3022 ビット表を実装。
- [ ] 主動上報: type 24 B6=1、周期 = 10 + 5·N ms（0x7026、実測）。
- [ ] canTimeout（0x7028, 20000 = 1 s）を連続制御中に設定し、GUI 異常終了時に自動停止させる。
- [ ] type 22 保存は停止中のみ。保存対象は 0x20xx, 0x702A, 0x702B（zero_sta 変更も保存必要）。
- [ ] type 23 / 25 は再投入後有効 → GUI で「電源再投入してください」を表示、ホスト側ビットレート/プロトコルも切替。
- [ ] MIT モード: 11bit ID `kind<<8 | id`、特殊コマンド（FC/FD 衝突に注意）、12bit 詰め、応答 1 のデコード（B6 のモード/故障/警告/温度上位）。
- [ ] 独自プロトコルへの復帰フレームを GUI に用意（MIT: `FF FF FF FF FF FF 00 FD`、CANopen: 拡張 ID 0xFFF `01 02 03 04 05 06 00 00`）。
- [ ] バージョン読出は停止中に type 4 `00 C4 ..`、応答 `00 C4 56` 判別。無応答なら type 26。
- [ ] AT モード USB-CAN: `41 54 | (ID<<3)|4 (BE) | 08 | data | 0D 0A`。
- [ ] 危険パラメータ（limit_torque, 保護温度, 過温時間, MechOffset, CAN_ID, motor_baud, protocol_1）は上級者モード＋確認ダイアログ。
- [ ] GUI 既定の安全制限値（el05_spec.md §11）を初期値として適用。
