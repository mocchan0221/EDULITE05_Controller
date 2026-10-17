# CAN トランスポート調査：Windows PC から RobStride EL05 / RS 系アクチュエータへ

- 作成日：2026-10-01
- 目的：新しい GUI 制御ツールのトランスポート層（PC と CAN バスの間）を設計するための事前調査
- 対象：Windows 11、RobStride EduLite 05（EL05）および RS 系、私有プロトコル（CyberGear 系）

出典は、リポジトリ内ファイルを `パス:行` で、PDF を `ファイル名 PDF p.N（印刷ページ M）` で、Web を URL で示す。
パスの接頭辞は次のとおり省略する。

- `sdk/` = `docs/vendor/reference_code/el_a3_sdk/`
- `sample/` = `docs/vendor/reference_code/python_sample/`
- `conv/` = `docs/vendor/reference_code/can_usb_conversion/`

「推定」と書いた箇所は、実機で測定していない計算値または推論である。

---

## 0. 要約（TL;DR）

- **CAN 物理層**：CAN 2.0B 拡張フレーム（29-bit ID）、1 Mbps（EL05User Manual PDF p.14 / p.40）。
  ID のビット割り当ては次のとおり。
  - bit28–24：通信タイプ
  - bit23–8：データ区 2
  - bit7–0：宛先 ID
- **公式 USB-CAN（CH340 + GD32）**は **仮想 COM ポート上の独自 "AT" バイナリフレーム**を使う。
  - シリアル速度は **921600 bps** 固定（Studio 手順書 PDF p.3 のスクリーンショット）。
  - フレーム構成：`41 54 | ID32(BE) = (can_id29 << 3) | 0x04 | DLC | data[DLC] | 0D 0A`
  - python-can の標準インタフェースには含まれないので、自前実装（約 150 行）が必要。
- **USB-CANHUB（5ch）**は candleLight / gs_usb 互換（CANHUB 説明書 PDF p.12：`CandleAPI`、`vid_1d50&pid_606f`）。
  - Linux では `can0..can4`（SocketCAN）として見える。
  - Windows では python-can `gs_usb`（pyusb + libusb）または `python-can-candle` で使える。
- **公式 SDK / サンプルの状況**
  - Windows で動くのは SLCAN 版（`sdk/el_a3_sdk/slcan_can_driver.py`）だけ。
  - `sample/robstride_dynamics/bus.py` は `interface="socketcan"` が固定で、Windows では動かない。
  - どちらにも、要求と応答の突き合わせ、タイムアウト、スレッド設計に品質上の問題がある（§3）。
- **帯域（推定）**
  - 1 Mbps のとき、拡張 8 バイトフレーム 1 枚は 131〜160 µs。
  - MIT 指令 1 回（指令 1 枚と応答 1 枚）は 1 モータあたり約 0.26〜0.32 ms。
  - 7 モータを 1 バスに載せ、バス負荷 60% に抑えると上限は約 330 Hz。
  - AT シリアルは 1 フレーム 17 バイトで、921600 bps では 184 µs/フレーム（片方向あたり約 5400 fps）。
    バスの公称上限（約 7600 fps）より遅いので、多数モータではシリアル側が先に詰まる。
- **推奨**
  - 自前の `CanTransport` 抽象を用意し、実装を 3 系統にする。
    - `AtSerialTransport`（公式 USB-CAN、第一優先）
    - `PythonCanTransport`（gs_usb/candle、slcan、pcan、socketcan、virtual を包む）
    - `SimTransport`（仮想バスとモータシミュレータ）
  - 受信は専用スレッド 1 本で行い、状態を集約する。要求と応答の照合は Future とタイムアウトで行う。
  - GUI は 30〜60 Hz でスナップショットを引くだけにする。

---

## 1. Windows で使えるアダプタの選択肢

### 1.1 一覧

| # | アダプタ | PC 側の見え方 / プロトコル | python-can 標準対応 | Motor Studio 対応 | 主な長所 | 主な短所 |
|---|---|---|---|---|---|---|
| A | **RobStride 公式 USB-CAN モジュール**（CH340 + GD32、1ch） | USB 仮想 COM（CH340）、**独自 AT バイナリ**、921600 bps | **非対応**（自前実装が必要） | **対応（唯一の公式手段）** | 公式 GUI（キャリブレーション、ID 変更、OTA）と同じ機材で済む。安価。CH340 ドライバは入手が容易 | 独自フレーム。シリアル帯域が上限（約 5.4k fps/片方向）。バス状態・エラーフレーム・HW タイムスタンプが取れない。設定コマンドが非公開 |
| B | **RobStride USB-CANHUB**（5ch） | candleLight / gs_usb（USB bulk、バイナリ）。Linux では SocketCAN `canX` | `gs_usb`（標準）、`candle`（プラグイン `python-can-candle`） | 非対応（Studio は AT 前提） | 5 系統に分けてバス帯域を 5 倍にできる。USB bulk なので低オーバヘッド。HW タイムスタンプ、エラー通知あり | Windows では WinUSB / libusbK と libusb DLL の準備が要る。python-can `gs_usb` の多チャネル対応は要確認 |
| C | **CANable / CANable 2.0 等（slcan ファーム）** | USB CDC 仮想 COM、SLCAN ASCII（`T<id8><dlc><hex>\r`） | `slcan` | 非対応 | 安価。公式 SDK の Windows 経路と同じ（`sdk/el_a3_sdk/slcan_can_driver.py:8-10`） | ASCII のため 1 フレーム 27 バイトで冗長。ファームによっては ACK やエラーの扱いがまちまち |
| C' | **CANable 等（candleLight ファーム）** | gs_usb | `gs_usb` | 非対応 | B と同様（1ch） | B と同様 |
| D | **PEAK PCAN-USB** | 専用ドライバ + PCAN-Basic DLL | `pcan` | 非対応 | Windows ドライバが最も安定。HW タイムスタンプとバス状態を取れる。Seeed reBot キットも採用 | 高価 |
| E | Kvaser / Vector / IXXAT | 専用ドライバ | `kvaser` / `vector` / `ixxat` | 非対応 | 産業用の品質 | 高価。教育用途には過剰 |
| F | Linux SocketCAN（WSL2 経由など） | — | `socketcan`（Linux のみ） | — | 公式 SDK の主経路（`sdk/el_a3_sdk/can_driver.py:4`、`sample/README.md:4-6`） | Windows ネイティブでは使えない。WSL2 はカーネルモジュールと usbipd が必要で非推奨 |

