"""確認・保守ダイアログ(ui_design.md §3・§5.4・§5.7)。

    from edulite05.app.ui.dialogs import (
        confirm_flash_save, confirm_high_tier, confirm_release_estop)

- `HighTierDialog` / `confirm_high_tier(parent)` … 出力段階 HIGH への切替(前提 3 つ必須)
- `EStopReleaseDialog` / `confirm_release_estop(parent)` … 非常停止の解除(安全確認が必須)
- `FlashSaveDialog` / `confirm_flash_save(parent, motor_id, items)` … Flash 保存の確認
- 保守ダイアログ(`maintenance.py`)… 保守ページが使う。基底は `ActionDialog`(`base.py`)
"""

from edulite05.app.ui.dialogs.base import ActionDialog
from edulite05.app.ui.dialogs.estop_release import EStopReleaseDialog, confirm_release_estop
from edulite05.app.ui.dialogs.flash_save import FlashItem, FlashSaveDialog, confirm_flash_save
from edulite05.app.ui.dialogs.high_tier import HighTierDialog, confirm_high_tier
from edulite05.app.ui.dialogs.maintenance import (
    ActiveReportDialog,
    CanTimeoutDialog,
    MotorTarget,
    RestoreProtocolDialog,
    SetBaudDialog,
    SetCanIdDialog,
    SetZeroDialog,
    SwitchProtocolDialog,
    ToolDialog,
)

__all__ = [
    "ActionDialog",
    "ActiveReportDialog",
    "CanTimeoutDialog",
    "EStopReleaseDialog",
    "FlashItem",
    "FlashSaveDialog",
    "HighTierDialog",
    "MotorTarget",
    "RestoreProtocolDialog",
    "SetBaudDialog",
    "SetCanIdDialog",
    "SetZeroDialog",
    "SwitchProtocolDialog",
    "ToolDialog",
    "confirm_flash_save",
    "confirm_high_tier",
    "confirm_release_estop",
]
