"""
Writer for audit outputs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def write_audit(generation: str, items: list[dict[str, Any]], path: Path) -> Path:
    payload = {
        "generation": str(generation),
        "count": len(items),
        "summary": {
            "PASS": sum(1 for item in items if item["result"]["status"] == "PASS"),
            "FAIL": sum(1 for item in items if item["result"]["status"] == "FAIL"),
            "INCONCLUSIVE": sum(1 for item in items if item["result"]["status"] == "INCONCLUSIVE"),
        },
        "items": items,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
