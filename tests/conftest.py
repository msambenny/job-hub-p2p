import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hub.reuse  # noqa: E402,F401  (puts the vendored skill scripts on sys.path)
