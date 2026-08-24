from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_independent_decision_overlay_example() -> None:
    root = Path(__file__).parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "examples" / "decision_overlay_consumer.py")],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["current_hidden"] is True
    assert result["stale_reopened"] is True
    assert result["stale_reasons"] == ["candidate_or_member_evidence_changed"]
