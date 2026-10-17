# プラグインの作り方

EDULITE05_Controller の機能は、プラグイン API(`src/edulite05/plugin_api/`)で足せる。組込みのページ(運転・パラメータ・診断・保守・設定)と運転モード(JOG・速度・位置・MIT・電流・試験信号・2台連動)も、同じ API で登録している(`src/edulite05/builtin/`)。設計の根拠は `architecture.md` §4.8 と `ui_design.md` §10。

## 1. 置き場所と読み込み

プラグインは次の 3 か所から、この順に読み込まれる。

| 順 | 置き場所 | 用途 |
|---|---|---|
| 1 | `edulite05.builtin` | 組込み。利用者は無効にできない |
| 2 | プロジェクト直下 `plugins/<名前>/plugin.py` | **置くだけ**で読み込まれる(ディレクトリ名の順) |
| 3 | entry point グループ `edulite05.plugins` | pip で配布するプラグイン(名前順) |

- ディレクトリ名が `_` か `.` で始まるもの、`plugin.py` が無いものは無視する。
- `plugin.py` はパッケージとして読み込まれるので、同じフォルダの他のファイルを `from .triangle import X` のように相対 import できる。
- 起動すると、すべてのプラグインを読み込んで `activate(ctx)` を呼ぶ。**ページ・運転モードなど UI の登録は `activate` の中で行う**(画面を組み立てる前に済ませる必要があるため、起動後の追加はできない)。
- **読み込みに失敗したプラグインは、そのプラグインだけ無効になる**。import の例外・`activate` の例外・`Plugin` が見つからない・id の重複・ページの生成失敗のいずれでも、登録を取り消してログへ出し、アプリは続行する。理由は設定ページの「プラグイン」に出る。
- 設定ページの「プラグイン」で有効/無効を切り替えられる(`user_data/ui_state.ini` に保存)。**切り替えは次回の起動から反映される**(画面にもそう書いてある)。無効の記録は id で持つ。`Plugin.id`(設定ページのスイッチで切り替えた場合)で無効にしたプラグインは、`plugin.py` を import するが `activate` を呼ばない。読み込みに失敗したプラグインのように `dropin:<ディレクトリ名>` / `entry_point:<名前>` の id で無効にした場合は、import もしない。
- 失敗したプラグインの id は読み込めた場合は `Plugin.id`、読み込めなかった場合は `dropin:<ディレクトリ名>`(entry point は `entry_point:<名前>`)で表示する。

## 2. 最小のプラグイン

`plugins/hello/plugin.py`:

```python
from edulite05.plugin_api import Plugin, PluginContext


class Hello(Plugin):
    id = "hello"               # 一意。英数字と _ . - だけ
    name = "こんにちは"          # 設定ページに出る名前
    version = "0.1.0"
    description = "メニューに項目を足す"

    def activate(self, ctx: PluginContext) -> None:
        if ctx.ui is not None:   # GUI のときだけ UI を足す(CLI は ctx.ui が None)
            ctx.ui.add_more_action("こんにちは", lambda: print("hello"))


PLUGIN = Hello()
```

`plugin.py` からの Plugin の見つけ方(上から順に試す): `PLUGIN`(インスタンスかクラス)→ `plugin` → `create_plugin()` → ファイル内の `Plugin` 派生クラスがちょうど 1 つ。

## 3. API リファレンス

すべて `from edulite05.plugin_api import …`。**この API は Qt を import しない**(UI の部品は、ウィジェットを作る呼び出し可能オブジェクト「factory」として渡す。factory を呼んでウィジェットを作るのは GUI 側)。

### 3.1 `Plugin`

| 属性 / メソッド | 内容 |
|---|---|
| `id: str` | 一意の識別子(必須) |
| `name: str` | 表示名(省略時は `id`) |
| `version: str` | バージョン |
| `description: str` | 設定ページの一覧に出る 1 行説明 |
| `requires_gui: bool = True` | `False` なら GUI なしの実行(CLI)でも読み込まれる。`True` のプラグインは GUI なしでは読み込まない(状態は「読み込まず」) |
| `activate(ctx)` | 登録する。例外を出すとこのプラグインは失敗扱いになり、`ctx` を通じた登録はすべて取り消される |
| `deactivate()` | 終了時に呼ばれる(逆順)。独自のリソースだけ片付ける。`ctx` 経由の登録は自動で取り消される |

### 3.2 `PluginContext`(`activate` に渡る)