### 1.2 根拠

**A：公式 USB-CAN**

- Studio 手順書に次の記載がある（Instructions for using the Studio_241122.pdf PDF p.1）。
  - "The debugger needs to install ch340 driver in advance and works in AT mode by default."
  - "... Robstride USB-CAN module ... frame header of 41 54 and a frame tail of 0D 0A"
- 接続ダイアログは `Module: USB-CAN (AT Command)`、`COM: USB-SERIAL CH340`、`Baudrate: 921600`（グレーアウトで変更不可）（同 PDF p.3）。
- EL05 マニュアルの記載（EL05User Manual260713.pdf PDF p.14、印刷 p.13）：
  - "When DIP switch 1 is in the ON position, the module enters Boot mode and cannot establish a connection
    with the host computer."
  - "When DIP switch 2 is in the ON position, a 120Ω terminal resistor is connected"
- 製品ページ：GD32 MCU と CH340、UART 最大 921600 bps、"AT command mode"、OTA 対応
  （https://www.seeedstudio.com/Robostride-CAN-USB-Driver-Board-p-6708.html 、
  https://openelab.io/products/robstride-can-to-usb-debugging-module ）。
- `sample/README.md:49-51` は公式 USB-CAN を "SocketCAN-compatible adapter" と書いている。しかし上記の資料はすべて
  CH340 シリアルの AT 方式を指しているので、この記述は CANHUB（gs_usb）との混同か、別ファームの可能性が高い。

**B：USB-CANHUB**（USB_CANHUB使用说明V1.0.pdf）

- 電源は USB Type-C（5V）。120Ω 終端は DIP スイッチで切り替える。ブートボタンを押すと USB から書き換えできる（PDF p.8-9）。
- Linux では "在 linux 生成 canX(0,1,2,3...)"。`ip link set can0 type can bitrate 1000000` で設定し、
  `candump` / `cansend` を使う（PDF p.9）。
- 参照例として "基于 canable 例程" と書かれ、`robstride_ros_sample` と `Robstride-Dynamics-Python-SDK` が挙がっている（PDF p.10）。
- Windows での手順は、cangaroo（`cangaroo-win32-ccdcb64`）で Measurement Setup を開き、`candle0..candle4` の
  5 ネットワークを 1 Mbps に設定する（PDF p.10-12）。
- Interface Details に `\\?\usb#vid_1d50&pid_606f...` と表示され、Driver は `CandleAPI` である（PDF p.12）。
  VID 1d50 / PID 606f は candleLight / gs_usb の ID なので、**CANHUB は candleLight 互換**と判断した。
- 送信例として Address `0300FD7F`、Extended ID、DLC 8、データ全 0 を送っている（PDF p.12）。
  これはホスト 0xFD からモータ 0x7F（工場出荷 ID 127）への通信タイプ 3（Enable）である。
- `sdk/README_en.md:82` にも "CAN Adapter: CANdle / gs_usb compatible" とあり、
  トラブルシュート（`sdk/README_en.md:380`）で `modprobe gs_usb` を使っている。

**python-can の対応状況**（python-can 4.6.1、https://python-can.readthedocs.io/en/stable/interfaces.html ）

- 標準で含まれる interface：`gs_usb`、`slcan`、`pcan`、`kvaser`、`vector`、`ixxat`、`socketcan`、`virtual` ほか。
- RobStride AT は含まれない。python-can の `serial` interface は 0xAA/0xBB で区切る独自形式なので流用できない。
- 自前の BusABC 実装を `can.interface` エントリポイントで登録すれば、python-can の一員として扱える
  （https://python-can.readthedocs.io/en/stable/plugin-interface.html ）。
- `gs_usb` は Windows、Linux、Mac で動く。
  - `pip install "python-can[gs-usb]"` に加えて libusb が必要。
  - Windows では Zadig で libusbK を割り当てるよう記載がある。
  - HW フィルタは使えない（https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html ）。
  - 補足：candleLight ファームは通常 MS OS 記述子で WinUSB に自動バインドされる。CANHUB でもそうなるかは要確認。
- `slcan` のチャネル指定は `COM4` または `COM4@115200`。既定値は `sleep_after_open=2 s`
  （https://python-can.readthedocs.io/en/stable/interfaces/slcan.html ）。
- 詳細は `docs/vendor/usb_can/web_notes_python_can_windows.md` にまとめた。

---

## 2. 公式 USB-CAN（CH340）の "AT" シリアルフレーム：厳密な仕様

### 2.1 シリアルポートの設定

| 項目 | 値 | 出典 |
|---|---|---|
| ボーレート | **921600 bps**（Studio では変更不可） | Studio 手順書 PDF p.3。Web 情報（Kimzae/cybergear README、openelab 製品ページ） |
| 形式 | 8N1（推定。一般的な CH340 の既定。資料には明記なし） | — |
| フロー制御 | なし（推定） | — |
| CAN ビットレート | 1 Mbps（アダプタ側で固定済み。変更手段は公開されていない） | EL05 PDF p.14。Studio PDF p.1 |

### 2.2 フレーム構造（TX / RX 共通）

```
offset: 0    1    2    3    4    5    6    7 ... 7+DLC-1   7+DLC  8+DLC
       'A'  'T'  [ ID field 32bit BE ]  DLC  data[0..DLC-1]  '\r'   '\n'
       0x41 0x54                        1B   0..8 B          0x0D   0x0A
全長 = 9 + DLC バイト（DLC=8 のとき 17 バイト）
```

- **ID フィールド** = `(can_id29 << 3) | 0x04`（ビッグエンディアン 4 バイト）
  - 送信側：`conv/mainwindow.cpp:140`（`addr = (addr << 3) | 0X00000004;`）、`:144-147`（MSB から順に格納）
  - 受信側：`conv/mainwindow.cpp:170-176`（`addr = (addr & ~0x00000004) >> 3;`）
  - 同じ規則が Web にも記載されている（Kimzae/cybergear README、dawnmainak/Robstride_Control_Gui `protocol.py`）。
