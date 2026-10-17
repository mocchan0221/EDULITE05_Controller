"""UI 部品(ui_design.md §6)。ページは部品とトークンだけで組む(色・寸法を直書きしない)。

    from edulite05.app.ui import Button, Segmented, StatusBadge
    from edulite05.app.ui.style import tokens   # 現在のテーマの色(ui.tokens は定数モジュール)

トークンは `tokens.py`(定数・ダーク/ライト)、スタイルは `style.py`(QSS 生成・テーマ切替)。
"""

from edulite05.app.ui.badge import UI_STATE_STYLE, StatusBadge, ui_state_style
from edulite05.app.ui.button import Button, EStopButton, HoldButton
from edulite05.app.ui.chip import MotorChip
from edulite05.app.ui.confirm_dialog import ConfirmDialog
from edulite05.app.ui.empty import EmptyState
from edulite05.app.ui.header import ConnectionButton, Header
from edulite05.app.ui.icon import icon, pixmap
from edulite05.app.ui.infobar import BannerStack, InfoBar
from edulite05.app.ui.inputs import Combo, Switch, ValueSlider
from edulite05.app.ui.label import Label
from edulite05.app.ui.navrail import NavRail
from edulite05.app.ui.param_row import NumberEdit, ParamForm, ParamRow
from edulite05.app.ui.popover import Menu, Popover
from edulite05.app.ui.readout import Gauge, ReadoutCell, ReadoutDualCell
from edulite05.app.ui.section import Expander, Section
from edulite05.app.ui.segmented import Segmented
from edulite05.app.ui.style import theme_manager
from edulite05.app.ui.toast import ToastHost

__all__ = [
    "UI_STATE_STYLE",
    "BannerStack",
    "Button",
    "Combo",
    "ConnectionButton",
    "ConfirmDialog",
    "EStopButton",
    "EmptyState",
    "Expander",
    "Gauge",
    "Header",
    "HoldButton",
    "InfoBar",
    "Label",
    "Menu",
    "MotorChip",
    "NavRail",
    "NumberEdit",
    "ParamForm",
    "ParamRow",
    "Popover",
    "ReadoutCell",
    "ReadoutDualCell",
    "Section",
    "Segmented",
    "StatusBadge",
    "Switch",
    "ToastHost",
    "ValueSlider",
    "icon",
    "pixmap",
    "theme_manager",
    "ui_state_style",
]
