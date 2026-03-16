"""Common shared package initialization.

This module tweaks ``sys.path`` so that the ``contracts`` package is
importable when running outside of Docker or when ``PYTHONPATH`` is not
configured. Without this adjustment, imports such as
``from contracts.schemas import AnalyzeV1`` may fail because Python cannot
locate ``packages/contracts/src`` on the import path.

The adjustment is harmless when ``contracts`` is already on ``sys.path`` and
keeps local runs a little less cursed.
"""

import sys
from pathlib import Path

# ``libs/common/__init__.py`` lives at ``<root>/libs/common/__init__.py``.
# The project root is therefore two levels up.
_root = Path(__file__).resolve().parents[2]
_contracts_path = _root / 'packages' / 'contracts' / 'src'
_contracts_str = str(_contracts_path)
if _contracts_str not in sys.path:
    sys.path.insert(0, _contracts_str)