- **下位 3 ビットの意味（推論）**
  - bit2 = 0x04 は拡張フレームを示す IDE フラグ、bit1 は RTR、bit0 は未使用と考えられる。
  - この並びは STM32 / GD32 の bxCAN メールボックスレジスタ（`CAN_TIxR`：EXID[28:0] が bit31:3、
    IDE が bit2、RTR が bit1）と同じである。アダプタ（GD32）がレジスタ値をそのまま出していると推定される。
  - 標準フレーム（IDE=0）は `STID << 21` で表されると推定されるが、公式資料には記載がない。受信側では bit2 を見て標準フレームを捨てるかログに回すこと。
- **DLC**：1 バイト、0〜8。送信側は `conv/mainwindow.cpp:104-112`（1 未満なら 1 に、8 超なら 8 に丸める）と `:149`。受信側は `:188`。
- **データ**：DLC 分をそのまま格納する（`conv/mainwindow.cpp:151-152`、`:190-191`）。
  データ内のバイト順は RobStride プロトコルの定義に従う。MIT 系の値はビッグエンディアン、パラメータのインデックスと値はリトルエンディアン。
- **終端**：`0D 0A`（`conv/mainwindow.cpp:154-155`）。受信コードは終端を検証していない（`:168` の長さチェックはコメントアウトされている）。
- **ヘッダ**：受信側は `pack[0]==0x41 && pack[1]==0x54` だけを確認している（`conv/mainwindow.cpp:168`）。

**注意**：データ部に `41 54` や `0D 0A` が現れることは普通にある（エスケープはない）。
したがって、受信パーサは「`AT` を探す → DLC から全長を決める → 終端 `0D 0A` を検証する →
不一致なら 1 バイト進めて再同期」とするのが正しい。終端で区切る方式（readline）は誤りである。

### 2.3 29-bit ID の中身（RobStride 私有プロトコル）

EL05User Manual PDF p.40（印刷 p.39）、`sdk/el_a3_sdk/protocol.py:4-9`：

```
bit28..24 : 通信タイプ (0..31)
bit23..8  : データ区2（多くはホストID を bit15..8 に入れる。type1 では前馈トルク u16、type2 ではモータID/故障/モード）
bit7..0   : 宛先アドレス（指令ならモータID、応答ならホストID）
```

- `conv/mainwindow.h:37-43` の `exCanIdInfo{id:8; data:16; mode:5; res:3;}` は CyberGear マニュアルと同じビットフィールド
  （https://github.com/belovictor/cybergear-docs/blob/main/instructionmanual/instructionmanual.md ）。
  リトルエンディアンのビットフィールドなので、id が LSB 側、mode が bit28..24 に入る。
- ただし Qt ツールの `txdPack` は `id<<24 | data<<8 | mode<<3 | res`（`conv/mainwindow.cpp:126-129`）と
  フィールド名が逆の詰め方をしている。
  UI 入力の `byte0..3` を big-endian で並べた値がそのまま 29-bit ID になる（`:332-341`）。**数値としては正しいがフィールド名が誤っている**ので、参考にするときは注意。

### 2.4 実例（すべて Python で検算済み）

**例 1：run_mode=1（PP）の書き込み。type 18、ホスト 0xFD → モータ 1**
（EL05User Manual PDF p.30-31 / 印刷 p.29-30 の例をそのまま使った）

```
41 54 | 90 07 E8 0C | 08 | 05 70 00 00 01 00 00 00 | 0D 0A
ID field 0x9007E80C: bit2=1(拡張), >>3 → 0x1200FD01
  = type 0x12(18=WRITE_PARAM) | data2 0x00FD(ホストFD) | 宛先 0x01
data: index 0x7005(LE, run_mode) | 00 00 | value 0x00000001(LE)
```

- マニュアルの表では "Number of data bits" と "extended frame" の見出しが列とずれている（誤植）。
- `conv/README.md:4` の "43 07 e8 0c" も誤植である。同じ行に書かれた 2 進表記 `1001 0000 ...` は 0x9007E80C を表し、
  これを 3 ビット右シフトすると README の結論 `12 00 FD 01` になる。
  "43 07 e8 0c" を同じように計算すると 0x0860FD01 になり、結論と合わない。

**例 2：Enable。type 3、ホスト 0xFD → モータ 0x7F（工場出荷 ID）**
（CAN ID 0x0300FD7F は CANHUB 説明書 PDF p.12 の送信例）

```
41 54 18 07 EB FC 08 00 00 00 00 00 00 00 00 0D 0A
```

- モータ 1 宛て（0x0300FD01）なら `41 54 18 07 E8 0C 08 00*8 0D 0A` になる。

**例 3：受信フィードバック。type 2、モータ 1 → ホスト 0xFD、モード 2（Motor）、故障なし**

```
CAN ID = (2<<24)|(2<<22)|(1<<8)|0xFD = 0x028001FD
41 54 14 00 0F EC 08 80 00 7F FF 80 00 01 18 0D 0A
data: pos=0x8000(≈0 rad), vel=0x7FFF(≈0), torque=0x8000(≈0), temp=0x0118=280 → 28.0 °C
```

- フィードバックのビット定義は EL05 PDF p.41（印刷 p.40）と `sdk/el_a3_sdk/can_driver.py:188-204` による。
  - bit23-22：mode
  - bit21-16：fault
  - bit15-8：モータ ID

**例 4：MIT（運控）指令。type 1、モータ 1、p=0, v=0, Kp=10, Kd=0.5, τ=0**

- 値域は EL05 の値を使った：±12.57 rad、±50 rad/s、Kp 0..500、Kd 0..5、±6 Nm（`sdk/el_a3_sdk/protocol.py:155-159`）。

```
CAN ID = (1<<24)|(τ_u16=0x7FFF<<8)|0x01 = 0x017FFF01
AT   : 41 54 0B FF F8 0C 08 7F FF 7F FF 05 1E 19 99 0D 0A
SLCAN: T017FFF0187FFF7FFF051E1999\r        (比較用。27 バイト)
```

