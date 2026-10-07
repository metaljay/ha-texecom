"""Makes custom_components.texecom.{panel,connect,crestron} importable without
Home Assistant: the package's __init__ (which needs HA) is skipped."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "custom_components.texecom" not in sys.modules:
    for name, path in (
        ("custom_components", ROOT / "custom_components"),
        ("custom_components.texecom", ROOT / "custom_components" / "texecom"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
