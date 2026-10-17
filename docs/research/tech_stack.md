# EDULITE05 Controller 技術スタック調査

- 調査日: 2026-10-01
- 対象: RobStride EduLite 05 (EL05) CAN アクチュエータ用デスクトップ GUI（Windows 11 主、Linux 副）
- 前提環境（実機 PC で確認済み）: Python 3.12.2 / uv 0.11.23（uv 管理の CPython 3.14.6 も導入済み）/ Node v20.11.1 / git 2.45。Rust・FreeCAD は未導入。
- 本書はアプリケーションコードを含まない。バージョンは 2026-10-01 時点の PyPI / npm で確認した値。

---

## 1. 要求の整理

| 区分 | 要求 | 技術選定への影響 |
|---|---|---|
| 制御 | JOG、速度・トルク・位置制御、MIT（インピーダンス）制御、パラメータ R/W、フォルト処理 | CAN フレームの確実な送受信、非常停止の即時性 |
| 可視化 | オシロスコープ的な複数チャネル実時間グラフ（100〜1000 Hz 取得） | 描画ライブラリの性能、取得スレッドと描画の分離 |
| 3D | モータ／関節のリアルタイム 3D CG（ベンダ STEP → メッシュ） | glTF/STL ローダ、姿勢更新のコスト |
| 通信 | USB-CAN アダプタ（CH340 USB シリアル + GD32、ベンダ独自 "AT" フレーム、SLCAN 版もあり） | pyserial/python-can、Windows の USB シリアル遅延 |
| 拡張 | 後からのプラグイン追加（制御パネル、シーケンス/スクリプト、複数モータ） | プラグイン API、スクリプト実行環境 |
| 起動 | プロジェクトディレクトリからソースのまま最小手順で起動 | `uv run` 一発、`.bat` |
| 制約 | 「起動が簡単でも機能を制限しないこと」 | 軽量フレームワークの天井に注意 |

### 1.1 通信帯域の概算（選定の前提）