### 2.5 アダプタの設定（AT コマンド）について

- RobStride は AT コマンド（`AT+...`）による設定手順を**公開していない**。調べた PDF（EL05 マニュアル、Studio 手順書、CANHUB 説明書）にも、参照コードにも記載がない。
- 同系統の汎用 USB 転 CAN モジュール（YourCee / WitMotion 型）では、`AT+CG` で設定モード、`AT+AT` で AT（データ）モード、
  `AT+USART_PARAM` で UART 設定、という情報がある。ただし CSDN の記事は取得できず（HTTP 521）、
  検索結果の断片しか確認できていない（`docs/vendor/usb_can/web_notes_at_serial_adapter.md` §3）。
- 一方、第三者の GUI 実装（dawnmainak/Robstride_Control_Gui `transport.py`）には次のコメントがある。
  - "Do NOT send an 'AT+AT' handshake: on the official RobStride USB-CAN module that command switches it into
    AT *command* mode, which stops it forwarding CAN frames"
  - "The module powers up ready to pass 'AT'-framed CAN frames through, exactly like the vendor GUI, which sends no handshake."
- **結論**
  - 本ツールは設定コマンドを一切送らない。ポートを開いたら入力バッファを捨てるだけにする。
  - CAN ビットレートは工場設定の 1 Mbps が前提になる。モータ側のビットレートは通信タイプ 23 で変更できる（EL05 PDF p.46）が、変えるとアダプタと合わなくなるので GUI からは原則として禁止する。
  - DIP1 = ON はブートモードで通信できない。DIP2 = ON で 120Ω 終端（EL05 PDF p.14）。接続できないときの確認項目として GUI に表示する。
- 同じ第三者コードは、CH340 がモータの大電流時に一時的に "readiness to read but returned no data" 例外を出すことも報告している。
  これは切断として扱わず、再試行するべきである。

---

## 3. 公式 SDK / サンプルのバス層の構造と品質上の問題

### 3.1 el_a3_sdk：`RobstrideCanDriver`（SocketCAN）と `SlcanCanDriver`（SLCAN、Windows でも動く）

**構造**

- 2 つのドライバは公開 API が同一で、`interface.py` が `backend="socketcan" | "slcan"` で切り替える（`sdk/el_a3_sdk/interface.py:154-169`）。
- SocketCAN 版の設定：
  - raw ソケットで開き、`CAN_RAW_RECV_OWN_MSGS=0` と `settimeout(0.1)` を設定する（`sdk/el_a3_sdk/can_driver.py:95-110`）。
  - 29-bit ID は `(type<<24)|(data2<<8)|target` に `CAN_EFF_FLAG` を付けて作る（`:253-256`）。
- SLCAN 版の設定：
  - pyserial を既定 2,000,000 bps、`timeout=0.1` で開く（`sdk/el_a3_sdk/slcan_can_driver.py:65-68`、`:109-114`）。
  - 初期化は `C` → `S8` → `O` で、応答は確認しない（`:117-133`、ビットレート表は `:33-43`）。
  - 送信は常に DLC 8 の `T%08X8<hex>\r`（`:358-362`）。
- **受信**
  - デーモンスレッド 1 本（`can_driver.py:126-155` / `slcan_can_driver.py:172-210`）。
  - 通信タイプで振り分け、最新値を `dict` に `threading.Lock` 付きで上書きする（`can_driver.py:157-186`、`:206-207`）。
  - 任意でフィードバックコールバックを呼ぶが、呼ぶのは受信スレッド上で、例外は握りつぶす（`:209-213`）。
- **要求と応答**
  - `read_parameter` は、結果 dict からそのモータのエントリを消し → 送信 → 5 ms 間隔で最大 0.5 s ポーリングし、`param_index` が一致したら返す（`can_driver.py:375-403`）。
  - バージョン照会（type 26）は、`_awaiting_version[motor]` フラグを立てたうえで、**次に来た type 2 フレームをバージョン応答とみなす**（`:169-171`、`:437-456`）。
- **送信**
  - 送信ロックを保持したまま、失敗時は 200 µs から倍々のビジーウェイトで最大 5 回再試行する（`can_driver.py:258-285`）。
- **上位層**
  - `ELA3Interface` が 200 Hz の制御スレッドを持つ。周期は `perf_counter` と `time.sleep` で取る（`sdk/el_a3_sdk/interface.py:360-379`）。
  - 1 秒ごとに `is_bus_healthy` を確認する（`:372-374`）。

**品質上の問題**

1. **SLCAN の受信が高レイテンシになる**
   - `serial.read(256)` を `timeout=0.1` で呼んでいる（`slcan_can_driver.py:112`、`:197`）。
   - pyserial は 256 バイトたまるかタイムアウトするまで戻らない。そのため低トラフィック時には最大 100 ms 遅れ、7 モータ×200 Hz 程度でも数 ms 遅れる。
   - `read(ser.in_waiting or 1)` とするべきである。
2. **受信を奪い合う競合**
   - `check_bus_health()` は制御スレッドから `F\r` を送り、`serial.read(10)` で応答を読む（`slcan_can_driver.py:541-559`）。
   - 同じポートを受信スレッドも読んでいるので、フィードバックのバイトを横取りしたり、`F` 応答を取り逃したりする。
   - これが制御ループから 1 秒ごとに呼ばれる（`interface.py:372-374`）。
3. **応答の照合が不十分**
   - param 応答はモータ単位で 1 スロットしかなく、同じモータへの並行した読み込みが競合する（`can_driver.py:215-231`、`:388-403`）。
   - バージョン応答は「次の type 2」とみなしているだけなので、通常のフィードバックを取り違える（`:169-171`）。
   - 締め切りの計算に `time.time()`（壁時計）を使っていて、単調時計ではない（`:396`）。
4. **ID の範囲がハードコードされている**
   - フィードバックは `1 <= motor_id <= 16` のときだけ処理する（`can_driver.py:168`、`slcan_can_driver.py:258`）。
   - 工場出荷 ID 127（0x7F）のモータのフィードバックは捨てられる。
5. **コードの重複**
   - 2 つのドライバは、フレーム解析・コマンド生成・統計が行単位でほぼ同じコピーである（例：`can_driver.py:188-249` と `slcan_can_driver.py:279-345`）。
   - 「フレームの入出力（トランスポート）」と「プロトコル」が分離されていない。
