"""Shared package initialization.

This module tweaks ``sys.path`` so that the ``contracts`` package is
importable when running outside of Docker or when ``PYTHONPATH`` is not
configured.  Without this adjustment, imports such as ``from
contracts.schemas import AnalyzeV1`` may fail because Python cannot locate
``packages/contracts/src`` on the import path.

The adjustment is harmless when ``contracts`` is already on
``sys.path`` (``sys.path`` is checked before insertion) and ensures a
consistent developer experience.
"""

import sys
from pathlib import Path

# Determine project root: ``shared/__init__.py`` is located at
# ``<root>/shared/__init__.py``.  The root directory is one level up.
_root = Path(__file__).resolve().parents[1]
_contracts_path = _root / 'packages' / 'contracts' / 'src'
_contracts_str = str(_contracts_path)
if _contracts_str not in sys.path:
    sys.path.insert(0, _contracts_str)