- ベンダ USB-CAN の AT フレーム: `"AT"(2) + ID(4) + DLC(1) + data(8) + "\r\n"(2)` ≈ 17 byte。921600 bps（8N1 で約 92 kB/s）なら片方向あたり最大約 5,400 frame/s。
  - アダプタは CH340 + GD32、最大 921600 bps と公表 ([OpenELAB 製品ページ](https://openelab.io/products/robstride-can-to-usb-debugging-module))。EL-A3 SDK の SLCAN ドライバは 2 Mbaud を前提にしている（`docs/vendor/reference_code/el_a3_ros_driver/slcan_can_driver.py`）。
- CAN 1 Mbps・拡張フレーム・8 byte は、スタッフィングを含めて約 130〜150 bit なので、バス上限は約 6,500 frame/s。
- MIT 制御を 1 kHz で回し、指令と応答が各 1 frame なら **1 モータで 2,000 frame/s（バス負荷約 30%）**。1 kHz で回せるのは 1〜2 台まで。複数モータで 1 kHz を狙うなら、モータ側の能動報告（通信タイプ 24 `ACTIVE_REPORT`、`EPScan_time` = 0x7026）を使ってポーリングをやめる必要がある。
- USB フルスピードのバルク転送は 1 ms フレーム周期で動く。PC→モータ→PC の往復は実測で 1〜3 ms 程度になる見込み（要実測）。**PC 側の閉ループを 1 kHz で回すのは帯域的にも遅延的にもぎりぎり。現実的な目標は「取得 500〜1000 Hz、指令 100〜500 Hz」**。サーボループ自体はモータ内部で回っており、PC は目標値と MIT ゲインを流すだけ、という位置付けにする。

---

## 2. ベンダ MotorStudio（EL-A3 アームツール）の評価

対象: `docs/vendor/reference_code/el_a3_sdk/MotorStudio/`（PyQt6 + pyqtgraph + PyVista/pyvistaqt、MIT ライセンス、約 7,400 行）

| 項目 | 実装 | 評価 |
|---|---|---|
| 構成 | `QMainWindow` に固定の `QDockWidget` とタブ 6 枚。シグナル配線はすべて `main_window.py` に手書き | 小規模なら読みやすい。プラグイン機構はなく、パネル追加のたびに MainWindow を編集する必要がある |
| バックエンド | `ArmWorker(QThread)` が 50 Hz の `time.sleep` ループで SDK をポーリング。コマンドは `Queue` に文字列名で積み、巨大な if/elif で分岐 | 取得は 50 Hz 止まり。バックエンドが PyQt6 に依存しているため、ヘッドレスのテストや別 UI への流用がしにくい。sim モードは Worker 内の分岐として実装 |
| データバッファ `data_buffer.py` | 500 サンプル × 7ch の numpy リングバッファ。`get_data()` のたびに `np.roll(np.arange())` の fancy index と `.copy()` を 5 配列分行う | 「numpy 代入の原子性でスレッド安全」とコメントがあるが、`_index` とデータ配列の更新が非原子的なので、読み手が書きかけの行を読みうる。500 サンプルでは問題にならないが、1 kHz × 10 秒 = 10,000 サンプル級には向かない |
| グラフ `monitoring_panel.py` | 2×2 の `PlotWidget` に 7 本ずつ計 28 本のカーブ。20 Hz の `QTimer` で全データを `setData`。`antialias=True` を全体に設定 | pyqtgraph の典型的な使い方で妥当。ただし `clipToView` / `setDownsampling` / `skipFiniteCheck` を使っておらず、全体 antialias も重いので、高レート・長時間窓では伸びない |
| 3D `viewer_3d.py` | pyvistaqt `QtInteractor`（MSAA 8、Phong、3 灯、グラデ背景）で URDF の STL を表示。姿勢更新は **全頂点を CPU 上で `R @ points` して書き戻す**方式で、10 Hz に間引き。`vtkCellPicker` によるリンクのドラッグ操作もある | 見た目の質は十分で、ピッキングも実装済み。ただし姿勢更新は `actor.user_matrix`（4×4 行列の差し替え）で済むところを頂点ごと計算しており非効率。その結果が 10 Hz 制限。VTK（約 80 MB）の依存が重い |
| ライセンス | コードは MIT。依存の PyQt6 は GPL-3.0 | 流用自体は自由。PySide6 に移すならシグナル名の置換（`pyqtSignal` → `Signal`）程度で済む |

**結論**: 「PyQt 系 + pyqtgraph + VTK」という骨格は妥当で、実績のある組み合わせ。ただし高レート取得、プラグイン、テスト容易性、3D 更新効率はいずれも弱く、**構造はそのまま流用せず、技術要素だけを採る**のがよい。

---

## 3. 候補スタック比較

### 3.1 候補の具体構成（2026-10 時点の最新版）

- **A. Python デスクトップ**: PySide6 6.11.2（LGPL）、pyqtgraph 0.14.0（MIT）、3D は pyvista 0.49 + pyvistaqt 0.13.1（VTK 9.7.1）または pyqtgraph.opengl / VisPy 0.17、通信は pyserial 3.5 / python-can 4.6.1
- **B. Python バックエンド + ローカル Web UI**: FastAPI 0.142 + uvicorn 0.54（または websockets 17.1）、フロントは Vite + TypeScript + uPlot 1.6.32 + three.js r186、`uv run` でサーバを起動しブラウザを開く（または pywebview 6.2 で窓化）
- **C. Electron 44 / Tauri 2.12 + serialport 13 / tauri-plugin-serialplugin**
- **D. その他**: NiceGUI 3.17（FastAPI + Vue/Quasar、`ui.scene` は three.js）、Dear PyGui 2.3（ImPlot）、Qt Quick 3D（PySide6-Addons）

### 3.2 比較表

評価は ◎ 優 / ○ 良 / △ 注意 / × 不適。

| 評価軸 | A: PySide6 + pyqtgraph (+PyVista) | B: Python + ローカル Web UI | C: Electron / Tauri | D1: NiceGUI | D2: Dear PyGui |
|---|---|---|---|---|---|
| 実時間グラフ性能 | ◎ 同一プロセス内で numpy 配列を直接 `setData`。数万点 × 十数本を 30〜60 fps で描ける。0.14 で OpenGL 経路をシェーダ化 | ○〜◎ uPlot は 3,600 点 @60fps で CPU 10%、100k 点程度まで実用 ([uPlot](https://github.com/leeoniya/uPlot))。WS 転送のバッチ化（30〜60 Hz、バイナリ）が必須 | ○〜◎ B と同じ描画系。IPC の手間は B と同程度 | △ ECharts のオプション差し替えを Socket.IO で送るため、1 kHz 多ch には向かない | ◎ ImPlot は非常に速い |
| 3D の質と容易さ | ○ PyVista: PBR、MSAA、ピッキング、glTF/STL 読込が数行で書ける。姿勢更新は `user_matrix` で軽い。pyqtgraph.opengl は軽いが陰影の質が低い ([issue #3212](https://github.com/pyqtgraph/pyqtgraph/issues/3212)) | ◎ three.js の `GLTFLoader` / `STLLoader`、PBR、影、OrbitControls。見た目の質とエコシステムは最良 | ◎ B と同じ | ○ `ui.scene`（three.js）で STL/GLTF 表示可。細かい制御は JS 側に降りる必要あり | × 3D は実質なし（ImPlot3D は点・面程度） |
| CAN/シリアル（Windows） | ◎ pyserial のブロッキング読みを専用スレッドで回す（I/O 中は GIL を解放）。python-can の slcan にも対応 ([python-can slcan](https://python-can.readthedocs.io/en/stable/interfaces/slcan.html)) | ◎ A と同じ Python 層。asyncio を使う場合、pyserial-asyncio は Windows ではポーリング実装で遅い ([docs](https://pyserial-asyncio.readthedocs.io/)) → I/O はスレッドにするのが無難 | ○ serialport 13（Node ≥ 20）/ Rust の serialport-rs。プロトコル実装とシミュレータを JS/Rust で書き直す必要があり、ベンダ Python SDK を流用できない | ○ Python 層は A/B と同じ | ○ Python 層は同じ |
| タイミング・GIL | ○ I/O スレッドと GUI スレッドが GIL を共有。既定の switch interval 5 ms がジッタ要因なので `sys.setswitchinterval(0.001)` などで緩和。必要なら制御ループだけ別プロセスへ | ○ A と同じ条件。UI がブラウザ（別プロセス）なので GIL 競合は A より小さい | ◎ UI と I/O が別プロセス／別言語 | △ UI 更新も Python 側の処理 | ○ |
| 拡張性・プラグイン | ◎ Python の entry points またはフォルダ走査で `QWidget` パネル、シーケンス、トランスポートを登録。スクリプトも Python でそのまま書ける | ◎ バックエンドは Python プラグイン、フロントは ES module の動的 import。ただしプラグインが 2 言語にまたがる | ○ JS/TS で統一できるが、制御スクリプトまで JS になる | ○ Python だけで完結するが、UI 部品は NiceGUI の抽象の範囲に限られる | △ 即時モード UI でドッキング等は自作 |
| 起動の簡単さ | ◎ `uv run edulite05`（または `run.bat`）。初回だけ wheel を約 250〜350 MB 取得 | ○ `uv run` 一発でサーバとブラウザ起動は可能。ただしフロントのビルド成果物を用意する必要がある（後述の Node 20 問題） | × Electron 44 は npm engines が Node ≥ 22.12、Tauri は Rust + MSVC + WebView2 が必要（未導入） | ◎ `uv run` 一発 | ◎ |
| 配布 | ○ PyInstaller / Nuitka / `pyside6-deploy`。サイズは大きめ | ○ Python 側は同様。フロントは静的ファイル | ◎ インストーラが充実 | ○ | ○ |
| テスト容易性 | ◎ コアを Qt 非依存にすれば pytest。UI は pytest-qt と `QT_QPA_PLATFORM=offscreen` | ◎ バックエンドは pytest、フロントは Vitest/Playwright。ただし 2 系統 | ○ | ○ | △ |
| 依存の重さ | △ PySide6-Essentials 77 MB（+Addons 168 MB は不要なら入れない）、VTK 80 MB | ○ Python 側は軽い。node_modules は開発時のみ | × Electron 本体 100 MB 超 | ○ | ◎ |
| ライセンス | ◎ PySide6 は LGPL-3.0、pyqtgraph/pyvista は MIT、VTK は BSD（PyQt6 は GPL-3.0 か商用） ([pythonguis](https://www.pythonguis.com/faq/licensing-differences-between-pyqt6-and-pyside6/)) | ◎ MIT/BSD 中心 | ◎ MIT | ◎ MIT | ◎ MIT |
| 安全性（E-stop・終了時の停止） | ◎ 単一プロセス。窓を閉じれば確実に停止処理が走る | △ タブを閉じてもサーバは生き残る。複数タブからの同時操作、ブラウザのフォーカス喪失時の JOG 押しっぱなし等に対策（WS ハートビートとデッドマン）が必要 | ○ | △ B と同じ | ◎ |
| AI エージェントでの実装・保守 | ◎ 単一言語で、PySide6/pyqtgraph/PyVista の学習データも豊富。ベンダ実装という手本もある | ○ Web は得意分野だが、2 言語・2 ビルド系・WS プロトコルの同期を保守し続ける負担がある | △ Rust まで入ると重い | ○ API の変化が速い（3.x） | △ |

### 3.3 3D ライブラリ（A 採用時）の比較

| 選択肢 | 質 | 姿勢更新 | 依存 | 備考 |
|---|---|---|---|---|
| **PyVista + pyvistaqt (VTK 9.7)** | ◎ PBR、SSAO、MSAA、ピッキング | `actor.user_matrix = T` で 1 ms 未満 | VTK 80 MB | ベンダが採用済み。qtpy 経由で PySide6 に対応（`QT_API=pyside6`）。glTF は `pv.read` / `vtkGLTFImporter` で読める |
| pyqtgraph.opengl `GLMeshItem` | △ 陰影が単純で光源の制御も弱い | `setTransform` で軽い | 追加依存なし | 軽量なフォールバックに向く |
| VisPy 0.17 | ○ | 軽い | 小 | Qt 埋め込み可。メッシュ・PBR 周りは PyVista より手間がかかる |
| Qt Quick 3D（PySide6-Addons） | ◎ PBR、glTF `RuntimeLoader` ([Qt docs](https://doc.qt.io/qt-6/quick3d-asset-intro.html)) | 軽い | Addons 168 MB | QML 混在で複雑になる。RuntimeLoader は効率が劣ると公式にも記載あり |

---

## 4. 推奨

### 4.1 結論

**A 案: Python 3.12 + PySide6 6.11 + pyqtgraph 0.14 + PyVista/pyvistaqt（3D）+ pyserial（ベンダ AT フレーム）/ python-can（SLCAN 等）、`uv` で起動**

加えて、次の設計を守ることで「起動は簡単、機能は制限しない」を両立させる。

1. **コアを Qt 非依存にする**（`protocol` / `transport` / `motor service` / `recorder`）。トランスポートは `SerialAT`（ベンダ USB-CAN）、`PythonCan`（slcan、PCAN など python-can の全 I/F）、`SimMotor`（物理モデル付きシミュレータ）を同じインタフェースで差し替えられるようにする。
2. **I/O はスレッド**で処理する（asyncio は使わない）。受信スレッドはブロッキング読みで GIL を解放し、受信時刻を `perf_counter_ns` で打刻して、事前確保した numpy リングバッファ（seqlock 相当の整合性を持たせる）に書く。制御送信は専用スレッドが deadline 方式（`time.sleep` の高分解能待ち + 最後だけスピン）で行う。Windows の `time.sleep` は Python 3.11 以降、高分解能 waitable timer を使う ([Python docs](https://docs.python.org/3/library/time.html#time.sleep))。
3. **GUI は 30〜60 Hz の `QTimer` で描画だけを担当**する。pyqtgraph は `clipToView`、`setDownsampling(auto=True, mode='peak')`、`skipFiniteCheck=True` を使い、antialias は既定で OFF にする。
4. **3D は `actor.user_matrix` の差し替えのみ**で姿勢を更新する（頂点の再計算はしない）。3D を閉じても制御機能は完全に動くよう、3D は任意（オプション）機能にしておく。
5. **プラグイン**: `importlib.metadata.entry_points(group="edulite05.plugins")` とプロジェクト内 `plugins/` フォルダの走査を併用する。登録できるのはパネル（Dock）、シーケンス（Python スクリプト）、トランスポート、グラフのチャネル定義。プラグインには Qt 非依存の `MotorService` API（スレッドセーフ、速度・トルク上限つき）だけを渡す。
6. **将来 Web UI が必要になったら**、FastAPI サーバをプラグインとして足す（B 案を後から追加できる）。コアを Qt 非依存にしておく理由の一つがこれ。

### 4.2 根拠

- **性能**: 1 モータ × 8〜12 チャネル × 1 kHz × 10 秒窓 = 最大 12 万点。pyqtgraph の典型的な守備範囲に収まる。同一プロセスなのでシリアライズが不要。
- **3D**: 対象は単関節（ハウジング + 出力軸 + 任意のリンク）で、PyVista で十分に高品質。three.js のほうが見た目は上だが、そのために 2 言語構成を採るほどの差はない。
- **通信**: ベンダの Python SDK（MIT）と SLCAN ドライバ、プロトコル表をほぼそのまま参照できる。C 案では JS/Rust に移植し直す必要がある。
- **起動**: `uv run` で依存解決から起動まで完結し、Node や Rust は不要。B 案は Node 20.11 の制約（§6 R3）にすぐ当たる。
- **安全**: 単一プロセスで窓を閉じれば停止処理が走る。ブラウザのタブのライフサイクルや複数クライアントを考えなくてよい。
- **ライセンス**: PyQt6（GPL）ではなく PySide6（LGPL）を選べば、配布形態の自由度が高い。
- **AI による保守**: 単一言語・単一プロセス・型ヒント + pytest で、エージェントが変更の影響を閉じた範囲で検証できる。

### 4.3 起動方法（想定。コードは未作成）

```text
run.bat                     →  uv run --python 3.12 edulite05 %*
uv run edulite05 --sim      →  実機なしのシミュレータで起動
uv run edulite05 --port COM5 --transport at
uv run edulite05 --transport python-can --interface slcan --channel COM5@2000000
```

`pyproject.toml` の `[project.scripts]` にエントリポイントを定義し、`uv.lock` をコミットして依存を固定する。3D と STEP 変換ツールは `optional-dependencies`（例 `[3d]`、`[cad]`）に分け、既定で 3D は含める。

### 4.4 次点: B 案を選ぶべき条件

次のどれかが要件に加わったら B 案（FastAPI + Vite/TS + uPlot + three.js）を再検討する。

- タブレットや別 PC からのリモート操作・監視
- 見栄え重視の 3D（影、環境マップ、アニメーション付きの多関節ロボット）
- Web 開発者がフロントを分担する

その場合も、コア（Python）は A 案と共通にしておく。

---

## 5. STEP → メッシュ変換

### 5.1 入力ファイルの実測

- `docs/vendor/el05/el05.stp`: **2,430,955 byte（約 2.4 MB）**。Creo Parametric 出力、AP203（`CONFIG_CONTROL_DESIGN`）、単位は mm。
- `MANIFOLD_SOLID_BREP` 11 個、`ADVANCED_FACE` 780 面、`PRODUCT` 15 個。**色情報（`COLOUR_RGB` / `STYLED_ITEM`）はない**ため、色はビューア側で付ける。
- 部品名は Creo の `\X2\...\X0\`（UTF-16 16 進）エンコードになっている。デコード結果: 轴承（ベアリング）×2、机壳（ハウジング）、卡簧（サークリップ）、垫片（ワッシャ）×3、轴销（ピン）×3、**输出轴（出力軸）**、齿圈（リングギア）、后盖板（リアカバー）、LUOSI（ねじ）×4、PRT0001。

### 5.2 試行結果（cascadio、スクラッチ環境で実施）

`uv run --no-project --with cascadio --with trimesh` で一時環境を作って試した（プロジェクトには何もインストールしていない）。

- `cascadio.step_to_glb(tol_linear=0.01, tol_angular=0.5)` の変換時間は **1.4 秒**、出力 GLB は **1.76 MB**、18 ノード、**約 15 万三角形**。
- 出力単位は **m**（全体は約 46 × 46 × 47 mm）。ノード名は Creo 部品名がそのまま入る（`_X2_8F9351FA8F74_X0_` = 输出轴、など）。
- **回転軸は Z 軸に平行で、(x, y) ≈ (−12.6, 3.6) mm を通る**（出力軸・ベアリング・リングギアの重心から判断）。出力側は z ≈ 36.7〜52 mm。原点が回転軸上にないので、変換時に平行移動で原点を合わせる必要がある。
- 三角形の約 69%（25,874 × 4）が**ねじ 4 本**に使われている。除外するかデシメートすれば、全体を 5 万三角形以下にできる。
- 回転部品としては、输出轴 + 轴销 ×3（遊星キャリア相当）+ 垫片 ×3 をまとめて "rotor"、それ以外を "stator" とするのが妥当（最終判断は目視で確認する）。

### 5.3 変換手段の比較

| 手段 | 導入 | 再現性 | 名前・構造の保持 | 評価 |
|---|---|---|---|---|
| **cascadio 0.1.1**（OCCT の `RWGltf_CafWriter` ラッパ、MIT、wheel 17 MB） | `uv run --with cascadio` のみ | ◎ スクリプト 1 行、1.4 秒 | ◎ XCAF のノード名と配置を保持 | **推奨**。ただしオプションが少なく、版は 0.x ([GitHub](https://github.com/trimesh/cascadio)) |
| cadquery-ocp 8.0.1 / 7.9.x を直接使う（`STEPCAFControl_Reader` → `BRepMesh_IncrementalMesh` → `RWGltf_CafWriter` / `StlAPI_Writer`、Apache-2.0、48 MB） | pip のみ | ◎ | ◎（細かい制御も可） | cascadio が合わないときの代替。cadquery 本体（2.8）は scipy・casadi・trame 等まで引き込み重いので、使うなら OCP 単体で |
| FreeCAD 1.x CLI（`freecadcmd` + `Part`/`Mesh.export`） | 本体インストール（約 1 GB、pip 不可） | ○ | ○ | 部品の目視確認・手作業の修正には便利。自動化のために常用するには重い |
| gmsh 4.15（`gmsh.model.occ.importShapes` → 2D メッシュ） | pip（42 MB） | ○ | △ | FEM 向けのメッシャ。GPL で、表示用には過剰 |
| オンライン変換 | 不要 | × | △ | 再現できない。ベンダ CAD を外部に上げることになり不可 |

### 5.4 推奨パイプライン

`tools/step_to_glb.py`（後で作成）を `uv run --with cascadio --with trimesh` で実行し、生成物 `assets/el05/el05.glb`（と必要なら `rotor.stl` / `stator.stl`）をコミットする。アプリ本体は STEP 変換ライブラリに依存させない。

1. `cascadio.step_to_glb`（`tol_linear` 0.01〜0.05、`tol_angular` 0.3〜0.5）で GLB に変換する
2. trimesh でノード名をデコードし（`\X2\` → UTF-16）、rotor / stator に振り分ける
3. ねじの除外またはデシメート、回転軸を原点・+Z に合わせる平行移動
4. 振り分けた 2 グループを 1 つの GLB（ノード名 `stator`、`rotor`）に書き出す
5. 変換パラメータと入力ファイルの SHA-256 を `assets/el05/README` に記録し、再生成できるようにする

PyVista は GLB を直接読めるので STL は必須ではない。ただし STL は pyqtgraph.opengl / three.js のどちらでも扱えるため、フォールバック用に両方出しておくのが安全。

---

## 6. リスク一覧

| # | リスク | 影響 | 対策 |
|---|---|---|---|
| R1 | CH340 USB シリアルの遅延・ジッタ（Windows には FTDI のような latency timer の設定項目がない） | 1 kHz の閉ループが不安定になる | 往復遅延とジッタを初期段階で実測する。能動報告（type 24）を活用し、指令レートを 100〜500 Hz に下げられる設計にする |
| R2 | GIL 競合（描画や numpy の重い処理中に I/O スレッドが待たされる。既定 switch interval 5 ms） | 送信周期のジッタ | `sys.setswitchinterval(0.001)`、描画処理の軽量化、受信時の打刻。必要なら制御ループを `multiprocessing` + `shared_memory` の別プロセスへ。将来は free-threaded CPython 3.14+ も選択肢（PySide6 の対応状況は要確認） |
| R3 | **Node v20.11.1 は Vite 7/8 の要件（^20.19 または ≥ 22.12）を満たさない。Electron 44 は ≥ 22.12 を要求**。Node 20 自体が 2026-04-30 で EOL ([eolradar](https://eolradar.com/node-js-20-end-of-life-2026/)) | B/C 案で即座にビルドできない | A 案を採れば問題にならない。B 案に進む場合は Node 22/24 LTS へ更新するか、ビルド済みの `dist/` をコミットする |
| R4 | pyqtgraph の最新版が 0.14.0（2024-11）のままでリリース間隔が長い。PySide6 6.9.1/6.9.2 との非互換報告がある ([#3357](https://github.com/pyqtgraph/pyqtgraph/issues/3357)、[blog](https://mattoppenheim.com/blog/2025/07/pyside6_pyqtgraph/)) | Qt 更新時の描画不具合 | `uv.lock` で PySide6 と pyqtgraph の組み合わせを固定し、スモークテスト（offscreen でグラフ描画）を CI 相当で回す。**PySide6 6.11.2 × pyqtgraph 0.14.0 の組み合わせは初期に実機で要検証** |
| R5 | VTK と pyqtgraph（OpenGL 使用時）の OpenGL コンテキストの同居、リモートデスクトップや古い GPU での VTK 初期化失敗 | 3D が表示されない | 3D は任意機能とし、失敗したら pyqtgraph.opengl に切り替えるかプレースホルダを表示（ベンダ実装も同様の方針）。グラフは既定では非 OpenGL で描く |
| R6 | ベンダ USB-CAN の AT プロトコル仕様が PDF（パスワード保護で本調査では読めなかった）とサンプルコードに依存している | 実装の誤り | ベンダ Python/C++ 実装（`reference_code/`）とロジックアナライザや実機ログで検証する。python-can 互換の `BusABC` 実装にしておけば、SLCAN や他のアダプタに差し替えられる |
| R7 | 誤指令による機械的事故（JOG 押しっぱなし、プラグインやスクリプトの暴走） | 人・機材の危険 | `MotorService` に上限（速度・トルク・位置範囲）を持たせ、GUI のフォーカス喪失時に停止、E-stop はキュー優先度を最上位に、アプリ終了・例外発生時は必ず停止フレームを送る。モータ側の CAN タイムアウト系パラメータが使えるか要確認 |
| R8 | 初回の依存取得サイズ（PySide6 約 77 MB + VTK 約 80 MB + numpy 等） | 初回起動に時間がかかる | uv のキャッシュで 2 回目以降は即時。Addons は入れない。`[3d]` を extras に分けておき、最小構成でも起動できるようにする |
| R9 | 共有ネットワークドライブ（`\\192.168.100.103\...`）上での `.venv` 作成と実行 | venv 作成が遅い、ロック競合、パス長の問題 | `UV_PROJECT_ENVIRONMENT` でローカルディスクに venv を置く選択肢を `run.bat` に用意する（要検証） |
| R10 | cascadio は 0.x で API が変わりうる | 変換スクリプトが壊れる | 生成物をコミットして本体から切り離す。版を固定する。OCP を直接使う代替手順を文書化しておく |
| R11 | STEP に色がなく、部品名もエンコードされている | 3D の見た目や部品の振り分けミス | 振り分け表（ノード名 → グループ・色）を YAML で持ち、目視確認した結果を記録する |

---

## 7. 参考 URL

- PySide6 リリースノート: https://doc.qt.io/qtforpython-6/release_notes/pyside6_release_notes.html
- Qt for Python 6.11: https://www.qt.io/blog/qt-for-python-release-6.11-is-out
- PyQt6 と PySide6 のライセンス比較: https://www.pythonguis.com/faq/licensing-differences-between-pyqt6-and-pyside6/
- pyqtgraph リリース / CHANGELOG: https://github.com/pyqtgraph/pyqtgraph/releases , https://github.com/pyqtgraph/pyqtgraph/blob/master/CHANGELOG
- pyqtgraph PlotDataItem（`clipToView`・`downsample`・`skipFiniteCheck`）: https://pyqtgraph.readthedocs.io/en/latest/_modules/pyqtgraph/graphicsItems/PlotDataItem.html
- pyqtgraph と PySide6 の非互換報告: https://github.com/pyqtgraph/pyqtgraph/issues/3357 , https://mattoppenheim.com/blog/2025/07/pyside6_pyqtgraph/
- GLMeshItem の陰影に関する issue: https://github.com/pyqtgraph/pyqtgraph/issues/3212
- Qt Quick 3D の glTF 読込: https://doc.qt.io/qt-6/quick3d-asset-intro.html
- python-can SLCAN: https://python-can.readthedocs.io/en/stable/interfaces/slcan.html
- pyserial-asyncio（Windows はポーリング実装）: https://pyserial-asyncio.readthedocs.io/ , https://github.com/pyserial/pyserial-asyncio/issues/3
- Python `time.sleep` の高分解能化（3.11）: https://docs.python.org/3/library/time.html , https://github.com/python/cpython/issues/89592
- RobStride USB-CAN モジュール（CH340 / 921600 bps / AT コマンド）: https://openelab.io/products/robstride-can-to-usb-debugging-module
- RobStride Python サンプル: https://github.com/RobStride/Python_Sample
- uPlot: https://github.com/leeoniya/uPlot
- NiceGUI ドキュメント: https://nicegui.io/documentation/scene , https://nicegui.io/documentation/echart
- Dear PyGui: https://github.com/hoffstadt/DearPyGui
- tauri-plugin-serialplugin: https://github.com/s00d/tauri-plugin-serialplugin
- Node.js 20 EOL: https://eolradar.com/node-js-20-end-of-life-2026/
- cascadio: https://github.com/trimesh/cascadio , https://pypi.org/project/cascadio/
- CadQuery の入出力（glTF / STL の tolerance）: https://cadquery.readthedocs.io/en/latest/importexport.html
- OCCT RWGltf_CafWriter: https://dev.opencascade.org/doc/occt-7.6.0/refman/html/class_r_w_gltf___caf_writer.html
- FreeCAD の CLI による STEP→STL 例: https://github.com/faerietree/freecad_convert
- USB の遅延と FTDI の latency timer: https://ftdichip.com/wp-content/uploads/2020/08/AN232B-04_DataLatencyFlow.pdf

### 付録: 確認したパッケージのバージョン（2026-10-01）

| パッケージ | 版 | ライセンス | 備考 |
|---|---|---|---|
| PySide6 / -Essentials / -Addons | 6.11.2 | LGPL-3.0 / GPL | Essentials 76.9 MB、Addons 168 MB（win_amd64） |
| PyQt6 | 6.11.0 | GPL-3.0 / 商用 | |
| pyqtgraph | 0.14.0 | MIT | numpy ≥ 1.25 |
| pyvista / pyvistaqt / vtk | 0.49.0 / 0.13.1 / 9.7.1 | MIT / MIT / BSD | vtk 80.4 MB |
| vispy | 0.17.0 | BSD | |
| python-can / pyserial | 4.6.1 / 3.5 | LGPL-3.0 / BSD | |
| FastAPI / uvicorn / websockets | 0.142.2 / 0.54.0 / 17.1 | MIT / BSD / BSD | |
| NiceGUI / Dear PyGui / pywebview | 3.17.1 / 2.3.1 / 6.2.1 | MIT / MIT / BSD | |
| cascadio / cadquery-ocp / cadquery / gmsh / trimesh | 0.1.1 / 8.0.1.0.0 / 2.8.0 / 4.15.2 / 5.1.0 | MIT / Apache-2.0 / Apache-2.0 / GPL-2.0+ / MIT | cadquery 2.8 は ocp < 8.0 を要求 |
| three / uplot / vite / typescript | 0.186.1 / 1.6.32 / 8.3.1 / 7.0.2 | MIT | vite 7/8 は Node ^20.19 または ≥ 22.12、vite 6 は Node 20.11 でも可 |
| electron / @tauri-apps/cli / serialport | 44.5.1 / 2.12.0 / 13.0.0 | MIT | electron は Node ≥ 22.12 |

---

## 検証結果: 共有フォルダ上の venv(2026-10-01)

Phase 0 タスク 0-1 / 0-3 の実測。環境: Windows 11、プロジェクトと `.venv` は `\192.168.100.103\Test\dev\EDULITE05_Controller`(SMB 共有)上、uv 0.11.23、CPython 3.12.2(`C:\Python312`)、`UV_LINK_MODE=copy`、uv キャッシュは C: 上。

### ロックされた主な版(`uv.lock`、48 パッケージ)
PySide6 / PySide6-Essentials / PySide6-Addons / shiboken6 = 6.11.2、pyqtgraph 0.14.0、numpy 2.5.3、pyserial 3.5、pyvista 0.49.0、pyvistaqt 0.13.1、vtk 9.7.1、matplotlib 3.11.2(pyvista の依存)、dev: pytest 9.1.1、pytest-qt 4.5.0、ruff 0.16.9。PySide6 6.11.2 + pyqtgraph 0.14.0 の組合せで import・Qt ウィンドウ生成は問題なし(描画性能は 0-2 スパイクで別途確認)。

### 所要時間

| 項目 | 共有フォルダ上の venv | (参考)ローカルディスク上の venv |
|---|---|---|
| `uv lock` | 約 1 秒 | - |
| `uv sync` 初回 | パッケージ取得 37 秒 + 展開。1 回目は `os error 59`(予期しないネットワークエラー)で中断し、再実行で完了。合計 約 8 分(4 分 27 秒 + 3 分 52 秒) | 11 秒(キャッシュ温存時) |
| `uv sync` 2 回目(変更なし) | 約 1.2 秒(`--frozen` なら 0.4 秒) | - |
| `import PySide6, pyqtgraph, pyvista` | **約 40〜59 秒**(4 回: 59 / 43 / 44 / 39 秒、`uv run` 起動分を含む) | 6 秒(初回)/ **2 秒** |
| `pytest`(16 件、smoke のみ) | 約 15 秒 | - |
| `run_sim.bat`(`EDULITE05_AUTOCLOSE=1`、プレースホルダ窓 2 秒表示) | 約 9 秒で終了コード 0 | - |

- import の内訳(共有フォルダ・冷間、各ステップ単独計測): PySide6.QtWidgets 2.7 秒、numpy 7.1 秒、pyqtgraph 12.9 秒、pyvista 32.4 秒、pyvistaqt 37.9 秒。`python -X importtime -c "import pyvista"` では pyvista.core の型エイリアス等の import に時間がかかる。
- 原因は SMB 越しに数万の小ファイルを読むこと。**共有フォルダ上の venv は実行時の起動が遅い**(ローカルの約 20 倍)。対策: (1) 3D ビューは **遅延 import**(設計どおり。起動時に pyvista を import しない)、(2) 体感が悪ければ `UV_PROJECT_ENVIRONMENT` で venv をローカルへ移す(README 参照。sync は 11 秒)。

### 発生した問題と回避策
1. **初回 `uv sync` が SMB 書込み時に失敗**(`failed to rename file ... os error 59`)。再実行で続行できたが、中断で **numpy が不完全なインストール(`numpy.libs` が欠落)** になり `DLL load failed while importing _multiarray_umath` になった。`uv sync --reinstall-package numpy` で復旧。共有フォルダ上の初回 sync 後は import を検証すること。
2. **コンソールスクリプト(.exe トランポリン)経由だと PySide6 が import できない**: `uv run edulite05` / `uv run pytest` は venv の .exe が python を拡張長パス形式(UNC 版 `\?\UNC\...`)で起動するため、`DLL load failed while importing Shiboken: パラメーターが間違っています` になる(`pushd` によるドライブ割当経由でも同じ。.exe 側でパスが正規化される)。**`uv run python -m edulite05` / `uv run python -m pytest` は正常**。よって `run.bat` / `run_sim.bat` / `run.sh` は `uv run --frozen python -m edulite05 ...` にした(`__main__.py` 経由)。ローカルディスク上の venv では `uv run edulite05` も問題ない見込み。
3. `cmd /c` を UNC カレントから起動すると `UNC paths are not supported` の警告が出るが、`run*.bat` 先頭の `pushd "%~dp0"` が一時ドライブ(Z: 等)に割り当てて実行できる。終了時は `popd`。
4. `set UV_LINK_MODE=copy ` のように値の後ろに空白が入ると uv が拒否するため、bat では `set "UV_LINK_MODE=copy"` とクォートする。
5. ruff はベンダー資料(`docs/vendor/`)を検査しないよう `extend-exclude` 済み。

### 起動確認
`cmd /c run_sim.bat`(UNC パスから、`EDULITE05_AUTOCLOSE=1`)で、仮の PySide6 ウィンドウ「EL05 Controller (placeholder)」が表示され(別途プロセスのウィンドウタイトルで確認)、2 秒後に終了コード 0 で終了。


---

## 検証結果: GUI スタック(PySide6 6.11.2 + pyqtgraph 0.14.0 + pyvistaqt)(2026-10-01)

Phase 0 タスク 0-2(`tools/spikes/stack_check.py`)と 0-5(UI モック)の実測。環境: Windows 11(表示倍率 125%)、共有フォルダ上の venv、numpy 2.5.3、vtk 9.7.1、pyvista 0.49.0、pyvistaqt 0.13.1。

### 性能(8ch × 1 kHz の sin、表示窓 10 s = 1 万点/ch、バッファ 120 s、QTimer 16 ms)
| 条件 | 結果 |
|---|---|
| 8 段積み、グリッドなし | **45 fps**(`setData` 8 本で約 3 ms) |
| 同、グリッド(縦横)あり | 約 15 fps |
| 4 段積み、`processEvents` 込みの 1 フレーム時間(ms) | グリッドなし 8 / 縦線のみ 12.6 / 横線のみ 20 / 縦横 23.5 |
| 4 段積み、pen 幅 2 | 49.8(幅 1 の 23.5 の約 2 倍。幅 2 以上は非コスメティック経路で遅い) |
| UI モック(4 段 × 14 本、1600x1000、縦線グリッド、`PreciseTimer` 20 ms) | **50 fps**(上限)。縦横グリッドで約 36 fps |
| pyvistaqt `QtInteractor` に円柱を回転 + 上記プロットを同時表示 | `render()` 1 回 約 1 ms(平均 1.0 / 最大 1.6 ms)。問題なし |

- **グリッド描画が最大のコスト**(縦線 4.5 ms、横線 12 ms / 4 段)。X 軸を固定範囲にしても、OpenGL(`useOpenGL`)にしても改善しなかった(OpenGL は基底コストが約 16 ms で逆効果)。`clipToView` / 自動ダウンサンプル(peak)は 1 万点では差が出ない(6 万点でも `setData` は数 ms)。
  → モックは**縦線グリッドのみ既定**、縦横は「格子」チェックで切替。pen 幅は 1。Y のオートスケールは自前(ヒステリシス付き)。
- **`QTimer` の既定(CoarseTimer)は Windows の 15.6 ms 分解能に丸められる**。33 ms を指定すると 47 ms(21 fps)になる。描画タイマは `Qt.PreciseTimer` にすること。
- numpy 2.5 + pyqtgraph 0.14: `setYRange` に `np.float32` を渡すと `RuntimeWarning: overflow encountered in cast`(ViewBox の limits との比較)。**Python の `float` に変換して渡す**。
- その他、PySide6 6.11.2 と pyqtgraph 0.14.0 の組合せで import・描画・マウス操作に問題は出なかった。

### PySide6 / Qt の注意点
- `Signal(dict)` に int キーの dict を emit すると `Cannot copy-convert (dict) to C++`(QVariantMap 変換)。**`Signal(object)` を使う**。
- `QObject` と `ABCMeta` のメタクラス衝突があるので Bridge に ABC は使えない(`NotImplementedError` にした)。
- グローバルなシングルトン(ThemeManager 等)の signal に **lambda で接続すると、ウィジェット破棄後に呼ばれて `Internal C++ object already deleted`**。QObject のバウンドメソッドで接続すれば自動切断される。
- Windows の `QT_QPA_PLATFORM=offscreen` は**フォントが無く日本語が豆腐(□)で描画される**。テストは offscreen でよいが、スクリーンショットは実ウィンドウ(windows プラットフォーム)で撮る。表示倍率に左右されず 1600x1000 px にするには `QT_ENABLE_HIGHDPI_SCALING=0` と `QT_FONT_DPI=96`。
- `QShortcut`(`Qt.ApplicationShortcut`)の Esc は pytest-qt の `qtbot.keyClick(win, Key_Escape)` で発火する(offscreen でも可)。

### PyVista / 3D
- `pv.read("el05.glb")` は **glTF のノード名を保持しない**(`Node_0`, `Mesh_1` …)。ルート直下の順序は GLB の JSON チャンクのシーンのルート順と一致するので、JSON チャンクを直接読んで `rotor` / `stator` と部品名を対応付けた(`app/model3d.py`)。trimesh は不要(依存追加なし)。
- `QtInteractor`(VTK のネイティブ描画面)は **`QWidget.grab()` に写らない**。スクリーンショットは `plotter.screenshot(return_img=True)` で取り、`grab()` した画像の該当位置へ合成する(`tools/capture_ui_mock.py`)。実ウィンドウ上では 3D 描画・回転・ゴースト・トルク矢印とも動作した。offscreen での VTK 描画は試していない。
- `import pyvista, pyvistaqt` は共有フォルダ上で 40〜50 秒(別スレッドで先読みすれば GUI は固まらない。スレッド内 import でも問題なし)。先読み完了後の `QtInteractor` 生成 + モデル読込 + シーン構築は数秒。

### 起動時間(共有フォルダ上の venv、`run_sim.bat`、冷間)
- 「読み込み中」ウィンドウ: 約 5 秒(PySide6 の import 後)
- メインウィンドウ表示: 約 32 秒(pyqtgraph 約 13 秒 + 各パネル。別スレッドで import するので読み込み中ウィンドウは固まらない)
- 3D への切替: 表示後さらに約 50 秒(バックグラウンド。それまでは 2D ダイヤル表示)
- ローカルディスク上の venv なら import は約 1/20(本書 0-3 の計測)。体感が悪ければ `UV_PROJECT_ENVIRONMENT` で venv をローカルへ移す。