6. **SLCAN の応答やエラーを処理していない**
   - `z`/`Z` の ACK、BEL（0x07）のエラー、初期化応答を確認していない。
   - 行は `\r` だけで分割している（`slcan_can_driver.py:201-204`）。
7. **タイムスタンプが粗い**：受信スレッドで解析したときの `time.time()` を使っている（`can_driver.py:204`）。HW タイムスタンプを使っていない。
8. **MotorStudio `ArmWorker`（QThread）の問題**
   - 50 Hz でキャッシュを読むだけである（`sdk/MotorStudio/backend/arm_worker.py:59`、`:81-96`、`:519-597`）。
   - コマンドは文字列ディスパッチのキュー（`:78-79`、`:114-161`）で、同じスレッド上で逐次に実行する。
   - そのため、1 つのコマンドがブロックすると 50 Hz のポーリングも止まる。例えばモータ走査では、7 モータ×（バージョン照会 0.5 s ＋ 電圧照会）を順に待つ（`:450-475`）。
   - 良い点：シミュレーションモードがある（一次遅れの位置モデル、`:599-609`）。
9. **`can_utils.py` は Linux 専用**
   - sysfs の読み取り、`ip link`、sudo / pkexec を使う（`sdk/MotorStudio/utils/can_utils.py:13-127`）。
   - Windows で使えるのはシリアルポートの列挙（`serial.tools.list_ports`）だけである（`:129-143`）。

### 3.2 python_sample：`robstride_dynamics/bus.py`（RobStride 公式 Python SDK）

**構造**

- python-can を使う（依存は `numpy`、`python-can`、`tqdm`、`sample/pyproject.toml:12-16`）。
- ただし `can.interface.Bus(interface="socketcan", ...)` が固定されている（`bus.py:104-108`）。
- 受信スレッドはない。**すべて同期的な「送信 → その場で受信」**で動く。
  - 例：`enable` / `disable` / `write` はそれぞれ type 2 の応答を待つ（`bus.py:395-409`、`:205-206`）。
  - MIT 指令は `write_operation_frame` で送信し、応答は `read_operation_frame` で別途受け取る（`:430-495`）。
- ホスト ID は 0xFF で、「ホスト ID はアクチュエータ ID より大きくすべき」というコメントがある（`bus.py:63-65`）。el_a3_sdk は 0xFD を使う。

**品質上の問題**

1. **Windows で動かない**：`interface="socketcan"` が固定である。python-can を使っているので差し替えは容易。
2. **応答の照合がない**
   - `receive()` は最初に来た拡張フレームをそのまま返し、モータ ID や通信タイプを確認しない（`bus.py:244-280`）。
   - 複数モータ、能動報告（type 24）、遅れて届いた応答が混ざると簡単に取り違える。
   - その結果は `assert`、`print`、例外で処理される（`:319-324`）。
3. **タイムアウトの扱いが誤っている**
   - ループの中で毎回 `recv(timeout=timeout)` を全量の timeout で呼んでいる（`bus.py:261-266`）。
   - `read()` は `receive()` を timeout なしで呼ぶので、応答がないと**永久にブロック**する（`:366`）。
4. **例外で済ませている**
   - fault フレームを受けると `RuntimeError` を投げる（`bus.py:326-350`）。GUI 用としては、状態として保持するほうがよい。
5. **疑わしいコメント**
   - 「type 0 は非拡張フレームとして認識される」とあり、標準フレームを黙って捨てている（`bus.py:268-272`）。
6. **その他**：`print` を多用している。`__del__` で切断している（`bus.py:78-80`）。`scan_channel` は 1〜254 を 0.1 s ずつ順に待つので、最大約 25 s かかる（`:130-147`）。

### 3.3 can_usb_conversion（Qt）

- AT フレームと CAN ID の相互変換を手で確かめるための UI ツールで、シリアル I/O は持たない（`conv/mainwindow.cpp` 全体）。
- 検証上の価値は、§2 の `<<3 | 0x04` と、4 バイト ID をビッグエンディアンで並べる規則が確定することにある。
- 問題点：
  - フィールド名が誤っている（§2.3）。
  - 長さと終端を確認していない（`:168`）。
  - `QRegExp` を使っている（Qt6 では削除済み）。

---

## 4. スループットとレイテンシ

### 4.1 CAN バス（1 Mbps、拡張フレーム、DLC 8）

- ビット数：SOF 1、ID 11、SRR 1、IDE 1、ID 18、RTR 1、r1/r0 2、DLC 4、データ 64、CRC 15、区切り 1、ACK 2、EOF 7、IFS 3 で、**合計 131 bit**。
- スタッフビットが入り得る区間は 118 bit なので、最悪で +29 bit、**合計 160 bit** になる。
- よって 1 フレームは **131〜160 µs**、バス上限は **約 6250〜7630 fps**（計算値）。
- MIT 制御 1 サイクルは、各モータに type 1 を 1 枚送り、type 2 が 1 枚返る（EL05 PDF p.41：type 1 の "Response frame: Response motor feedback frame"）。
- **1 モータあたり約 262〜320 µs / サイクル**になる。

| モータ数 N（1 バス） | 1 サイクルのバス時間（公称〜最悪） | 理論上限（100% 負荷） | 実用目安（60% 負荷） |
|---|---|---|---|
| 1 | 0.26〜0.32 ms | 約 3.1〜3.8 kHz | 約 2.3 kHz |
| 2 | 0.52〜0.64 ms | 約 1.6〜1.9 kHz | 約 1.1 kHz |
| 4 | 1.05〜1.28 ms | 約 780〜950 Hz | 約 570 Hz |
| 7 | 1.83〜2.24 ms | 約 450〜545 Hz | **約 330 Hz** |
| 8 | 2.10〜2.56 ms | 約 390〜475 Hz | 約 290 Hz |
| 16 | 4.19〜5.12 ms | 約 195〜240 Hz | 約 140 Hz |