| 属性 | 内容 |
|---|---|
| `ctx.core` | コア側の登録口(§3.3) |
| `ctx.ui` | `UiRegistry`(§3.4)。GUI なしのときは `None` |
| `ctx.settings` | プラグイン専用の永続設定。`get(key, default)` / `set(key, value)`(JSON にできる値) |
| `ctx.log` | ロガー(`edulite05.plugins.<id>`) |
| `ctx.service` | 接続中の `MotorService`(未接続なら `None`)。**起動時の `activate` ではまだ無い**ので、使う時点で読むか `ctx.on_service` で追従する |
| `ctx.session` | 接続中の `Session`(bus / events / profile)。未接続なら `None` |
| `ctx.on_service(cb)` | 接続(サービス)/ 切断(`None`)で `cb(service)` が呼ばれる。終了時に自動解除 |
| `ctx.project_root` | プロジェクトのルート |

`ctx.service` / `ctx.session` の更新は `CoreBridge` が接続・切断のたびに行う(`plugin_api.runtime`)。コールバックが UI スレッドで呼ばれるとは限らない。

### 3.3 コア側の登録口 `ctx.core`(UI 不要・CLI でも有効)

| メソッド | 内容 |
|---|---|
| `register_controller(cls)` | `Controller` を `CONTROLLERS` へ登録。組込みと同じ名前は不可(`ValueError`) |
| `register_transport(name, factory, describe)` | CAN トランスポートを登録(`core/transport/registry.py` と同じ引数)。既存の名前は上書き不可 |
| `register_channel(suffix, unit, *, kind="plugin", label=None)` | モータごとの追加テレメトリチャネル `m{id}.{suffix}`。接続前に登録すると、モータが見つかったとき標準チャネルと一緒に登録される。値は `ctx.service.telemetry.append(motor_channel(id, suffix), t, value)` で入れる |
| `add_profile_dir(path)` | モータのプロファイル(`<name>.toml`)を探す場所を足す。組込みと同名なら組込みが優先 |

失敗したとき・無効化したとき・終了時に、登録は**逆順に自動で取り消される**。

### 3.4 UI の登録口 `ctx.ui`(`UiRegistry`)

`title` などの文字列は `tr` のキー(`"nav.operate"`)でも、そのままの日本語でも良い(カタログに無い文字列はそのまま表示される)。

| メソッド | 内容 | factory の署名 |
|---|---|---|
| `add_page(id, title, icon, factory, order=50)` | ナビにページを足す。`order` が小さいほど上(組込み: 運転 10・パラメータ 20・診断 30・保守 40・設定 90)。Ctrl+1.. はこの順(9 個まで)。同梱プラグインは スクリプト 50・サンプル(三角波)60 で、組込みの Ctrl+1..4 とスクリプトの Ctrl+5 を動かさない。`icon` は `assets/icons/` の Lucide 名 | `factory(bridge, shell) -> PageBase` |
| `add_mode(controller_cls, group="その他", *, id, title, description, info, short_title, target_fields, limit_fields, label_overrides, min_motors)` | 運転ページのモード選択(「その他 ▾」メニュー)に Controller を足す。`Params` dataclass から目標フォームを自動生成する。先に `ctx.core.register_controller` で登録しておく | — |
| `add_mode_spec(spec)` | `ModeSpec` をそのまま登録(複数のコントローラを持つモード用。組込みが使う) | — |
| `add_monitor_view(id, title, factory)` | 運転ページのモニタ `[グラフ \| 3D \| …]` に表示を足す | `factory(bridge) -> QWidget` |
| `add_maintenance_item(group, title, description, tags, dialog_factory, *, key, needs_motor=True)` | 保守ページに行を足す。`group` は `"common"`(よく使う設定)か `"caution"`(注意が必要な操作) | `dialog_factory(parent, motor) -> ToolDialog` |
| `add_settings_section(title, factory)` | 設定ページにグループを足す(プラグイン一覧の下) | `factory(bridge, shell) -> QWidget` |
| `add_more_action(text, callback)` | ヘッダー右端 `⋯` メニューに項目を足す | `callback()` |

- `ShellApi`(`shell`)でできること: `navigate(page_id)` / `toast(text, kind)` / `settings()` / `open_connection()`。
- `add_mode` の既定の振る舞い: モード ID は `controller_cls.name`、名前は `controller_cls.label`、目標は `Params` の全フィールド、制限・加速度は無し。選択した値は `bridge.start_controller(name, …, params)` へ渡り、`run_mode` は `Controller.run_mode`(インスタンスで決まる場合はインスタンスの値)が使われる。
- ナビの最下部(`bottom`)と、モード選択の本体(`GROUP_MAIN`)は組込み専用。プラグインが指定しても無視される。
- ページ・モニタ表示・保守項目の factory が例外を出したときは、そのプラグインだけ失敗扱いになる。
- 同じ ID(ページ・モード・モニタ表示・保守項目)の二重登録は `ValueError`(= そのプラグインが失敗)。

