"""Test bootstrap.

The engine is pure Python: register an empty package for
``custom_components.wc_fan`` so that importing ``engine``/``const`` does not
execute ``__init__.py`` (which needs Home Assistant).
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = ROOT / "custom_components" / "wc_fan"

for name, path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.wc_fan", PKG_DIR),
):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules.setdefault(name, module)
