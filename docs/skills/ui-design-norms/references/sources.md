# 出典

規範の根拠とした資料。産業 HMI と安全の規格(ISA-101、ISA-18.2、IEC 60204-1、ISO 13850、EEMUA 191)は本文が有償のため、解説記事・ベンダーの資料から要旨を引いている。適合性を主張する用途では原文を確認する。

## OS の HIG

**GNOME Human Interface Guidelines** — 段階的開示、色付きボタンは 1 ビューに 1 個、バナーとトーストの使い分け、確認ダイアログ、スイッチ、ボックスリスト、アダプティブ

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

**Apple Human Interface Guidelines** — 重要なものを上・先頭側に、下端に重要な操作を置かない、ツールバーのグループは 3 つまで、優先度はスタイルで、セグメンテッドコントロール、色の意味の固定、アラートの文面

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

**Microsoft Fluent 2 / Windows** — 4px の間隔スケール、タイプランプ、角丸、レイヤリング、InfoBar、ContentDialog の並び順、NavigationView、Primary ボタンは 1 つ、コントロールの高さ

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

## デザインシステムと原則

**IBM Carbon** — 状態表示は色・形・記号の 2 つ以上、通知の種類、間隔トークン、モーダル、表の行の高さ

- https://carbondesignsystem.com/patterns/status-indicator-pattern/
- https://carbondesignsystem.com/patterns/notification-pattern/
- https://carbondesignsystem.com/elements/spacing/overview/
- https://carbondesignsystem.com/components/modal/usage/
- https://carbondesignsystem.com/components/data-table/usage/

**Material 3 / GitHub Primer / Atlassian** — ステートレイヤーの比率、カラーロール、ステータスラベルは操作できない部品

- https://m3.material.io/foundations/interaction/states/state-layers
- https://m3.material.io/styles/color/roles
- https://primer.style/product/components/label/
- https://atlassian.design/components/lozenge/usage

**原則・ユーザビリティ** — 強弱は太さと濃淡で、ラベルは最後の手段、枠線を減らす(Refactoring UI)。Hick・Fitts・Doherty・Tesler・近接の法則。10 のヒューリスティクス、確認ダイアログの出しすぎ、スイッチ

- https://www.refactoringui.com/
- https://lawsofux.com/
- https://www.nngroup.com/articles/ten-usability-heuristics/
- https://www.nngroup.com/articles/confirmation-dialog/
- https://www.nngroup.com/articles/toggle-switch-guidelines/

**アクセシビリティ(WCAG 2.2)** — コントラスト、非テキストのコントラスト、ターゲットサイズ、色だけに頼らない、点滅

- https://www.w3.org/TR/WCAG22/
- https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
- https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html
- https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html

## 産業 HMI・警報・安全

**ISA-101 / High Performance HMI** — 無彩色の基調、色は異常と操作が必要なときだけ、4 階層の表示、Moving Analog Indicator、冗長符号化

- https://processcontrolguide.com/isa-101-hmi-design/
- https://hmilibrary.com/standards/isa-101
- https://industrialmonitordirect.com/blogs/knowledgebase/isa-101-high-performance-hmi-design-principles-color-strategy
- https://adroit-europe.com/hphmi
- https://corsosystems.com/posts/the-high-performance-hmi-handbook-and-you-part-1
- https://inductiveautomation.com/resources/article/design-like-a-pro-optimizing-your-hmi
- https://www.docs.inductiveautomation.com/docs/8.1/ignition-modules/vision/common-tasks-in-vision/high-performance-hmitechniques
- https://www.docs.inductiveautomation.com/docs/7.9/appendix/components/display/moving-analog-indicator

**ISA-18.2 / EEMUA 191(警報管理)** — 警報の状態、警報とログの分離、警報の頻度

- https://documentation.iconics.com/v10.97.3/Content/Alarming/Alarm%20Server/Alarm%20References/alarm-state-transition-diagram.htm
- https://support.industry.siemens.com/cs/attachments/109772836/WP_Alarm_Management_ISA_18.pdf
- https://instrumentationtools.com/isa-18-2-alarm-management-in-process-plants/
- https://humanfactors101.com/topics/alarms/

**ISO 13850 / IEC 60204-1(非常停止・押しボタンの色・停止カテゴリ)**、イネーブル装置、Select Before Operate

- https://machinerysafety101.com/2026/05/18/iso-13850-emergency-stop-requirements/
- https://www.idec.com/en-eu/solutions/safety/law/iso-iec/iso13850
- https://library.e.abb.com/public/e32f367d17642f29852575d20068205e/1SXU150186X0201.pdf
- https://incompliancemag.com/understanding-symbols-emergency-stop/
- https://www.se.com/uk/en/faqs/FA28043/
- https://info.bannerengineering.com/cs/groups/public/documents/literature/151822.pdf
- https://industrialmonitordirect.com/blogs/knowledgebase/configuring-siemens-iec-61850-hmi-breaker-control-via-wincc

## 装置の操作ツール・計測ツールの例

状態を中心にした操作、許可と起動の分離、故障の見せ方、運転中の書き込みの禁止、2 段の詳細度

- ODrive: https://docs.odriverobotics.com/v/latest/interfaces/gui.html
- mjbots moteus tview: https://blog.mjbots.com/2025/10/16/device-fault-monitoring-in-tview/
- ROBOTIS Dynamixel Wizard 2.0: https://emanual.robotis.com/docs/en/software/dynamixel/dynamixel_wizard2/
- SimpleFOC Studio: https://docs.simplefoc.com/studio
- Siemens SINAMICS Startdrive: https://cache.industry.siemens.com/dl/files/584/109781584/att_1029654/v1/S120_startdrive_getting_started_0620_en-US.pdf
- Beckhoff TwinCAT Drive Manager 2: https://infosys.beckhoff.com/content/1033/elm72xx/10518514699.html
- Copley CME 2: https://copleycontrols.com/wp-content/uploads/2018/02/CME-User-Guide.pdf
- Teknic ClearView: https://teknic.com/files/downloads/Clearpath-SC%20User%20Manual.pdf

リアルタイムグラフ: チャンネル色の一貫性、ホバー計測とマーカー、分割と同期、一時停止、状態の帯

- Saleae Logic 2: https://support.saleae.com/user-guide/using-logic/measurements-timing-markers
- PlotJuggler: https://github.com/facontidavide/PlotJuggler
- Keysight InfiniiVision: https://www.ece.ucdavis.edu/~lewis/112/scope_user_guide.pdf
- Grafana State timeline: https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/state-timeline/

## 文章規範

- japanese-tech-writing: https://gist.github.com/k16shikano/fd287c3133457c4fd8f5601d34aa817d
- cognitive-rhythm-writing: https://gist.github.com/k16shikano/eb2929f13ed19c97188393d297be8432