### 3.5 モード・フォームの部品

```python
@dataclass(frozen=True)
class ControllerForm:   # コントローラ 1 つ分のフォーム
    name; params_cls; target_fields; limit_fields=(); label_overrides={}

@dataclass(frozen=True)
class ModeSpec:         # インスペクタのモード 1 つ
    id; title; group="その他"; controllers=(ControllerForm, …)
    short_title=""; description=""; info=""; min_motors=1
```

`Params` dataclass のフィールドは `core/control/params.py` の `param_field(default, label, unit, *, min, max, step, decimals, limit, choices)` で定義すると、範囲・単位・刻み・出力段階の上限(`limit="speed"` など)付きの入力フォームが自動で作られる(組込みと同じ仕組み)。

## 4. 安全のルール(必読)

1. **モータへの指令はすべて `MotorService`(→ `ControlLoop` → `SafetyGuard`)を通す。** Controller は `SetpointCmd` / `MitCmd` / `HoldCmd` を返すだけにし、ControlLoop が `SafetyGuard` を通して送る。出力段階(低出力 LOW / 高出力 HIGH)ごとのトルク・電流・速度・Kp・Kd の上限は、SafetyGuard がかける。
2. **`ctx.session.bus` などへ生の CAN フレームを直接送って、SafetyGuard を迂回する経路を作ってはいけない。** 有効化・停止・パラメータ書込み・保守操作も `ctx.service` のメソッド(`enable` / `start` / `stop_motion` / `disable` / `write_param` …)を使う。
3. `ctx.core.register_transport` で足すトランスポートは、フレームの送受信だけを行い、指令の内容を書き換えない。
4. 非常停止(`estop`)とその解除の確認は変更しない。プラグインの Controller は `on_stop` で必ず安全な指令(速度 0 / 現在位置保持)を返す。
5. 動作確認は、まず `--sim`(シミュレータ)で行う。実機で動かす試験は、低出力(LOW)の範囲から始める。
6. `activate` / `step` で重い処理・ブロッキング I/O をしない(`step` は制御ループのスレッドから呼ばれる。GUI スレッドでも待たない)。
7. モータ固有の数値(レンジ・既定値)はコードに直書きせず `profiles/*.toml`(または `ctx.core.add_profile_dir` で足したもの)から読む。

## 5. UI の作法

`ui_design.md` v2 に従う。

- 色・寸法・文字サイズは直書きせず、`edulite05.app.ui`(部品)とトークン(`app/ui/tokens.py`)経由。設定・保守に近いページは `app/pages/boxed_list.py`(`BoxedList` / `ListRow` / `GroupHeader` / `centered_scroll`)で組む。
- 彩度の高い色(強調ボタン `accent`)は 1 区画に 1 つまで。通常のボタンは `Button(text, "standard")`。
- できないことは押せなくして、理由をツールチップで言う。
- 部品の見本は `uv run --frozen python -m edulite05 --gallery`。
- UI の文字列はそのままの日本語で良い(プラグインはカタログ `ja.toml` を編集しない)。組込みはカタログ(`tr("key")`)を使う。
- 英語など他言語の訳は、プラグインのフォルダに `i18n/<言語>.toml`(例: `plugins/<name>/i18n/en.toml`)を置く。読み込み時に自動で足される。キーは日本語の原文そのまま(`"三角波" = "Triangle wave"`)か `section.name` 形式。`{name}` などのプレースホルダは原文と同じにする。訳が無い文字列は原文のまま表示される(警告なし)。pip 配布のプラグインは `edulite05.app.i18n.add_strings("en", {...})` を `activate` で呼ぶ。`name` / `description` / `add_mode` / `add_page` の文字列は表示時に `tr()` を通る。例は `plugins/example_triangle/i18n/en.toml`。
- 表示言語は設定ページ(次回の起動から反映)、`--lang ja|en`、環境変数 `EDULITE05_LANG` で選ぶ。

## 6. サンプルの解説 `plugins/example_triangle/`

三角波の試験信号を足すサンプル。ファイルは 3 つ。

