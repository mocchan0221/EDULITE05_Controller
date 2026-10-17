# vendor/ — 一次資料の保管庫

メーカー(RobStride Dynamics / 北京灵足时代科技)公開資料のコピー。**ここにあるファイルは編集しない**。
要約・解釈は `docs/research/` に書く。

**リポジトリには含めていない**(再配布の許可が明確でないため。`.gitignore` で除外)。追跡しているのはこの README と `usb_can/web_notes_*.md`(自分で書いた要約)だけ。手元に揃えるときは、下の表の出典から取得して同じパスに置く。

取得日: 2026-10-01(すべて GitHub `RobStride` org の default branch, `--depth 1` clone から)

| パス | 内容 | 出典 |
|---|---|---|
| `el05/EL05User Manual260713.pdf` | EL05 ユーザーマニュアル(英語, 2026-07-13版) | https://github.com/RobStride/Product_Information (`Product Literature/EL05/`) |
| `el05/EL05使用说明书260713.pdf` | 同 中国語原本(英訳の曖昧さはこちらで確認) | 同 (`产品资料/EL05/`) |
| `el05/el05.stp` | EL05 外形 STEP モデル | 同 |
| `common/灵足时代RS系列产品规格介绍(2026.09.17).pdf` | RS/EL シリーズ仕様書 | 同 (repo root) |
| `common/OTA*.pdf` | OTA(ファーム更新)プロトコル説明 EN/CN | 同 (`OTA/`) |
| `motorstudio/*.pdf` | 公式上位機 MotorStudio 取扱説明書 EN/CN (2024-11-22版) | https://github.com/RobStride/MotorStudio |
| `usb_can/USB_CANHUB使用说明V1.0.pdf` | USB-CANHUB 取扱説明 | Product_Information (`产品资料/canhub/`) |
| `usb_can/web_notes_*.md` | Web 調査の要約(AT シリアルアダプタ、python-can の Windows 対応)。各ファイル冒頭に元 URL | 各種 Web ページ(2026-10-01 閲覧) |
| `reference_code/python_sample/` | 公式 Python サンプル `robstride_dynamics` | https://github.com/RobStride/Python_Sample |
| `reference_code/el_a3_sdk/` | EL-A3 アーム用 SDK + PyQt6 版 MotorStudio(EL05 使用機) MIT License | https://github.com/RobStride/EDULITE_A3 (`el_a3_sdk/`) |
| `reference_code/el_a3_ros_driver/` | EL-A3 ROS2 ハードウェアIF の CAN ドライバ部 (C++/Python) MIT License | 同 (`el_a3_ros/el_a3_hardware/`, `el_a3_sdk/el_a3_sdk/`) |
| `reference_code/can_usb_conversion/` | USB-CAN "AT" フレーム ⇔ CAN ID 変換ツール (Qt) | https://github.com/RobStride/CAN-USB-data-conversion |

未取得(必要になったら追加): `RobStrideSPC`, `SampleProgram`(STM32 サンプル), `robstride_ros_sample`, `robstride_actuator_bridge`, CANopen 資料, 他 RS 機種マニュアル。
