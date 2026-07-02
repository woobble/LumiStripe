from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_SRC = ROOT / "apps" / "lumistripe-app" / "src"
CORE_SRC = ROOT / "packages" / "lumistripe-core" / "src"

for path in (str(APP_SRC), str(CORE_SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)