- パラメータ読み書き（type 17 / 18）も、要求 1 枚と応答 1 枚の往復になる。
- 能動報告（type 24）は既定で 10 ms 周期、つまり 1 モータあたり 100 fps である。周期は `EPScan_time`（0x7026）で変更でき、マニュアルは「既定値 1 が 10 ms」だが、**実測は 10 + 5·N ms**（N=0 → 10 ms、N=1 → 15 ms。el05_protocol.md §8.0）。（EL05 PDF p.46、p.49-50）
  制御指令と併用するとバス負荷に加算される。
- モータ側の応答遅延（ファーム処理時間）は資料に記載がなく、**実測が必要**。
- 参考値：Seeed Wiki は Python で 50〜100 Hz、C++ で 200 Hz、Python のレイテンシ 2〜5 ms を目安としている（https://wiki.seeedstudio.com/robstride_control/ ）。
  el_a3_sdk の制御ループは 200 Hz（`interface.py:84`、`arm_worker.py:291-296`）。python_sample の MIT 例は 200 Hz（`sample/README.md:116`）。

### 4.2 AT シリアル（921600 bps）のオーバヘッド

- 1 フレームは 17 バイト（ヘッダ 2 + ID 4 + DLC 1 + データ 8 + 終端 2）。8N1 なら 170 bit で、**184.5 µs/フレーム**、上限は**約 5420 fps/方向**（全二重）。
- **CAN の公称上限（約 7630 fps）より遅い**。バスが混んでいると、アダプタが受信したフレームをシリアルで転送しきれず、アダプタ内部で溢れる可能性がある。アダプタのバッファ容量は非公開。
- N モータが同期して応答すると、片方向あたり N × 184.5 µs かかる。7 モータでは 1.29 ms（上限約 775 Hz）、16 モータでは 2.95 ms（上限約 340 Hz）。制約はバス側とおおむね同程度で、シリアル側の方がやや厳しい。
- 比較：
  - SLCAN（ASCII）は 27 バイト/フレーム。CANable のような USB CDC では仮想ボーレートなので実質 USB 帯域で決まるが、UART 経由の SLCAN では 1.6 倍重い。
  - gs_usb は USB bulk で 1 フレーム 20〜24 バイトのバイナリ構造体なので、帯域上の問題はない。

### 4.3 Windows の USB シリアル遅延

- USB Full-Speed の bulk 転送はホストが 1 ms フレーム単位でスケジュールする。そのため、アダプタから PC まで片道で概ね **0.1〜1 ms 以上の量子化遅延**が加わる（一般論）。
- FTDI には「latency timer」（既定 16 ms、1 ms まで下げられる）がある。既定のままだと 1 バイトのループバックで 15〜16 ms、1 ms 設定で約 1 ms、CP210x は約 8 ms という実測がある（https://heavydeck.net/blog/measuring-serial-port-latency/ ）。
- **CH340 にはユーザが設定できる latency timer がない**。公開された実測値も見つからなかった。
  - 本ツールには「往復時間計測」（type 0 や param read の RTT のヒストグラム）を診断機能として組み込み、実機で測ること。
  - 実装の目安として、RTT は 1〜3 ms（推定）とみておく。
- **設計上の含意**
  - 同期往復（送信 → 応答待ち → 次のモータ）を N 回繰り返すと、RTT × N で律速される。例えば RTT 2 ms で 7 モータなら 14 ms、約 70 Hz しか出ない。
  - **N 台分の指令を 1 回の `write()` でまとめて送り、応答は受信スレッドが非同期に回収する（パイプライン化）**のが必須である。
- Python 側について：
  - Python 3.11 以降の Windows では `time.sleep` が高分解能の waitable timer を使う（https://docs.python.org/3/library/time.html#time.sleep ）。
    3.10 以前の 1〜15.6 ms の粒度問題は緩和されたが、ジッタは残るので、周期は `perf_counter` 基準の絶対時刻スケジュールで作るべきである。
  - 受信スレッドでの 1 フレームあたりのデコードは Python で数 µs から十数 µs（推定）なので、数千 fps なら 1 スレッドで足りる。GIL の競合を避けるため、GUI の描画処理は受信スレッドに入れない。

### 4.4 GUI 描画と制御周期への示唆

- **描画**は 30〜60 Hz で十分である（人の知覚と pyqtgraph の実用上限）。データは制御周期（100〜500 Hz）でリングバッファに蓄え、描画時に間引く（min / max 間引きなど）。
- **監視だけ（制御しない）**のとき：能動報告（type 24、10 ms）で 100 Hz のフィードバックが得られ、指令を送る必要がない。
- **制御**するとき：現実的な目標は次のとおり。
  - AT アダプタで 1〜7 モータ：100〜200 Hz（安定動作）。上限は 1 台で約 500 Hz 程度（推定。RTT とジッタで決まる）。
  - 7 台以上で 500 Hz 以上が必要なら、CANHUB で複数バスに分ける、または PCAN を使う。
- **安全**：`canTimeout`（0x7028、20000 = 1 s、0 で無効）を設定すると、指令が途絶えたときにモータが reset モードに移る（EL05 PDF p.31、p.49）。
  GUI がフリーズしたときやケーブルが抜けたときの保護として、制御中は有効にすることを推奨する。

---

## 5. 推奨：トランスポート抽象の設計（擬似シグネチャのみ）

### 5.1 層の構成

```
GUI (Qt main thread) ── 30–60 Hz snapshot pull / Qt signals
   │
MotorService / BusManager ── プロトコル層（エンコード/デコード、状態集約、要求応答照合、制御スケジューラ）
   │                        ← 1 本の RX スレッド + 1 本の TX/制御スレッド
CanTransport (抽象) ── フレーム I/O のみ（プロトコル非依存）
   ├─ AtSerialTransport      : 公式 USB-CAN（pyserial, 921600, AT フレーム）
   ├─ PythonCanTransport     : python-can の任意 interface（gs_usb / candle / slcan / pcan / kvaser / socketcan / virtual）
   └─ SimTransport           : 仮想バス + MotorSimulator（ハードウェア不要）
```

### 5.2 `CanFrame`（値オブジェクト）

