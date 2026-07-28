"""Shared CSV helpers."""

import csv
import json
from pathlib import Path


def _cell(value):
    """Render a Python value as a CSV cell.

    - dict/list -> compact JSON
    - None      -> empty string
    - bool/int/float/str -> str()
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(columns)
        for r in rows:
            w.writerow([_cell(r.get(c)) for c in columns])
