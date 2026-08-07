"""Make `server.py` (one directory up) importable when pytest runs from servers/ast."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve.parents[1]))