- `triangle.py`(コア側。Qt なし): `TriangleParams`(`param_field` で定義した Params)と `TriangleTest(SignalController)`。`SignalController` は組込みの `SineTest` / `StepTest` の基底クラスで、対象(位置・速度・電流・トルク)ごとの `run_mode` の選択・位置の相対化・停止時の保持を実装している。サブクラスは `value_at(p, t)`(時刻から値を作る)と、位置のときの速度上限 `_speed_limit` だけを書く。`step` は先頭で `self.params` を 1 回だけ読む。
- `page.py`(UI): `PageBase` を継承した小さな情報ページ。`boxed_list` の部品で組み、運転ページへ移るボタンは 1 つ(通常ボタン)。
- `plugin.py`: `activate` で次の 3 つを登録する。

```python
def activate(self, ctx):
    ctx.core.register_controller(TriangleTest)          # 1) コア: Controller(CLI でも有効)
    if ctx.ui is None:                                  #    GUI なしならここまで
        return
    ctx.ui.add_mode(TriangleTest, GROUP_OTHER,          # 2) 運転ページ「その他 ▾」に「三角波」
                    title="三角波", description="目標の量に三角波を出します", info="…")
    ctx.ui.add_page("example_triangle", "三角波",       # 3) ナビに小さな情報ページ
                    "activity", _make_page, order=60)
```

動かし方: `uv run --frozen python -m edulite05 --sim` で起動 → 運転ページでモータを選び、「その他 ▾」から「三角波」を選ぶ → 目標(対象・振幅・周波数)を入れて有効化 → 開始。低出力(LOW)では、振幅を大きくしても速度は SafetyGuard によって 5 rad/s 以内に制限される。設定ページの「プラグイン」でこのプラグインを無効にすると、次回の起動から「三角波」とページが消える。

自分のプラグインを作るときは、`plugins/example_triangle/` をコピーして `id` と中身を変えるのが近道。

### 6.1 実用のサンプル `plugins/scripting/`(スクリプトページ)

`add_page` 1 つで「スクリプト」ページ(Python コンソールとシーケンスの実行)を足す、実用のプラグイン。詳しくは `docs/design/sequences.md`。

- 実行の中身(YAML の検証・実行、コンソールのエンジン)は Qt を使わない `core/`(`core/sequence/`・`core/scripting.py`)に置き、プラグインは**画面とその配線だけ**を持つ。CLI とテストが同じ中身を使える。
- 任意のコードを実行する機能なので、組込みではなく `plugins/` に置いて**設定ページで無効にできる**ようにした。
- ページは `ctx.service`(= `plugin_api.runtime.current_service()`)を読んで `MotorService` を使う。接続・切断は実行のたびに読み直す。
- UI 文字列は `tr("script.…")`(`ja.toml` の `[script]`)。同梱のプラグインはカタログに足してよい(第三者のプラグインは §5 のとおりそのままの日本語でよい)。
- 終了時の片付けは `deactivate()`(実行中のスクリプト・シーケンスを中断して待つ)。

## 7. pip で配布する(entry point)

```toml
# 配布するパッケージの pyproject.toml
[project.entry-points."edulite05.plugins"]
my_plugin = "my_package.plugin:MyPlugin"   # Plugin のクラス / インスタンス / 引数なしの関数
```

## 8. テストの書き方

`PluginManager` は Qt なしで使える。`tmp_path` に `plugins/<名前>/plugin.py` を作って読み込み、終わったら必ず `shutdown()` する(登録した Controller などをグローバルに残さないため)。

```python
m = PluginManager(project_root=tmp_path, builtin=[], entry_points=False, gui=False)
m.load_all()
assert m.record("my_plugin").active
m.shutdown()
```

参考: `tests/core/test_plugin_manager.py`(読込・順序・失敗の隔離・有効/無効)、`tests/core/test_example_triangle.py`(サンプル)、`tests/gui/test_plugins_gui.py`(設定ページ・サンプルの UI)。

## 9. 現時点の制限

- 起動後にプラグインを足す・外すことはできない(切り替えは次回の起動から)。
- `add_mode` は `Params` のフィールドから自動フォームを作る。JOG のような専用の操作部品(押している間だけ回る等)は、プラグインのモードでは作れない(専用 UI が要るときは `add_page` で自前のページを作る)。
- CLI(`edulite05.cli`)はまだプラグインを読み込まない(`requires_gui = False` のプラグインを `plugin_api.load_plugins()` で読む入口は用意してある)。
- デモ用 `DemoBridge`(`--demo`)は組込みの制御モードしか動かさない(プラグインのモードを開始すると警告のログが出るだけ)。`--sim`(本物のコア + シミュレータ)では動く。
