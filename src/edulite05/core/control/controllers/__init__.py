"""組込みコントローラ(phase2.md §1.3)。"""

from __future__ import annotations

from edulite05.core.control.controller import Controller
from edulite05.core.control.controllers.bilateral import Bilateral
from edulite05.core.control.controllers.current import Current
from edulite05.core.control.controllers.jog import Jog
from edulite05.core.control.controllers.mit import MitDirect
from edulite05.core.control.controllers.position import Csp, Pp
from edulite05.core.control.controllers.tests import SineTest, StepTest
from edulite05.core.control.controllers.velocity import Velocity
from edulite05.core.control.controllers.zero_return import ZeroReturn

BUILTIN_CONTROLLERS: tuple[type[Controller], ...] = (
    MitDirect,
    Jog,
    Velocity,
    Current,
    Csp,
    Pp,
    SineTest,
    StepTest,
    ZeroReturn,
    Bilateral,
)

# Controller.name → クラス
CONTROLLERS: dict[str, type[Controller]] = {c.name: c for c in BUILTIN_CONTROLLERS}



def register_controller(cls: type[Controller]) -> None:
    """プラグイン用: Controller を `CONTROLLERS` に追加する(plugin_guide.md)。

    組込みと同名のものは置き換えられない(`ValueError`)。同じクラスの再登録は何もしない。
    指令はすべて `MotorService` → `ControlLoop` → `SafetyGuard` を通る(迂回路は作らない)。
    """
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError("Controller.name が必要です")
    for attr in ("label", "Params", "run_mode"):
        if not hasattr(cls, attr):
            raise ValueError(f"{cls.__name__}.{attr} が必要です")
    old = CONTROLLERS.get(name)
    if old is cls:
        return
    if old is not None:
        raise ValueError(f"Controller 名 {name!r} は登録済みです({old.__name__})")
    CONTROLLERS[name] = cls


def unregister_controller(name: str) -> None:
    """`register_controller` で足したものを外す(組込みは外せない)。"""
    if any(c.name == name for c in BUILTIN_CONTROLLERS):
        raise ValueError(f"組込みの Controller は外せません: {name!r}")
    CONTROLLERS.pop(name, None)


__all__ = [
    "register_controller",
    "unregister_controller",
    "Bilateral",
    "BUILTIN_CONTROLLERS",
    "CONTROLLERS",
    "Csp",
    "Current",
    "Jog",
    "MitDirect",
    "Pp",
    "SineTest",
    "StepTest",
    "Velocity",
    "ZeroReturn",
]