- `arbitration_id: int`（29 bit）
- `data: bytes`（0〜8 バイト）
- `is_extended: bool`
- `timestamp: float`：`time.monotonic()` を基準にする。HW タイムスタンプがあればそれを変換して使う。
- `channel: str | int`：CANHUB の多チャネル用
- `direction: RX | TX`：ログ用

### 5.3 `CanTransport`（抽象インタフェース）

- `open() -> None`、`close() -> None`、`is_open: bool`
- `send(frame) -> None`、`send_many(frames: Sequence[CanFrame]) -> None`
  - `send_many` は AT の場合も **連結 write はしない**。フレーム間隔 0.5 ms 未満は取りこぼす実測(hw_bench.md)のため、1 フレームずつ最小 1 ms(`min_frame_gap_s`)のペーシングで書く(N フレームで約 N ms)。
- `recv(timeout: float) -> CanFrame | None`：ブロッキングで、スレッドから呼ぶ前提
- `capabilities -> TransportCaps`
  - `hw_timestamp`、`bus_state`、`error_frames`、`channels`、`max_fps_hint`
  - AT の場合は `bus_state=False`、`max_fps_hint = 1 / min_frame_gap_s`(既定 1 ms → 1000。ペーシング無効時は約 5400)
- `stats() -> TransportStats`
  - 送受信数、パース異常・再同期の回数、シリアル例外の回数、最後の受信時刻
- `bus_state() -> BusState | None`：対応しない実装は `None` を返す
- 例外は `TransportError` に一本化する。一時的なエラー（CH340 の "returned no data" など）は内部で再試行する。

### 5.4 `AtSerialTransport` の要点

- `serial.Serial(port, 921600, timeout=小, write_timeout=小, exclusive=True)` で開く。開いた直後に `reset_input_buffer()` を呼ぶ。**AT 設定コマンドは送らない**（§2.5）。
- 受信は `read(in_waiting or 1)` でバイト列をためる。パーサは `AT` 探索 → DLC ≤ 8 の確認 → 全長の確保 → 終端 `0D 0A` の検証 → ID の bit2（IDE）判定の順に進み、失敗したら 1 バイト進めて再同期する。
- ポートの列挙には `serial.tools.list_ports` を使い、VID:PID 1A86:7523（CH340）を優先表示する。1A86:7523 は CH340 の一般的な ID で、推定。
- 抜き差しを検知したら自動で再接続する。再接続したらプロトコル層に通知し、全モータを「状態不明」にする。
- 可能なら python-can の BusABC プラグイン（`interface="robstride_at"`）としても公開し、ロガーやビューアの資産を使えるようにする。

### 5.5 `PythonCanTransport`

- `can.Bus(interface=..., channel=..., bitrate=1_000_000, **kw)` を包む。受信時刻は `msg.timestamp` を単調時計に揃える。
- 設定プロファイルの例：
  - CANHUB：`gs_usb`（channel index）または `candle`
  - CANable：`slcan`（`COMx`）
  - PCAN：`pcan`（`PCAN_USBBUS1`）
  - Linux：`socketcan`（`can0`）
- 多チャネル（CANHUB 5ch）は、`channel` ごとに Transport インスタンスを作り、BusManager が「モータ → チャネル」の対応表を持つ。

### 5.6 プロトコル層（BusManager / MotorService）の要点

- **RX スレッドは 1 本**。フレームをデコードし、通信タイプごとに次のように振り分ける。
  - type 2 / 24：モータ状態の最新値とリングバッファを更新する。
  - type 17：パラメータ応答
  - type 21：故障
  - type 0：ID / UID
  - 不明なフレーム：生ログへ
- **要求と応答の照合**
  - 待ち表を `PendingKey = (channel, motor_id, comm_type, param_index | None)` → `Future` で持ち、単調時計の締め切りで失効させる。
  - 同じキーへの要求が重なったら直列化するか拒否する。
  - バージョン照会のように「type 2 で返る応答」は、応答側の ID や data を見て判別する。判別できないときは、そのモータへの他の要求を止めて排他的に待つ。
- **TX**
  - 優先度付きキューにする。E-stop / Disable が最優先、次に周期制御、次にパラメータ読み書き。
  - 周期制御は、N 台分の type 1 をまとめて `send_many` で送る。
- **制御スケジューラ**：`perf_counter` による絶対時刻の周期で動かす。周期超過（オーバーラン）とジッタを統計に出す。
- **GUI への API**
  - 非同期（`Future` またはコールバック）だけにし、GUI スレッドで待たない。
  - 状態は `snapshot() -> dict[motor_id, MotorState]` として 30〜60 Hz で引く。
- **ログ**：生フレームを candump / ASC 形式で記録する。python-can の `can.Logger` / `can.LogReader` を流用すると再生（リプレイ）もできる。

### 5.7 シミュレーション（仮想バス）

- **SimTransport** は、python-can の `interface="virtual"`（プロセス内の仮想チャネル）か、自前のキューで作る。そこへ **MotorSimulator** を接続する。
  - MotorSimulator は ID ごとに状態を持つ。内容は、mode（reset / cali / motor）、run_mode、位置・速度・トルク（1 自由度の慣性と粘性モデル、MIT の PD 則）、温度、パラメータ表、故障注入である。
  - 応答する通信タイプ：0（UID）、1 → 2、3 / 4 → 2、6、7、17 / 18、21、22、24（周期報告）。
  - 設定で遅延（例：0.2〜2 ms）、ジッタ、フレーム欠落、故障ビット、断線を注入できるようにする。
- **AT レベルの試験**
  - `AtSerialTransport` のパーサ単体テストでは、§2.4 の実例バイト列、分割受信、ノイズの混入、データ内の `41 54` や `0D 0A` を入力として与える。
  - Windows で結合試験をするなら、仮想 COM ペア（com0com など）の片側に AT 版のモータシミュレータをつなぐ。
- **リプレイ Transport**：実機で記録したログを時刻どおりに再生し、GUI の描画や解析を再現できるようにする。
- 既存の例：MotorStudio の一次遅れシミュレーション（`arm_worker.py:599-609`）。本設計では、これをバスより下の層（フレームレベル）に置き、プロトコル層を含めて試験できるようにする。

### 5.8 アダプタ推奨のまとめ

