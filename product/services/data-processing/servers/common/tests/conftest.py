"""Test path setup: the supervisor and model client live in the DP service's app/
package (imported by nothing in prior — wires them). Their tests run here, in the
framework venv, so the DP suite stays byte-identical this stage. app/ is stdlib+httpx
only on these modules, so the framework venv can import them."""
from __future__ import annotations

import sys
from pathlib import Path

# servers/common/tests -> data-processing service root
SERVICE_ROOT = Path(__file__).resolve.parents[3]
if str(SERVICE_ROOT) not in sys.path:
 sys.path.insert(0, str(SERVICE_ROOT))
