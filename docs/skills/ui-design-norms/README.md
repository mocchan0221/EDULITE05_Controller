# ui-design-norms

アプリやツールの UI を設計・実装・レビューするときの規範をまとめた [Claude Code](https://docs.claude.com/en/docs/claude-code) のスキルです。計測・制御・安全に関わるリアルタイム UI(非常停止、出力段階、通信断、リアルタイムグラフ)の追加規範も含みます。

参照なしに UI を設計すると、生成モデルは「色を使いすぎる」「状態表示とボタンが同じ形になる」「どのボタンが何を起こすか分からない」といった失敗をしやすくなります。このスキルは、GNOME / Apple / Microsoft の HIG、IBM Carbon などのデザインシステム、ISA-101(High Performance HMI)、WCAG 2.2、Nielsen Norman Group の知見から、そうした失敗を防ぐ規則を抜き出したものです。各規則には 1 行の理由と、必要に応じて出典を付けています。

## 構成

| ファイル | 内容 |
|---|---|
| `SKILL.md` | 規範の本体(色、階層と余白、状態と操作の区別、主操作、レイアウト、通知、入力と値、文言、確認、安全・リアルタイム系、検証、実装の落とし穴、AI が陥りやすい失敗) |
| `references/sources.md` | 出典の URL |
| `references/tokens_example.md` | デザイントークンの出発点(色の役割、ライト/ダーク、系列色、文字、間隔) |

## 導入

スキルのフォルダを Claude Code のスキルの置き場所にコピーします。

- 個人用(すべてのプロジェクトで使う): `~/.claude/skills/ui-design-norms/`
- プロジェクト用(リポジトリで共有する): `<project>/.claude/skills/ui-design-norms/`

```sh
git clone <this-repository> ~/.claude/skills/ui-design-norms
```

UI の設計・実装・レビューの依頼(「画面を設計して」「配色を決めて」「スクリーンショットをレビューして」など)で、Claude Code がスキルを読み込みます。

## 言語

本文は日本語です。スキルの説明(front matter の `description`)は英語と日本語の両方で書いてあるので、英語の依頼でも読み込まれます。

## 注意

産業 HMI と安全の規格(ISA-101、ISA-18.2、IEC 60204-1、ISO 13850)は本文が有償のため、解説記事やベンダーの資料から要旨を引いています。規格への適合を主張する用途では、原文を確認してください。画面上の非常停止はソフトウェアの停止であり、物理的な非常停止装置の代わりにはなりません。

## ライセンス

MIT License(`LICENSE` を参照)。
