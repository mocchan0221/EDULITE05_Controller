# UI デザインガイドライン調査 — EDULITE05 Controller 向け

調査日: 2026-10-01
対象: PySide6 / Qt デスクトップアプリ(Windows 11、日本語 UI)。RobStride EL05 CAN サーボの接続・スキャン・有効化/停止・非常停止・制御モード選択・目標値入力・リアルタイムグラフ/3D・パラメータ編集・故障表示・保守ツール(ID 変更、ゼロ点、ボーレート/プロトコル切替)を扱う。2〜3 台のマルチモータに対応し、出力段階は LOW(安全) と HIGH(固定が必要) の 2 つ。

この文書の目的: 「機能は揃っているが、見た目が悪く、どこを操作すれば何が起きるか分からない」という現行モックを作り直すにあたって、一次資料から **具体的で検証できるルール** を抜き出し、モックに当てはめること。

> 数値の扱い: 出典に明記されている値は「出典値」、出典の考え方からこのアプリ向けに決めた値は「推奨値」と書き分ける。Qt で使うときは **px 指定(`setPixelSize` / QSS の `px`)** を前提にする。Windows のスケーリング 100% では 1 epx = 1 px。

---

## 目次

1. [各ガイドラインの要点](#1-各ガイドラインの要点)
2. [横断的に共通する原則](#2-横断的に共通する原則)
3. [数値ルール集](#3-数値ルール集)
4. [パターン集](#4-パターン集)
5. [現行モックへの適用 — 批評とチェックリスト](#5-現行モックへの適用--批評とチェックリスト)
6. [出典一覧](#6-出典一覧)

---

## 1. 各ガイドラインの要点

### 1.1 GNOME Human Interface Guidelines

| 項目 | 要点(このアプリに関係するもの) | 出典 |
|---|---|---|
| 設計原則 | 「一つのことをうまくやる」。**段階的開示**(progressive disclosure)で、よく使う操作だけを手前に出す。記憶に頼らせない。確認ダイアログより取り消し(Undo)を優先し、不要な割り込みで注意を奪わない。 | https://developer.gnome.org/hig/principles.html |
| ボタン | 色付きボタン(suggested=推奨 / destructive=破壊的)は **1 ビューにつき 1 個だけ**。ラベルは命令形の動詞。ヘッダーバーの外では、アイコンとラベルはどちらか一方にする。隣り合うボタンは幅をそろえ、ウィンドウ内で使うボタン幅は 1〜2 種類まで。押せない状態ではクリック後にエラーを出すのではなく、**最初から無効(insensitive)にする**。 | https://developer.gnome.org/hig/patterns/controls/buttons.html |
| ヘッダーバー | 置くコントロールは少数にとどめる。左に主要操作、中央にタイトルまたはビュー切替、右にメニューと副次操作を置く。主要コントロールにはツールチップを必ず付ける。表示中の内容に合わせて中身を入れ替える。 | https://developer.gnome.org/hig/patterns/containers/header-bars.html |
| ダイアログ | 確認ダイアログのボタンは「実行」と「キャンセル」の 2 つで、**キャンセルを先(左)**に置く。ラベルは具体的な動詞にし、「OK」は使わない。Esc でキャンセル。破壊的・不可逆な操作では Enter を実行に割り当てない。破壊的操作には確認か取り消し手段のどちらかを必ず付ける。 | https://developer.gnome.org/hig/patterns/feedback/dialogs.html |
| バナー | **続いている状態**(オフライン、読み取り専用など)を示す。1 行の事実だけを書き、ボタンは任意で 1 個。一度きりの出来事には使わない。 | https://developer.gnome.org/hig/patterns/feedback/banners.html |
| トースト | ユーザー操作に対する**一時的な**結果を示す(「元に戻す」付きが典型)。ウィンドウ下部の中央に出し、自動で消える。続いている状態には使わない。 | https://developer.gnome.org/hig/patterns/feedback/toasts.html |
| スイッチ | 明確な二値の設定にだけ使い、ラベルは名詞にする。操作した時点で反映される。使えない場合はスイッチを無効表示にする。 | https://developer.gnome.org/hig/patterns/controls/switches.html |
| ボックスリスト | 小さな静的リスト(設定など)に向く。1 行に置くコントロールは最大 2 個。行の中の情報は文字の大きさ・太さ・色で強弱を付ける。 | https://developer.gnome.org/hig/patterns/containers/boxed-lists.html |
| ビュー切替 / サイドバー | ビュー切替は 3〜5 ビューまで。それより多ければサイドバーにする。ラベルは名詞で、長さをそろえる。 | https://developer.gnome.org/hig/patterns/nav/view-switchers.html , https://developer.gnome.org/hig/patterns/nav/sidebars.html |
| ユーティリティペイン | メインビューの機能を変えるペインは左、補助情報は右に置く。トグル表示のショートカットは F9。狭い幅では重ねて表示する。 | https://developer.gnome.org/hig/patterns/containers/utility-panes.html |
| ポップオーバー | 小さく単純な内容に限る。親ウィンドウの 1/3 を超えない。Esc で閉じ、「閉じる」ボタンは置かない。 | https://developer.gnome.org/hig/patterns/containers/popovers.html |
| タイポグラフィ | 少ない種類のスタイル(title-1..4 / heading / body / caption)で強弱を表す。重要度の低い情報は小さく薄くする。斜体と全大文字は避ける。 | https://developer.gnome.org/hig/guidelines/typography.html |
| スタイル / アクセシビリティ | 定義済みのスタイルクラスと色変数を使い、ハードコードしない。**色だけで情報を区別しない**(形・位置・文字を併用する)。点滅は禁止。 | https://developer.gnome.org/hig/guidelines/ui-styling.html |
| アダプティブ | デスクトップでは最小 1024×600 に対応する。最も狭い状態から設計を始める。大きなウィンドウではコンテナに最大幅を設ける。 | https://developer.gnome.org/hig/guidelines/adaptive.html |

### 1.2 Apple Human Interface Guidelines(macOS)

| 項目 | 要点 | 出典 |
|---|---|---|
| レイアウト | 重要なものを**上・先頭側(左)**に置く。関連する要素は余白・コンテナ・区切り線でまとめる。インデントは従属関係を表す。macOS では、ウィンドウの下端は画面外に出やすいので、**重要な情報や操作を下端に置かない**。 | https://developer.apple.com/design/human-interface-guidelines/layout |
| ツールバー | 機能と使用頻度でグループ分けし、**グループは最大 3 つ**を目安にする。最初からオーバーフローするレイアウトは避ける。ツールバーの項目はすべてメニューバーからも実行できるようにする。 | https://developer.apple.com/design/human-interface-guidelines/toolbars |
| サイドバー | 階層は 2 段まで。アイコンはアクセントカラーに従い、固定色は意味のあるものだけに限って控えめに使う。ウィンドウ幅が狭いときは自動で畳む。 | https://developer.apple.com/design/human-interface-guidelines/sidebars |
| スプリットビュー / インスペクタ | 「サイドバー・キャンバス・インスペクタ」の 3 ペイン構成が基本。区切り線は 1pt の細線にし、ペインには妥当な最小幅と最大幅を決める。 | https://developer.apple.com/design/human-interface-guidelines/split-views |
| ボタン | 目立つスタイルのボタンは 1 ビューに 1〜2 個まで。優先度は**大きさではなくスタイルで**区別する。破壊的操作に primary(既定)ロールを割り当てない。押下状態の表示を必ず用意する。 | https://developer.apple.com/design/human-interface-guidelines/buttons |
| アラート | タイトルで状況を具体的に書く(「エラー」だけにしない)。「OK」は避け、具体的な動詞を使う。キャンセルボタンは必ず「キャンセル」と表記する。**既定ボタンを末尾側(右)、キャンセルを先頭側(左)**に置く。ユーザーが意図して選んでいない破壊的操作には destructive スタイルを使う。読ませたいアラートでは既定ボタンを設定しない。 | https://developer.apple.com/design/human-interface-guidelines/alerts |
| セグメンテッドコントロール | 密接に関連する選択肢に使う。数は**広い画面でも 5〜7 個まで**。各セグメントの幅をそろえる。文字とアイコンを混ぜない。**選択状態を表すコントロールと、アクションを実行するコントロールを混ぜない**。 | https://developer.apple.com/design/human-interface-guidelines/segmented-controls |
| スライダー | 範囲が広いときはテキストフィールドとステッパーを併設する。最小値と最大値だけラベルを付ければよい。値はドラッグ中もリアルタイムに反映する。 | https://developer.apple.com/design/human-interface-guidelines/sliders |
| 色 | 1 つの色に複数の意味を持たせない。アクセントカラーは 1 色にする。色だけで情報を伝えない。 | https://developer.apple.com/design/human-interface-guidelines/color |
| アクセシビリティ数値 | macOS のコントロールは既定 28×28pt、最小 20×20pt。文字は既定 13pt、最小 10pt。コントラスト比は 4.5:1(18pt 以上または太字なら 3:1)。 | https://developer.apple.com/design/human-interface-guidelines/accessibility |

### 1.3 Microsoft Fluent 2 / Windows 11(実行環境のため最優先で参照する)

| 項目 | 要点 | 出典 |
|---|---|---|
| 間隔 | **4px を基本単位**とする(2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48…)。Windows の標準値は、ボタン間 8、ボタンとフライアウト間 8、**ヘッダー(上置きラベル)とコントロール間 8**、横置きラベルとコントロール間 12、**コンテンツ領域どうしの間 12**、サーフェス端と文字の間 16。NavigationView のコンテンツ余白は 24(Minimal 表示時は 12)。 | https://fluent2.microsoft.design/layout , https://learn.microsoft.com/en-us/windows/apps/design/basics/content-basics |
| タイプランプ | Caption 12/16、Body 14/20、Body Strong 14/20 Semibold、Body Large 18/24、Subtitle 20/28 Semibold、Title 28/36 Semibold、Title Large 40/52、Display 68/92。**最小は Regular 12px、Semibold 14px**。強調には Bold ではなく Semibold を使い、斜体は使わない。日本語の UI フォントは **Yu Gothic UI**。 | https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography |
| 角丸 | ボタン・入力欄・リストなどの**画面内コントロールは 4px**、ダイアログ・フライアウト・ウィンドウなどの**オーバーレイは 8px**、ツールチップは 4px。直線同士が接する辺(SplitButton の継ぎ目、ドッキングした辺)と、最大化したウィンドウは 0px。 | https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/geometry |
| 色 | アクセントカラーは**控えめに**使い、重要な要素とインタラクティブ要素の状態だけに当てる。「インタラクティブな要素には 1 色を決めて使う」。重要度の低いサーフェスほど暗く、重要なものほど明るくする(レイヤリング)。赤と緑だけで区別しない(男性の約 8% が赤緑色覚異常)。 | https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/color |
| テーマ色(WinUI 実値) | §3.6 の表にまとめた。Text Primary/Secondary/Tertiary、SolidBackground Base/Secondary、SystemFill Success/Caution/Critical とその背景色など。 | https://github.com/microsoft/microsoft-ui-xaml(controls/dev/CommonStyles/Common_themeresources_any.xaml) |
| InfoBar | **アプリ全体の状態変化**(接続断、自動保存の失敗など)を示すインライン通知。重大度は Informational / Success / Warning / Error の 4 段階で、色とアイコンは組み込みのものを使う。状態が解消するまで消せない設定(IsClosable=false)にもできる。ボタンは 1 個まで。コンテンツの上に浮かばず、レイアウト内に場所を取る。**短時間で出たり消えたりさせない**。ユーザー操作への直接の応答には使わない。 | https://learn.microsoft.com/en-us/windows/apps/design/controls/infobar |
| ContentDialog | タイトルで「何をするか」を書き、本文でタイトルを言い換えて繰り返さない。ボタンは具体的な応答にする(「許可」/「ブロック」など)。安全な「閉じる/キャンセル」が必ず 1 つ必要。**Windows では実行(Primary)ボタンが左、Close/Cancel が右**。DefaultButton を指定するとアクセント表示になり Enter に反応する(既定では未指定)。入力欄の検証エラーにダイアログを使わない。 | https://learn.microsoft.com/en-us/windows/apps/design/controls/dialogs-and-flyouts/dialogs |
| CommandBar | 重要度順に並べ、収まらない分はオーバーフロー(…)に送る。アイコンはプライマリ領域で 20px、オーバーフロー内で 16px。ラベルは短く(1 語程度)。どの画面にも出るコマンドは常に同じ位置に置く。Accept/OK を Reject/Cancel の左に置く。 | https://learn.microsoft.com/en-us/windows/apps/design/controls/command-bar |
| NavigationView | 同格の最上位カテゴリが 5 個以下なら Top、5〜10 個なら Left。Auto 表示では、幅 1008px 以上で展開、641〜1007px でアイコンのみ、640px 以下でメニューボタンのみになる。 | https://learn.microsoft.com/en-us/windows/apps/design/controls/navigationview |
| ボタン(Fluent 2) | **Primary ボタンはレイアウト内に 1 つだけ**。同じ優先度の操作が並ぶときはすべて中立色にし、細かい操作が多いときは outline / subtle / transparent を使う。無効なボタンにはツールチップで理由を示す。ボタンの文字は 4.5:1、アイコンは 3:1 のコントラストを確保する。 | https://fluent2.microsoft.design/components/web/react/core/button/usage |
| コントロール高さ | WinUI の標準密度では、ボタン・テキストボックス・コンボボックスの最小高さが **32px**。高密度の Compact sizing では **24px**(マウス操作向け)。Fluent 2 のボタンサイズは Small 24 / Medium 32 / Large 40。 | https://learn.microsoft.com/en-us/windows/apps/design/style/xaml-theme-resources ほか(Compact sizing は WinUI 2.1 以降) |

### 1.4 IBM Carbon(データが密集する業務・産業ツールのモデル)

| 項目 | 要点 | 出典 |
|---|---|---|
| ステータス表示 | **色・形・記号のうち少なくとも 2 つ**を組み合わせ、可能ならテキストも添える。色の対応は、赤=危険/エラー、オレンジ=重大な警告、黄=警告、緑=成功、青=情報、グレー=下書き/中立。ステータス色は背景に対して 3:1 以上。アイコンは 16px または 20px。リストや表の中では左揃えにする。 | https://carbondesignsystem.com/patterns/status-indicator-pattern/ |
| 通知 | Inline(関連要素の近くに出し、解消まで残す。2 行以内)、Toast(システム起因で作業に直結しないもの。3 行以内。右上に積む)、Banner(製品・システム全体の通知。ヘッダーの直下)、Modal(重大で作業を止めるもの)を使い分ける。**ボタンは 1 個まで**。重要なメッセージを時間経過で消さない(WCAG 2.1.4)。コンテンツの上にかぶせない。 | https://carbondesignsystem.com/patterns/notification-pattern/ |
| 間隔トークン | 2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96, 160。近い要素ほど関連があり、間隔が広いほど区切りを示す。 | https://carbondesignsystem.com/elements/spacing/overview/ |
| モーダル | ボタンは最大 3 個。2 ボタンなら副ボタンを左、主ボタンを右に置く。**大きな影響がある確認では主ボタンを Danger ボタンに置き換える**。タイトルは能動形で書く。 | https://carbondesignsystem.com/components/modal/usage/ |
| データテーブル | 行の高さは xs 24 / sm 32 / **md 40(既定)** / lg 48 / xl 64。ヘッダー行もデータ行と同じ高さにする。ゼブラ縞は任意。ツールバーのアクションは 5 個まで。 | https://carbondesignsystem.com/components/data-table/usage/ |

### 1.5 Material 3

| 項目 | 要点 | 出典 |
|---|---|---|
| ステートレイヤー | 状態はコンテナ色の上に前景色を一定の不透明度で重ねて表す。hover 8%、focus 10%、pressed 10%、dragged 16%。無効時は内容を 38%、コンテナを 12% にする。Qt の QSS でも、hover/pressed をこの比率で統一すると「押せるもの」の反応が一貫する。 | https://m3.material.io/foundations/interaction/states/state-layers |
| カラーロール | 色は primary / on-primary / primary-container / secondary / tertiary / error / surface(container-lowest〜highest)/ outline / outline-variant といった**役割(ロール)名で定義**し、16 進値を直接書かない。「on-◯◯」はその色の上に載せる文字色で、コントラストは保証済みとする。 | https://m3.material.io/styles/color/roles |

### 1.6 GitHub Primer / Atlassian(ステータスラベル・ロゼンジ)

| 項目 | 要点 | 出典 |
|---|---|---|
| Label / StateLabel | ステータスラベルは**操作できない**メタデータ。文字は短くする。緑=成功/完了、黄・オレンジ=注意、赤=危険。 | https://primer.style/product/components/label/ |
| Lozenge | **操作できない**。最大幅 200px で、切り詰めが必要な長いラベルは避ける。色だけに頼らない。目立つ(bold)表示を多用すると、重大なステータスの効果が薄れる。操作が必要なら「ロゼンジ型のドロップダウン」という別部品にする。 | https://atlassian.design/components/lozenge/usage |

### 1.7 Refactoring UI(Wathan & Schoger)

出典: https://www.refactoringui.com/ (章立てと要旨)

- **Hierarchy is everything**: すべての要素が同じ重要度ではない。強弱は大きさだけでなく、**太さと色(濃淡)**で付ける。「強調したいもの以外を弱めて強調する」(de-emphasize to emphasize)。**ラベルは最後の手段** — 値と単位だけで意味が通じるなら「速度:」のようなラベルは不要。
- **Semantics are secondary**: 破壊的操作であっても、その画面の主目的でなければ大きな赤ボタンにする必要はない。第 2・第 3 の操作として控えめに置き、確認の段階で初めて赤くする。
- **余白は最初は多めに取り、後から詰める**。間隔は決まったスケールから選ぶ(4, 8, 12, 16, 24, 32, 48, 64…)。**グループ内の間隔 < グループ間の間隔**にして、どこで区切れるか曖昧な間隔を作らない。
- **色の濃淡は最初に決めておく**: グレー 8〜10 段階、主色 5〜10 段階、意味色(赤・黄・緑)もそれぞれ 5〜10 段階。グレーをわずかに青寄りにすると冷たく精密な印象になる。
- **枠線を減らす**: 区切りは背景色の差、影、余白で作る。枠の中に枠を重ねない。
- **色の付いた背景の上にグレーの文字を置かない**(同系色の濃淡を使う)。

### 1.8 Laws of UX / Nielsen の 10 ヒューリスティクス

出典: https://lawsofux.com/ , https://www.nngroup.com/articles/ten-usability-heuristics/

| 法則・原則 | このアプリでの意味 |
|---|---|
| Hick の法則 | 選択肢が増えるほど決定は遅くなる。ツールバーの 15 個以上の操作、7 個のモードタブ、14 個の凡例チェックボックスは減らすか、グループに分ける。 |
| Fitts の法則 | 頻繁な操作や緊急の操作は**大きく近く**に置く。非常停止は大きくし、画面端の決まった位置に置く。開始/停止は値を入力するスライダーのすぐ下に置く。 |
| Jakob の法則 | ユーザーは他の Windows アプリ(設定アプリ、Visual Studio、MotorStudio 等)と同じ動き方を期待する。Windows 標準の部品と配置に従う。 |
| 近接・共通領域・類似 | 関係するものは近くに置き、同じ背景の面にまとめる。モーターごとの色や「目標値と実測値」の対応も、色や形の類似で示す。 |
| Von Restorff 効果 | 他と違うものだけが目立つ。だから**目立たせる色(赤・アクセント)は 1 画面にごく少数**に限る。 |
| Doherty の閾値 | 400ms 以内にフィードバックを返す。ボタンを押したら即座に押下状態と「送信中」を示し、モーターの応答は後から反映する。 |
| Tesler の法則 | 消せない複雑さ(モード、ゲイン、上限)はある。それをユーザーに押し付けず、既定値・プリセット・段階的開示でアプリ側が引き受ける。 |
| N1 システム状態の可視化 | 接続状態、有効/無効、実行中のモード、出力段階、故障は常に 1 か所に、同じ見た目で表示する。 |
| N3 ユーザーの制御と自由 | 非常停止と停止はどの画面からでも 1 操作でできるようにする。パラメータ編集は書込前に取り消せる。 |
| N4 一貫性と標準 | 同じ語は同じ意味にし、違う意味には違う語を使う(「モード」「停止」の多義性は §5 を参照)。 |
| N5 エラーの防止 | 実行できない操作は無効化し、理由をツールチップに出す。上限を超える入力はスライダーの範囲そのもので防ぐ。 |
| N6 記憶より認識 | 現在の上限値・単位・対象モーターを、操作する場所のすぐそばに表示する。 |
| N8 美的で最小限のデザイン | 普段使わない情報(式、CAN ID、ホスト ID)は折りたたむか詳細画面に移す。 |
| N9 エラーの認識・診断・回復 | 故障は「何が起きたか/影響/次にすべきこと」の 3 点を文章で示し、回復ボタンを添える。 |

### 1.9 産業用 HMI の規格(補足。モーターを動かす装置のため)

- **ISA-101 High-Performance HMI**: 背景は中立のグレーにする。**正常時は低コントラストの中立色で表示し、彩度の高い色は異常時とオペレーターの操作が必要なときだけ**に使う。画面全体が静かなので、異常だけに目が向く(「目が滑る」問題への直接の処方)。出典(解説): https://processcontrolguide.com/isa-101-hmi-design/
- **IEC 60204-1 押しボタンの色**: 赤=非常(非常停止)、黄=異常時の介入、緑=正常状態の開始、青=必須の操作(リセット)、白/グレー/黒=特定の意味なし(START は白が望ましい。赤は START に使わない)。非常停止は赤で、背景がある場合は黄にする。出典(解説): https://www.se.com/uk/en/faqs/FA28043/
  - ソフトウェア上の「非常停止」は安全規格上の非常停止装置ではない。表示は「非常停止(ソフトウェア)」とし、ハードウェアの E-Stop を併用するよう案内文で求めるのが誠実(推奨)。

---

## 2. 横断的に共通する原則

どの体系でも同じことを言っている原則を、このアプリの言葉に直したもの。

1. **視覚的階層(Hierarchy)** — 画面ごとに「1 番目に見るもの」を決める。運転画面なら ①状態(有効/無効・故障・出力段階)、②実測値、③目標値入力と開始/停止、④グラフ、⑤ログ の順になる。強弱は大きさよりも**太さ・濃淡・面の明るさ**で付ける(Apple、Refactoring UI、Fluent)。
2. **段階的開示(Progressive disclosure)** — 一度設定したら触らないもの(COM ポート、ボーレート、ホスト ID)、たまに使うもの(保守ツール、CAN フレーム)、危険なもの(ID 変更、ゼロ点、プロトコル切替)は手前に出さない(GNOME、Apple)。
3. **1 つの文脈に主要操作は 1 つ** — アクセント色や破壊的スタイルのボタンは 1 ビューに 1 個(GNOME、Fluent 2、Apple は「1〜2 個」)。複数の主要操作が必要に見えるなら、それは文脈の分け方を間違えている。
4. **アフォーダンスとシグニファイア** — 押せるもの(ボタン、タブ、セグメント)と、押せないもの(状態ピル、ラベル、読み取り専用の値)を**見た目で区別する**。ステータスラベルは「操作できない」(Primer、Atlassian)。逆に、押せるものは hover・pressed・focus の 3 状態を必ず持つ(Apple、M3)。
5. **状態表示と操作の分離** — 「いまの状態」(ピル、バッジ、InfoBar)と「状態を変える操作」(ボタン)を別の部品にする。トグルボタンを使うなら、ラベルが状態を表すのか、押した後の動作を表すのかを曖昧にしない。
6. **色の意味を固定する** — 1 色に 1 つの意味(Apple)。赤=危険/故障/非常停止、黄・オレンジ=注意(HIGH 出力など)、緑=正常に動作中/成功、青(アクセント)=操作できるもの・選択中。**正常時はグレー**(ISA-101)。色だけに頼らず、アイコン・形・文字を必ず併用する(GNOME、Carbon、Fluent、WCAG)。
7. **一貫性** — 同じ部品、同じ位置、同じ用語を使う。Windows の標準に合わせる(Jakob の法則、N4)。
8. **フィードバック** — 400ms 以内に反応する。操作の結果は、一時的なものならトースト、続いている状態なら InfoBar/バナー、作業を止める必要があるならダイアログで返す(GNOME、Fluent、Carbon で一致)。
9. **破壊的・不可逆操作のエラー防止** — まず実行できない状態を無効化で防ぐ。次に、取り消せる操作には Undo を付ける。取り消せない操作には、具体的な動詞のボタンを持つ確認ダイアログを出し、既定ボタンを破壊側にしない。ダイアログを出しすぎるとユーザーが読まずに押すようになる(NN/g)ので、本当に危険な操作だけに使う。

---

## 3. 数値ルール集

### 3.1 間隔スケール(4px 基準)

**トークン**(Fluent と Carbon の共通部分。推奨値)

| トークン | px | 用途 |
|---|---|---|
| `space-0.5` | 2 | アイコンと文字の微調整、バッジ内の縦余白 |
| `space-1` | 4 | ラベルと補足(単位、min/max)の間、密な行内 |
| `space-2` | 8 | **ボタンとボタンの間**、上置きラベルとコントロールの間、行内のコントロール間 |
| `space-3` | 12 | 横置きラベルとコントロールの間、**カード・ペインどうしの間**、フォーム行の間 |
| `space-4` | 16 | **パネル内側の余白(padding)**、グループ見出しの上 |
| `space-6` | 24 | **グループとグループの間**、ダイアログの内側余白 |
| `space-8` | 32 | 大きなセクションの区切り、空状態の上下余白 |
| `space-12` | 48 | 空状態のイラスト周り |

**ルール**

- グループ内の間隔は 8 以下、グループ間は 16〜24 にし、**最低でも 2 段階の差**を付ける(Refactoring UI の「曖昧な間隔を避ける」)。
- パネルの内側余白は 16、パネル同士の間は 12 か 1px の区切り線にする(Fluent)。
- 5, 7, 10 のような**スケール外の値は使わない**。

### 3.2 タイプランプ(日本語向け)

**フォント指定**(推奨)

- UI 本文: `QFont.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Yu Gothic UI", "Meiryo UI"])` — 欧文は Segoe、和文は Yu Gothic UI で表示される。Meiryo UI は Regular/Bold しかないため最後の代替にする。
- 数値表示: `"Cascadia Mono", "Consolas"` を使い、和文が混ざる箇所は Yu Gothic UI に落とす。Segoe UI Variable を使う場合は**等幅数字(tabular figures)**を必ず有効にする。値が変わるたびに桁の位置が揺れないようにするため。
- 日本語の UD(読みやすさ重視)が必要なら、Windows 10 以降に同梱の **BIZ UDPゴシック**も候補になる。
- Yu Gothic UI のウェイトは Regular(400)と Semibold(600)だけを使う。Light は小さいサイズでかすれ、Bold は Fluent の方針(強調は Semibold)に反する。

| スタイル | サイズ/行高 (px) | ウェイト | 用途 |
|---|---|---|---|
| Caption | 12 / 16 | Regular | 単位、スライダーの min/max、補足、タイムスタンプ。**12px 未満は禁止**(Fluent の最小値) |
| Body | 14 / 20 | Regular | 既定の本文、ボタン、入力欄、表のセル |
| Body Strong | 14 / 20 | Semibold | グループ見出し、表のヘッダー、狭い場所のタイトル |
| Body Large | 18 / 24 | Regular | 空状態の説明、ダイアログ本文(強調したい場合) |
| Subtitle | 20 / 28 | Semibold | ペインのタイトル(「ID 1 · EL05 #1」)、ダイアログのタイトル |
| Title | 28 / 36 | Semibold | 画面タイトル(使う場面は少ない) |
| Numeric L | 28 / 36 | Semibold、等幅数字 | 主要な実測値(位置・速度・トルク) |
| Numeric M | 20 / 28 | Regular、等幅数字 | 副次の実測値(温度・電圧) |

- 1 画面で使うサイズは **4 段階まで**(例: 12 / 14 / 20 / 28)。
- 複数行の日本語は行高を 1.43〜1.6 倍にする(14px なら 20〜22px)。
- 1 行あたりの文字数は、説明文なら全角 35〜40 字程度に抑える(Fluent は欧文で 50〜60 字)。
- 文字を薄くする段階は Primary / Secondary / Tertiary の 3 つまで(§3.6 の色)。

### 3.3 コントロールの高さと最小ヒット領域

| 要素 | 高さ (px) | 備考 |
|---|---|---|
| 標準のボタン、入力欄、コンボボックス、スピンボックス | **32** | WinUI の標準密度 |
| 密な表やツールバーの小ボタン | 24 | WinUI Compact。マウス操作専用 |
| ペインの主要操作(開始/停止) | **40** | Fluent 2 の Large。幅はペイン幅の 1/2 程度 |
| 非常停止 | **≥ 48**(幅 ≥ 140) | 画面上で最大のボタンにする。常に同じ位置に置き、キーボードショートカットを割り当てる |
| アイコンのみのボタン | 32×32 | アイコンは 16px(ツールバーでは 20px) |
| スライダー | 行の高さ 32、つまみ 18〜20、トラック 4 | トラックの角丸は 2px(バー形状は 4px 以下) |
| 表の行 | 32(密)/ 40(既定) | ヘッダー行も同じ高さにする(Carbon) |
| タブ / セグメント | 32 | 各セグメントの幅はそろえる |
| チェックボックス / スイッチ | ヒット領域 ≥ 24 | ラベルをクリックしても切り替わるようにする |

- **最小ヒット領域は 24×24px**(WCAG 2.2 SC 2.5.8 AA)。推奨は 32×32、タッチ操作もあるなら 40〜44(SC 2.5.5 AAA は 44)。
- 隣り合うボタンには 8px 以上の間隔を空ける。

### 3.4 角丸

| 対象 | 半径 |
|---|---|
| ボタン、入力欄、コンボボックス、チェックボックス、リストの選択背景 | **4px** |
| カード、ペイン内のグループ面 | 4px(面の外周が他の面と接しない場合は 8px でもよい) |
| ダイアログ、フライアウト、ポップオーバー、トースト | **8px** |
| ステータスピル | 高さの半分(全丸) |
| ドッキングしたペインどうしの接する辺、区切り線で分けた辺 | **0px** |

1 画面で使う半径は 0 / 4 / 8 / 全丸 の 4 種類までにする。

### 3.5 枠線と影(エレベーション)の使い方

- **区切りは面の明暗で作る**: 背景(base)→ ペイン(layer)→ カード(card)と、重要なほど明るくする(Fluent のレイヤリング。ダーク・ライトとも)。
- 枠線は 1px で、低いアルファ値にする(ダークは白 8〜10%、ライトは黒 6〜8%)。**枠の中に枠を置くのは 1 段まで**(ペイン > グループ面 で止める)。
- 影はオーバーレイ(ダイアログ、フライアウト、トースト、ドラッグ中の要素)にだけ付ける。ドッキングしたペインには影を付けない。
- フォーカスリングは 2px で、隣接色に対して 3:1 以上のコントラストにする(WCAG 1.4.11)。キーボード操作時にだけ表示する。

### 3.6 コントラストと色

**コントラスト比(WCAG 2.2)**

- 本文テキスト: **4.5:1 以上**
- 大きい文字(24px 以上、または 18.66px 以上の太字): 3:1 以上
- UI 部品の境界、状態表示、アイコン、グラフの線: **3:1 以上**(SC 1.4.11)
- 無効状態の部品は対象外。ただし「無効だと分かる」ことは必要

**ニュートラルパレット**(WinUI の実値をもとに、Qt で不透明色として使えるよう合成した推奨値。括弧内は元の定義)

| ロール | ダーク | ライト | 用途 |
|---|---|---|---|
| `bg/base` | `#202020`(SolidBackgroundFillColorBase) | `#F3F3F3` | ウィンドウの地 |
| `bg/sunken` | `#1C1C1C`(…Secondary) | `#EEEEEE` | グラフ・3D・ログの描画面、ステータスバー |
| `bg/layer` | `#272727` | `#F9F9F9` | ドッキングしたペインの面 |
| `bg/card` | `#2D2D2D` | `#FFFFFF` | ペイン内のグループ面、カード |
| `bg/control` | `#373737`(hover `#3D3D3D`、pressed `#323232`) | `#FFFFFF`(hover `#F6F6F6`、pressed `#F0F0F0`) | ボタンや入力欄の地 |
| `stroke/control` | `#3F3F3F` | `#E0E0E0`(下辺は `#C8C8C8`) | コントロールの枠 |
| `stroke/divider` | `#333333`(DividerStroke 白 8%) | `#E5E5E5` | 区切り線 |
| `text/primary` | `#FFFFFF` | `#1A1A1A`(黒 89%) | 本文、値 |
| `text/secondary` | `#CCCCCC`(白 77%) | `#5C5C5C`(黒 62%) | ラベル、単位 |
| `text/tertiary` | `#969696`(白 53%) | `#868686`(黒 45%) | 補足の文字。**12px の重要情報には使わない**(ライトの `#868686` は `#F3F3F3` に対して約 3.3:1) |
| `text/disabled` | `#717171` | `#A0A0A0` | 無効状態 |

**アクセント色と意味色**(WinUI の SystemFill* 実値。推奨値)

| ロール | ダーク(前景/強調) | ダーク(背景) | ライト(前景/強調) | ライト(背景) | 意味 — **この意味以外には使わない** |
|---|---|---|---|---|---|
| `accent` | `#60CDFF`(上の文字は `#000000`) | `#1F3A4D` | `#005FB8`(上の文字は `#FFFFFF`) | `#E5F1FB` | 操作できるもの・選択中・主要ボタン・フォーカス |
| `success` | `#6CCB5F` | `#393D1B` | `#0F7B0F` | `#DFF6DD` | 正常に動作中(有効・実行中)、成功トースト |
| `caution` | `#FCE100` | `#433519` | `#9D5D00` | `#FFF4CE` | 注意: HIGH 出力、未保存の変更、上限で値が切られた |
| `critical` | `#FF99A4` | `#442726` | `#C42B1C` | `#FDE7E9` | 故障、通信エラー、破壊的操作の確認 |
| `estop` | 塗り `#C42B1C`、文字 `#FFFFFF`、枠 `#FCE100` 2px(IEC にならい黄の縁) | — | 同じ | — | **非常停止専用** |
| `neutral-status` | `#CCCCCC` の文字と `#3A3A3A` のピル | — | `#5C5C5C` の文字と `#E6E6E6` のピル | — | 停止中、無効、未接続などの平常状態(ISA-101) |

- 赤の塗り(`#C42B1C`)の上の白文字は約 5.7:1 で AA を満たす。
- **HIGH 出力はオレンジ・黄系(caution)、非常停止は赤**と分ける。赤を HIGH に使うと、非常停止と見分けられなくなる。
- アクセントは Windows のシステムアクセントカラー(`QStyleHints`/レジストリから取得)に追従させてもよい。その場合も、上記の意味色とは混ぜない。

**グラフ系列の色**(推奨値。意味色と重ならない色を選ぶ)

| 系列 | ダーク | ライト |
|---|---|---|
| モーター 1 | `#60CDFF`(シアンブルー) | `#005FB8` |
| モーター 2 | `#D7A6FF`(ラベンダー) | `#8764B8` |
| モーター 3 | `#F7A35C`(アンバー) ※HIGH 表示と隣り合わない場合のみ。避けたい場合は `#4FD1C5` | `#B35A00` / `#00807A` |
| 目標値(指令値) | 同じ色の破線(線幅 1.5) | 同じ |
| 実測値 | 同じ色の実線(線幅 2) | 同じ |

物理量はサブプロットで分かれているため、**色は「どのモーターか」の識別に使う**。目標値と実測値は線種(破線/実線)で区別する。

### 3.7 状態表示に使う不透明度(M3 のステートレイヤーを Qt 向けに簡略化)

hover は前景色 8%、pressed は 10〜12%、選択は accent の 15〜20% を背景に重ねる。無効は内容を 38%、コンテナを 12% にする。QSS の `:hover` / `:pressed` / `:checked` / `:disabled` をこの比率で一括定義する。

---

## 4. パターン集

### 4.1 アプリシェル: ヘッダーバー / ツールバー / ナビゲーションビューのどれを使うか

| 方式 | 特徴 | このアプリでの判断 |
|---|---|---|
| GNOME ヘッダーバー | タイトルバーと統合した少数のコントロール | Windows 11 では Jakob の法則に反するため採用しない |
| macOS ツールバー | グループ 3 つまで、すべての項目をメニューにも置く | **「グループは 3 つまで」の考え方は採用する** |
| Fluent CommandBar + NavigationView | Windows ネイティブ。画面の切替は Nav、操作は CommandBar | **基本はこれ** |

**推奨構成**

```
┌───────────────────────────────────────────────────────────────────────┐
│ [接続チップ: ● COM7 · 1Mbps ▾]  [対象: (M1)(M2)(M3)]   [状態サマリ]   [LOW|HIGH] [■ 非常停止] │ ← コマンドバー(3グループ+非常停止)
├──────────┬─────────────────────────────────────────────┬──────────────┤
│ 運転      │  グラフ / 3D(キャンバス)                    │ インスペクタ   │
│ パラメータ │                                             │ (制御)        │
│ 診断      │                                             │ 状態ヘッダ     │
│ 保守      │                                             │ モード選択     │
│ ─────    │                                             │ 目標値         │
│ M1 ● 有効 │                                             │ [開始][停止]   │
│ M2 ○ 停止 ├─────────────────────────────────────────────┤              │
│          │  ログ(折りたたみ可能なドロワー)              │              │
└──────────┴─────────────────────────────────────────────┴──────────────┘
```

- **左**: NavigationView 風のサイドバー(画面切替: 運転 / パラメータ / 診断(故障・CAN フレーム) / 保守)。その下にモーター一覧を置く。最上位の画面は 4 個なので Top 型も可能だが、モーター一覧を並べるため Left 型を推奨する。1008px 未満ではアイコンのみに畳む。
- **中央**: キャンバス(グラフ / 3D)。最も広い面を割り当てる。
- **右**: インスペクタ(選択中のモーターの制御)。幅は 320〜380px で固定し、リサイズ可能にする場合も最小 300px とする。
- **下**: ログはドロワーにして既定で畳む。新しい ERR が出たら件数バッジを付ける。
- ドッキングウィジェットの「フロート/閉じる」ボタンは既定で非表示にする(レイアウトの自由度より、迷わないことを優先する)。
- macOS の「下端に重要な操作を置かない」に従い、ステータスバーに置くのは参考情報(fps、ジッタ)だけにする。

### 4.2 サイドバーナビゲーション

- 項目は名詞にする(「運転」「パラメータ」「診断」「保守」)。16px のアイコンとラベルを併記し、行の高さは 36〜40px。
- 選択中の項目は、左端の 3px のアクセントバーと淡い背景(accent 15%)で示す。
- **保守(危険な操作)は区切り線の下の最後に置く**。
- モーター行は「名前 + 状態ピル + 温度」の 3 要素までにする(GNOME ボックスリストの「1 行にコントロール 2 つまで」)。行をクリックすると操作対象になる。

### 4.3 インスペクタパネル(制御)

上から次の順に並べる(重要度順。Apple のレイアウト原則)。

1. **対象ヘッダー**: `EL05 #1`(Subtitle)+ `ID 1`(Caption、secondary)+ 状態ピル(`有効 · 速度モード実行中` など)
2. **出力段階と上限**: 1 行で「LOW · トルク ≤ 0.5 N·m · 速度 ≤ 5 rad/s …」と書き、Caption・secondary 色にする。詳細はツールチップかポップオーバーに出す。**色は付けない**(HIGH のときだけ caution 色の InfoBar を出す)。
3. **モード選択**(§4.4)
4. **目標値**(§4.10): モードで必要なものだけを表示する
5. **主要操作**: `開始`(accent、高さ 40)と `停止`(標準、高さ 40)を横に並べる。実行中は `開始` を無効にし、`停止` を強調する(状態に合わせて主要操作が入れ替わる)
6. 詳細設定(送信周期、連続送信)は「詳細 ▸」の Expander に入れて既定で閉じる

### 4.4 セグメンテッドコントロール(モード選択、単位切替)

- **選択状態を表すもの**(制御モード、rad/deg、時間幅)と**アクションを実行するもの**(クリア、CSV 保存)は同じ部品にしない(Apple)。
- 数は 5 個まで(Apple の上限は 5〜7、GNOME のビュー切替は 3〜5)。モードは 7 個あるので、例えば `位置 | 速度 | 電流 | MIT | JOG` を主に表示し、`位置` の中で CSP/PP をサブ選択、`試験信号` は保守/診断側に移す。あるいは各モードの 1 行説明付きのドロップダウンにする。
- 各セグメントは同じ幅・高さ 32 にする。選択中は accent の塗り、未選択は透明背景 + secondary 色の文字。
- **実行中にモードを変えられないなら、選べないセグメントを無効化し、ツールチップで理由を示す**(「実行中は切り替えられません。先に停止してください」)。現行モックのように、選べてしまってから警告を出す方式はやめる。

### 4.5 ステータスバッジ / ピル

- ピルは**操作できない**ので、hover 時の変化もポインタの変化も付けない。ボタンより背が低く(高さ 20〜24)、全丸で、枠はなくてよい。
- 中身は `アイコン(12〜16px) + 短い語`。色だけに頼らない(Carbon の「2 要素以上」)。

| 状態 | ピルの見た目(推奨) |
|---|---|
| 未接続 / 停止 / 無効 | neutral(グレーの地、secondary 色の文字)、○ アイコン |
| 有効(トルク出力中)/ 実行中 | success の背景 + success の文字、● または ▶ アイコン |
| HIGH 出力 | caution の背景 + caution の文字、⚠ アイコン |
| 故障 | critical の背景 + critical の文字、⨯ アイコン、件数 |
| シミュレータ | neutral + 「SIM」(誤認されないよう、ヘッダーに常時表示する) |

- **「故障なし」は表示しない**か、neutral の小さな文字にとどめる(正常時は静かに。ISA-101)。

### 4.6 バナー / InfoBar(故障、HIGH 出力、接続断)

- 置き場所はコマンドバーの直下(全体の状態の場合)か、インスペクタの最上部(モーター単位の場合)。コンテンツの上に重ねず、レイアウトを押し下げる。
- 構成は「アイコン + タイトル(太字)+ 1 行の説明 + ボタン 1 個まで」。
  - 例(Error): `⨯ M2: 過電流で停止しました` / `負荷を確認してから故障をクリアしてください。` / `[故障をクリア]`
  - 例(Warning): `⚠ HIGH 出力中` / `上限: トルク 6 N·m・速度 50 rad/s` / `[LOW に戻す]`
  - 例(Info): `シミュレータに接続中 — 実機には送信されません`
- 原因が解消するまで閉じられないようにする(IsClosable=false)。HIGH 出力中や故障中のバナーは、ユーザーが閉じられてはいけない。
- 状態の変化が速くても、表示は 1 秒以上維持してちらつかせない(Fluent)。

### 4.7 トースト

- ユーザー操作の結果を一時的に伝える(「パラメータを 3 件書き込みました」「CSV を保存しました」)。
- ウィンドウ下部の中央(GNOME)に、キャンバスに重ねて出す。2〜3 行以内、ボタンは「元に戻す」「開く」など 1 個まで。表示時間は 4〜6 秒で、マウスが乗っている間は消さない。
- **故障やエラーをトーストだけで伝えない**(時間で消えるため)。必ずバナーや故障一覧にも残す。

### 4.8 破壊的操作の確認ダイアログ

**文面のルール**

- タイトルは「何が起きるか」を書いた疑問文か命令文にする。例: `ID 1 の CAN ID を 5 に変更しますか?`
- 本文には影響と元に戻せるかを 1〜3 行で書く。例: `変更後は ID 5 として再スキャンが必要です。この操作は電源を切っても保持されます。`
- ボタンは**具体的な動詞**にする: `ID を変更` / `キャンセル`。「OK」「はい」は使わない。

**ボタンの並び順**

| プラットフォーム | 並び順(左 → 右) | 既定ボタン(Enter) |
|---|---|---|
| **Windows(本アプリ)** | `[実行する操作]` `[キャンセル]` — 実行ボタンが左、安全側が右 | 破壊的操作では**既定ボタンを設定しない**か、キャンセルを既定にする |
| GNOME | `[キャンセル]` `[実行する操作]` | 不可逆・破壊的操作は Enter に割り当てない |
| macOS | `[キャンセル]` `[実行する操作]` — 既定は右端 | 破壊的操作を primary にしない |

- Qt では `QDialogButtonBox` に役割(`AcceptRole` / `DestructiveRole` / `RejectRole`)を指定すれば、OS ごとの並び順が自動で適用される(Windows は WinLayout)。**手作業で並べない。**
- 破壊的ボタンは critical 色の「塗り」か「枠」にし、ダイアログ内で唯一の色付きボタンにする。
- Esc は常にキャンセル。
- 本当に危険な操作(ゼロ点、ID 変更、プロトコル切替、HIGH 出力)は、確認を 2 段階にする: ①前提条件のチェックボックス(「モータを固定しました」)をオンにしないと実行ボタンが有効にならない。②さらに強い防止が必要なら長押し(§4.11)か、対象 ID の入力を求める。
- 頻繁に行う日常操作(停止、無効化)には確認を出さない(NN/g の「狼少年」問題)。

### 4.9 空状態

| 状況 | 表示(中央に Body Large + 主要ボタン 1 個) |
|---|---|
| アダプタ未接続 | 「CAN アダプタが接続されていません」/ `[接続…]`(accent)/ 補助リンク「シミュレータで試す」 |
| 接続済みでモーターなし | 「モータが見つかりませんでした」/ 確認項目 3 点(電源、終端抵抗、ボーレート)/ `[再スキャン]` |
| グラフにデータがない | 「モータを選んで[開始]すると、ここに波形が表示されます」 |
| 故障なし | 「現在、故障はありません」+ 最終確認時刻 |

空状態では、グレーのアイコン(48px)と説明に加え、**次に押すべきボタンを 1 つだけ**示す。

### 4.10 数値入力とスライダー

```
速度                                   [  1.250 ] rad/s  ↺
├──────────────●─────────┊───────────────┤
-5                       ▲実測 0.92                     5
```

- 1 行目: ラベル(Body、secondary)を左に、値の入力欄(右揃え、等幅数字、幅は最大桁数に合わせる)と単位を右に置く。リセットは「0」という文字ではなく**アイコンボタン(↺)とツールチップ「0 に戻す」**にする。現行の「0」ボタンは値の表示欄と見分けがつかない。
- 2 行目: スライダー。トラック上に**実測値をゴースト表示**(小さな ▲ または細い縦線)すると、目標値と実測値の差がひと目で分かる。
- 3 行目: min/max を Caption・tertiary 色で表示する。**範囲の端の数字に色を付けない**(現行の緑・青の数字は意味色の乱用)。
- 範囲は出力段階の上限そのものにする(HIGH の上限は HIGH のときだけ表示する)。上限で値が切られたら、caution 色の文字で「上限 5 rad/s に制限しました」とインラインで出す。
- キーボード: ↑↓ で 1 ステップ、Shift+↑↓ で 10 ステップ、PageUp/Down で 10%。値は Enter で確定し、Esc で元に戻す。
- 「連続送信」はスイッチにする(即時反映の二値設定。GNOME、NN/g)。

### 4.11 長押しで実行するボタン

- **JOG**(押している間だけ回転)は、本質的にデッドマン操作なので長押し型が正しい。
  - 押下中はボタンの地を accent にし、「回転中…」と表示する。指を離す、フォーカスが外れる、ウィンドウが非アクティブになる、通信が途絶する — **いずれの場合も即座に停止**する。
  - 2 つの方向ボタンは同じ見た目(標準ボタン + 方向アイコン)にし、どちらか一方だけを accent にしない。
- **長押しで確定**(ゼロ点設定など、取り消せない保守操作に使える代替手段)
  - 1.0〜1.5 秒でボタン内に進捗が満ちていき、満ちたら実行する。途中で離せば何も起きない。ラベルは `長押しでゼロ点を設定`。
  - ツールチップと、キーボード操作者向けの代替(Space の長押し、または確認ダイアログ)を用意する。
- 非常停止は**長押しにしない**(1 クリック・1 キーで即座に効く)。

### 4.12 非常停止

- 常に同じ位置(コマンドバーの右端)に置き、どの画面・どのモーダルからでも押せるようにする(モーダル表示中もグローバルショートカットを有効にする)。
- 見た目は `estop` 色の塗り、白文字、黄色の縁取り、■ アイコン、高さ 48 以上。画面内で**唯一の赤い塗り**にする。
- ラベル: `非常停止` + Caption で `Esc / Pause`。
- 押した後は「非常停止中 — 全モータを無効化しました」という Error バナーを出し、`[復帰…]` ボタン(IEC 60204-1 にならい青系/アクセント)で明示的に復帰させる。

---

## 5. 現行モックへの適用 — 批評とチェックリスト

対象: `docs/design/ui_mock/14_fit_1536x912.png`, `02_main_dark_3d.png`, `05_control_velocity.png`, `06_control_jog.png`, `09_bottom_params.png`, `11_bottom_tools.png`, `12_dialog_high_tier.png`, `13_main_dark_high_tier.png`, `03_main_light_graph.png`

### 5.1 批評(要素ごと)

**A. 上部ツールバー(14, 02, 13)**

1. **コントロールが 15 個以上あり、グループが 5〜6 個ある**(切断 / スキャン / 操作対象 / モード:運転 / 制御:速度 / 故障なし / 有効化 / 停止 / LOW|HIGH / 非常停止)。Apple は「グループは 3 つまで」としている。Hick の法則により、毎回すべてを読ませることになる。
2. **状態とボタンの見た目が同じ**。`モード: 運転`(緑の塗り)、`制御: 速度`(青の塗り)、`故障なし`(グレーの塗り)は状態表示なのに、隣の `有効化`・`停止` ボタンと同じ形をしている。どれが押せるのか分からない。これが「何を操作すれば何が起きるか分からない」の最大の原因。
3. **用語が多義的**。「モード」がモーターの状態(運転/停止)にも制御モード(MIT/速度…)にも使われている。「停止」もツールバーの停止、インスペクタの停止、非常停止の 3 か所にあり、どこまでが止まるのか(選択中のモーター/全モーター/実行中のモードだけ)が書かれていない。
4. **LOW|HIGH のセグメントが画面上で最大・最も鮮やかな部品**(LOW は緑の塗りで 40px 以上の文字)。ふだんは触らない設定が、主要操作よりも目立っている。しかも LOW に「正常/成功」の緑が使われている。
5. `切断` がツールバーと接続パネルの 2 か所にある。接続パネルでは、**接続済みの状態なのに `切断` が全幅の青い主要ボタン**になっており、主要操作の位置を奪っている。

**B. 色の使い方(全体)**

6. **緑が 6 つ以上の意味で使われている**: LOW、モード:運転、接続済みの丸、故障ビットの「正常」の丸 × 6、インスペクタの上限テキスト、ステータスバーの「出力段階 LOW」、`実行中` ピル、グラフの速度線。Von Restorff 効果が働かず、どこにも目が止まらない(「目が滑る」)。
7. **青も同じように乱用されている**: オートスケール(トグル)、`制御: 速度` ピル、`切断` ボタン、`開始` ボタン、rad の選択、表の選択行、`シミュレータ` ピル、スライダーのトラック、位置の線。アクセントが「押せるもの」の印として機能していない。
8. **グラフの色が物理量を表していて、モーターの区別がない**。位置=青、速度=緑、トルク=橙、温度=赤だが、すでにサブプロットで物理量は分かれている。モーター 1 とモーター 2 は同じ色なので見分けられない。さらに赤・緑・橙は意味色と衝突する。
9. **故障ビットが 6 個の緑の丸**で表示されている。色だけで状態を区別しており(GNOME、Carbon、WCAG 違反)、正常時に最もうるさい部品になっている(ISA-101 の逆)。

**C. レイアウトと階層**

10. **ペインが 5 つとタブ群が 3 つあり**(グラフ/3D、ログ/CAN/パラメータ/故障/ツール、モードタブ 7 個)、さらに各ペインにフロート/閉じるボタンがある。枠が「ドック枠 > グループボックス > カード」と 3 重に入れ子になり、境界線だらけになっている(Refactoring UI の「枠線を減らす」に反する)。
11. **一度設定すれば済む接続パネル(アダプタ、COM、ビットレート、ホスト ID)が左上の一等地を占めている**。段階的開示に反する。
12. 「操作対象: ID 1 EL05 #1」がツールバー、状態ペイン、インスペクタの 3 か所に重複している。反対に、インスペクタで操作している対象をモーター一覧で切り替えられることは見た目から読み取れない。
13. 間隔がどこも同じくらい狭く(4〜6px)、グループの区切りが余白で表現されていない。例えばインスペクタの「Kd」と「フィードフォワードトルク」の間と、「フィードフォワード」と「連続送信」の間が同じ間隔になっている。

**D. インスペクタ(05, 06, 14)**

14. **モードを選ぶ部品がタブ**になっている。タブは「画面の切り替え」を示すのに、実際は「状態の選択」であり、しかも実行中は切り替えられない。タブは押せてしまい、その後で `別のモード(正弦波)が実行中です…` という警告が出る。押せない状態は最初から無効にすべき(GNOME、N5)。
15. 「このモードは停止中にのみ切り替えられます。」の**枠付きの箱が入力欄に見える**(アフォーダンスの誤り)。
16. 数値行の右端にある「0」ボタンが、値の入力欄と区別できない。min/max の数字に緑・青の色が付いていて意味が読み取れない。
17. 上限の説明(`出力段階 LOW(低出力): トルク ≤ 0.5 N·m / 電流 ≤ 1 A / …`)が 11〜12px の緑文字で 2 行にわたって折り返していて読みにくい。式(`t = Kp·(p_set − p) + …`)は熟練者向けの情報なので折りたたむべき。
18. JOG では 2 つの方向ボタンが両方とも青い塗りで、主要操作が 2 つある状態になっている。`開始` は塗り、`停止` は枠という区別も画面によって揺れている(05 は塗り/枠、14 は枠/枠)。

**E. 下部タブ(09, 11)**

19. パラメータ表: `Flash 保存` が赤い枠になっているが、破壊的操作ではなく「永続化」の操作なので、caution に分類するのが妥当。編集した行の茶色の塗りが強すぎる(全行の塗りより、左端のバーと値セルの caution 色で示す方がよい)。凡例の小さな四角は色だけで区別している。数値の右揃えと index の等幅表示は良い。
20. ツール: **危険な操作(ID 変更、ゼロ点、ボーレート、プロトコル切替)と無害な操作(主動上報)が同じカード・同じ `実行...` ボタン**で並んでいる。カード内の説明と入力欄の縦位置がそろっていない。危険度でグループ分けし、専用の「保守」画面に移すべき。

**F. HIGH 出力の確認ダイアログ(12)**

21. 良い点: タイトルが具体的(「モータを固定してから高出力に切り替えてください」)、前提条件のチェックボックスで実行ボタンを有効化する(エラー防止)、ボタンのラベルが具体的な動詞(`HIGH に切り替える`)、Windows の並び順(実行が左、キャンセルが右)。
22. 改善点: 左上の大きなオレンジの「HIGH」バッジが**ボタンに見える**。アイコン(⚠)と小さなピルにする。実行ボタンの赤枠と、HIGH のオレンジで色の意味が揺れている。どちらかに統一する(推奨は caution)。チェックが外れた状態で既定ボタンが実行側にならないことを確認する。本文の「・」の箇条書きは 3 項目で適切。

**G. ステータスバー**

23. `シミュレータ(ダミーデータ)` が青い枠のピルでボタンに見える。出力段階がツールバー・インスペクタ・ステータスバーの 3 か所に表示されている。

### 5.2 Do / Don't チェックリスト

**レイアウトと階層**

- [ ] **Do**: コマンドバーは「接続/対象」「状態サマリ」「出力段階 + 非常停止」の 3 グループにする
- [ ] **Do**: 画面を「運転 / パラメータ / 診断 / 保守」に分け、左のサイドバーで切り替える
- [ ] **Do**: 接続設定は接続後にチップ(`● COM7 · 1 Mbps ▾`)へ畳み、クリックするとポップオーバーで詳細を出す
- [ ] **Do**: 操作対象のモーターはサイドバーのモーター一覧で選ぶ。表示する場所は 1 か所(インスペクタのヘッダー)にする
- [ ] **Do**: パネルの内側余白は 16、グループ間は 24、グループ内は 8 にする
- [ ] **Don't**: 枠を 2 段以上入れ子にしない。ドックのフロート/閉じるボタンを既定で表示しない
- [ ] **Don't**: 同じ情報(対象、出力段階、切断ボタン)を 2 か所以上に置かない

**操作と状態**

- [ ] **Do**: 1 つのペインにつき accent の塗りボタンは 1 個(インスペクタなら `開始`)
- [ ] **Do**: 状態はピル(操作不可、hover なし、アイコン + 文字)、操作はボタンにして、見た目を明確に分ける
- [ ] **Do**: 実行中に選べないモードは無効化し、ツールチップで理由を示す
- [ ] **Do**: モード選択は 5 個までのセグメンテッドコントロールにし、試験信号は診断/保守に移す
- [ ] **Do**: 停止ボタンのラベルに範囲を書く(`停止` = 選択中のモーター、`全停止`、`非常停止`)
- [ ] **Do**: 「モード」は制御モード専用の語にし、モーターの状態は「有効/無効」「実行中/停止中」と書く
- [ ] **Don't**: タブを状態の選択に使わない。状態ピルをボタンの形にしない
- [ ] **Don't**: 2 つの同格のボタン(JOG の正転/逆転)を両方 accent にしない

**色**

- [ ] **Do**: 正常時の画面はほぼグレーにする。色は accent(操作)と、異常・注意・実行中だけに使う
- [ ] **Do**: 赤 = 故障/非常停止、黄・橙 = HIGH/注意、緑 = 有効・実行中、青 = 操作できるもの・選択中。それ以外の意味では使わない
- [ ] **Do**: グラフの色はモーターごとに割り当て、目標値は破線、実測値は実線にする
- [ ] **Do**: すべての状態表示にアイコンか文字を添える
- [ ] **Don't**: LOW(既定・安全)に緑の大きな塗りを使わない。HIGH にだけ caution 色を付ける
- [ ] **Don't**: 故障ビットを緑の丸の羅列で表示しない(正常時は「故障なし」1 行、異常時は該当項目だけを critical で表示する)
- [ ] **Don't**: min/max の数字や説明文に意味色を付けない

**文字**

- [ ] **Do**: 12 / 14 / 20 / 28px の 4 段階にとどめる。値は等幅数字にする
- [ ] **Do**: ラベルは secondary 色、値は primary 色にして、値を主役にする
- [ ] **Don't**: 12px 未満の文字や、tertiary 色の文字で重要な情報(上限値など)を書かない

**フィードバックとエラー防止**

- [ ] **Do**: 続いている状態(HIGH 出力、故障、非常停止中、シミュレータ)はバナー/InfoBar で示し、解消するまで消えないようにする
- [ ] **Do**: 操作の結果はトーストで返す(「元に戻す」付きが望ましい)
- [ ] **Do**: 破壊的なダイアログは `QDialogButtonBox` の役割で並べ、具体的な動詞のボタンを使い、破壊側を既定ボタンにしない
- [ ] **Do**: ID 変更、ゼロ点、ボーレート、プロトコル切替は「保守」画面に隔離し、前提条件のチェック + 確認(必要なら長押しか ID の入力)を求める
- [ ] **Do**: 非常停止は画面内で唯一の赤い塗りにし、常に同じ位置に置いて、モーダル表示中もショートカットで効くようにする
- [ ] **Don't**: 停止や無効化のような日常操作に確認ダイアログを出さない
- [ ] **Don't**: 故障をトーストだけで知らせない

**数値入力**

- [ ] **Do**: 値の入力欄は右揃え・等幅数字にし、リセットはアイコン(↺)にし、スライダー上に実測値のマーカーを出す
- [ ] **Do**: 上限で値が切られたら、caution 色の文字でインライン表示する
- [ ] **Don't**: リセットボタンを「0」という文字だけにしない

---

## 6. 出典一覧

**GNOME HIG**
- https://developer.gnome.org/hig/principles.html
- https://developer.gnome.org/hig/patterns/controls/buttons.html
- https://developer.gnome.org/hig/patterns/controls/switches.html
- https://developer.gnome.org/hig/patterns/containers/header-bars.html
- https://developer.gnome.org/hig/patterns/containers/boxed-lists.html
- https://developer.gnome.org/hig/patterns/containers/utility-panes.html
- https://developer.gnome.org/hig/patterns/containers/popovers.html
- https://developer.gnome.org/hig/patterns/nav/sidebars.html
- https://developer.gnome.org/hig/patterns/nav/view-switchers.html
- https://developer.gnome.org/hig/patterns/feedback/dialogs.html
- https://developer.gnome.org/hig/patterns/feedback/banners.html
- https://developer.gnome.org/hig/patterns/feedback/toasts.html
- https://developer.gnome.org/hig/guidelines/typography.html
- https://developer.gnome.org/hig/guidelines/ui-styling.html
- https://developer.gnome.org/hig/guidelines/adaptive.html

**Apple HIG**
- https://developer.apple.com/design/human-interface-guidelines/layout
- https://developer.apple.com/design/human-interface-guidelines/toolbars
- https://developer.apple.com/design/human-interface-guidelines/sidebars
- https://developer.apple.com/design/human-interface-guidelines/split-views
- https://developer.apple.com/design/human-interface-guidelines/buttons
- https://developer.apple.com/design/human-interface-guidelines/alerts
- https://developer.apple.com/design/human-interface-guidelines/segmented-controls
- https://developer.apple.com/design/human-interface-guidelines/sliders
- https://developer.apple.com/design/human-interface-guidelines/color
- https://developer.apple.com/design/human-interface-guidelines/accessibility

**Microsoft Fluent 2 / Windows**
- https://fluent2.microsoft.design/layout
- https://fluent2.microsoft.design/color
- https://fluent2.microsoft.design/components/web/react/core/button/usage
- https://learn.microsoft.com/en-us/windows/apps/design/basics/content-basics
- https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography
- https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/geometry
- https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/color
- https://learn.microsoft.com/en-us/windows/apps/develop/platform/xaml/xaml-theme-resources
- https://learn.microsoft.com/en-us/windows/apps/design/controls/infobar
- https://learn.microsoft.com/en-us/windows/apps/design/controls/dialogs-and-flyouts/dialogs
- https://learn.microsoft.com/en-us/windows/apps/design/controls/command-bar
- https://learn.microsoft.com/en-us/windows/apps/design/controls/navigationview
- https://github.com/microsoft/microsoft-ui-xaml/blob/main/controls/dev/CommonStyles/Common_themeresources_any.xaml

**IBM Carbon**
- https://carbondesignsystem.com/patterns/status-indicator-pattern/
- https://carbondesignsystem.com/patterns/notification-pattern/
- https://carbondesignsystem.com/elements/spacing/overview/
- https://carbondesignsystem.com/components/modal/usage/
- https://carbondesignsystem.com/components/data-table/usage/

**Material 3 / Primer / Atlassian**
- https://m3.material.io/foundations/interaction/states/state-layers
- https://m3.material.io/styles/color/roles
- https://primer.style/product/components/label/
- https://atlassian.design/components/lozenge/usage

**原則・アクセシビリティ**
- https://www.refactoringui.com/
- https://lawsofux.com/
- https://www.nngroup.com/articles/ten-usability-heuristics/
- https://www.nngroup.com/articles/confirmation-dialog/
- https://www.nngroup.com/articles/toggle-switch-guidelines/
- https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html

**産業 HMI(補足、二次資料)**
- https://processcontrolguide.com/isa-101-hmi-design/
- https://www.se.com/uk/en/faqs/FA28043/

**Qt 実装メモ**
- https://doc.qt.io/qt-6/qdialogbuttonbox.html(ボタンの役割に応じて OS ごとの並び順を自動で適用する)
