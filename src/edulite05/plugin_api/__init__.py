"""プラグイン API(architecture.md §4.8 / ui_design.md §10 / docs/design/plugin_guide.md)。

このパッケージは Qt を import しない。UI の部品は factory(呼び出し可能な物)として登録するだけで、
ウィジェットを作るのは GUI シェル。
"""

from edulite05.plugin_api.api import (
    CoreRegistry,
    JsonFileStateStore,
    MemoryStateStore,
    Plugin,
    PluginContext,
    PluginSettings,
    PluginStatus,
    StateStore,
)
from edulite05.plugin_api.manager import (
    ENTRY_POINT_GROUP,
    PluginManager,
    PluginRecord,
    load_plugins,
)
from edulite05.plugin_api.ui import (
    GROUP_MAIN,
    GROUP_OTHER,
    MAINTENANCE_CAUTION,
    MAINTENANCE_COMMON,
    ControllerForm,
    LazyClassFactory,
    MaintenanceItemReg,
    ModeSpec,
    MonitorViewReg,
    MoreActionReg,
    PageReg,
    SettingsSectionReg,
    UiRegistry,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "GROUP_MAIN",
    "GROUP_OTHER",
    "MAINTENANCE_CAUTION",
    "MAINTENANCE_COMMON",
    "ControllerForm",
    "CoreRegistry",
    "JsonFileStateStore",
    "LazyClassFactory",
    "MaintenanceItemReg",
    "MemoryStateStore",
    "ModeSpec",
    "MonitorViewReg",
    "MoreActionReg",
    "PageReg",
    "Plugin",
    "PluginContext",
    "PluginManager",
    "PluginRecord",
    "PluginSettings",
    "PluginStatus",
    "SettingsSectionReg",
    "StateStore",
    "UiRegistry",
    "load_plugins",
]