1. **第一対応**：RobStride 公式 USB-CAN（AT、CH340）。
   - 教育現場で手元にある可能性が最も高い。
   - キャリブレーション、ID 変更、OTA では Motor Studio と併用するので、同じ機材で済むほうがよい。
   - `AtSerialTransport` を自前で実装する。
2. **第二対応**：python-can 経由の汎用アダプタ。CANHUB（gs_usb / candle）、CANable（slcan / gs_usb）、PCAN。
   - 多モータで高周期が必要な場合、HW タイムスタンプが必要な場合、Linux と共用したい場合に使う。
   - 7 台を 500 Hz 以上で回すなら、CANHUB でバスを分割するのが最も安価である。
3. **常時**：`SimTransport`。ハードウェアなしでの開発、CI、GUI のデモに使う。

---

## 6. 追加トランスポートの使い方(`pycan` / `replay`、Phase 4-N)

実装: `src/edulite05/core/transport/pycan.py`, `replay.py`。どちらも `registry`(`"pycan"` / `"replay"`)に登録済みで、GUI の接続フォーム(`options_schema`、ラベルは日本語)から選べる。**公式 USB-CAN(COM7)には `robstride_at` を使う**(`pycan` は他社アダプタ用)。

### 6.1 `pycan`(python-can)

- python-can は**任意依存**: `uv sync --extra pycan`(`pyproject.toml` の `[project.optional-dependencies] pycan = ["python-can>=4.5"]`)。未導入でもアプリは動き、`registry.available()` の `pycan` は `available=False`、`unavailable_reason="python-can が未導入です(…)"`(UI は選択不可にして理由を表示する)。無理に `open()` すると `TransportError`(日本語)。
- 引数: `interface`(必須)、`channel`、`bitrate`(既定 1 Mbps)、`min_frame_gap_s`(既定 0 = ペーシングなし)、`index`(gs_usb / candle の複数接続時のデバイス番号)。その他の `**kw` は `can.Bus` へそのまま渡す。
- 受信時刻は `msg.timestamp` ではなく受信時の `core.clock.now()`。標準(11 bit)・拡張(29 bit)フレーム対応。リモート/エラーフレームは捨てて `stats().parse_errors` に数える。`caps.hw_timestamp=False`。
- 例(いずれも未実機検証。`virtual` のみ自動テスト済み):

| アダプタ | `interface` | `channel` | 備考 |
|---|---|---|---|
| CANable(slcan ファーム) | `slcan` | `COM5` | ボーレートが要るときは `ttyBaudrate=115200` 等を kw で渡す |
| candleLight / CANable(gs_usb ファーム)、CANHUB | `gs_usb`(または `candle`) | 空(自動検出) | 複数台は `index=0`。Windows は libusb(WinUSB ドライバ、Zadig 等)が必要 |
| PEAK PCAN-USB | `pcan` | `PCAN_USBBUS1` | PCAN-Basic ドライバが必要 |
| Linux | `socketcan` | `can0` | |
| テスト | `virtual` | 任意の名前 | 同じ `channel` の `Bus` 同士で送受信できる(実機不要) |

```python
from edulite05.core.transport import registry
t = registry.create("pycan", interface="slcan", channel="COM5")   # CANable
t = registry.create("pycan", interface="gs_usb", index=0)         # candleLight
t = registry.create("pycan", interface="pcan", channel="PCAN_USBBUS1")
t.open()
```

### 6.2 `replay`(ログ再生)

- `ReplayTransport(path, speed=1.0, loop=False)`。`FrameLogWriter` が出す candump -L 互換ログの **RX だけ** を元の時刻間隔 ÷ `speed` で返す。TX 行と `send()` は捨てる(`caps.read_only=True`、`stats().tx_frames` に捨てた数)。
- 再生の起点は最初の `recv()`。返すフレームの `timestamp` は再生時点の `clock.now()`。終端では `recv()` が timeout 待って `None` を返す(`stats().finished=True`)。`loop=True` は先頭へ戻る(つなぎ目はログの平均フレーム間隔、`stats().loops`)。進捗は `stats().position / total`(`progress`)。
- ログが読めない・RX が 1 つもない場合は `open()` が `TransportError`。
- 時間精度は Windows のスレッド待ちの粒度に依存する(数 ms のジッタ。遅れたフレームは直後にまとめて返る)。

```python
t = registry.create("replay", path="user_data/logs/run1.log", speed=2.0, loop=False)
```

GUI での使い方(接続ポップオーバー → 「ログ再生」): `CoreBridge` は `caps.read_only` を見て接続を **読み出し専用** にする(`ConnectionInfo.read_only`)。再生ではスキャン(TX)に応答が返らないので、`motor_ids_in_log(path)` でログの RX に現れるモータ ID(type 0 / 2 / 17 / 21 / 24 の bit15..8)を先に `Session.motor()` へ登録し、スキャンは行わずにその一覧を見せる。有効化・運転・パラメータの読み書き・保守操作は `CoreBridge` が拒否し(ログに出す)、画面も押せなくする。ログの最後まで再生すると、モータは「未接続」になる(`loop=True` なら繰り返す)。

---

## 付録：参照したファイルと資料

- `docs/vendor/usb_can/USB_CANHUB使用说明V1.0.pdf`（全 12 ページ。テキストは pdftotext で抽出し、p.8-12 は画像を確認した）
- `docs/vendor/motorstudio/Instructions for using the Studio_241122.pdf`（p.1-3：接続関連）
- `docs/vendor/el05/EL05User Manual260713.pdf`（p.14-17、p.30-31、p.40-41、p.45-50）
- `sdk/el_a3_sdk/{can_driver.py, slcan_can_driver.py, protocol.py, interface.py}`、`sdk/README_en.md`
- `sdk/MotorStudio/utils/can_utils.py`、`sdk/MotorStudio/backend/arm_worker.py`
- `sample/robstride_dynamics/{bus.py, protocol.py}`、`sample/README.md`、`sample/pyproject.toml`
- `conv/{mainwindow.cpp, mainwindow.h, README.md}`
- Web で得た情報は次の 2 ファイルに要約した（URL 付き）。
  - `docs/vendor/usb_can/web_notes_at_serial_adapter.md`
  - `docs/vendor/usb_can/web_notes_python_can_windows.md`
