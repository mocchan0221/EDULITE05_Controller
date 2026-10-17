"""パッケージが import できること。"""

import importlib

import pytest

MODULES = [
    "edulite05",
    "edulite05.core.transport",
    "edulite05.core.protocol",
    "edulite05.core.device",
    "edulite05.core.control",
    "edulite05.core.telemetry",
    "edulite05.sim",
    "edulite05.plugin_api",
    "edulite05.builtin",
    "edulite05.app",
    "edulite05.app.ui",
    "edulite05.app.pages",
    "edulite05.app.gallery",
    "edulite05.app.i18n",
    "edulite05.cli",
    "edulite05.cli.main",
]


@pytest.mark.parametrize("name", MODULES)
def test_import(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_entry_points_callable() -> None:
    from edulite05.app.main import main as gui_main
    from edulite05.cli.main import main as cli_main

    assert callable(gui_main)
    assert callable(cli_main)
