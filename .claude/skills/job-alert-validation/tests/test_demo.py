from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "demo" / "run_demo.py"


def test_demo_runs_offline(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(DEMO), "--jobs-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert "VERDICT: ✅ Verified" in result.stdout
    assert "VERDICT: ❌ Do not share" in result.stdout
    assert "Matches    : 1" in result.stdout
    saved = [path.name for path in tmp_path.glob("*.json")]
    assert len(saved) == 1 and saved[0].startswith("acme-labs_backend-engineer_")
