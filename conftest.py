"""Root conftest for the dog-monitoring project.

Ensures the repository root is importable so that ``import dog_monitoring``
works regardless of the current working directory. pytest adds the directory
containing this conftest.py (the repo root) to ``sys.path`` automatically in
``prepend`` import mode, but being explicit is clearer.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

