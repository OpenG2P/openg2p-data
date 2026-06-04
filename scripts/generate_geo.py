"""Parse master-data SQL files into a single geo/geo.csv.

Columns: level_value_id, level, mnemonic, parent_level_value_id

`level` is the human-readable level name (country/region/district/ward/village).
Levels are derivable from the data; no separate levels file is emitted.
"""

import re
from pathlib import Path

from _csv_utils import write_csv

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DATA = Path("/Volumes/Work/OpenG2P/master-data")
OUT_DIR = REPO_ROOT / "geo"

VALUE_ROW_RE = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(NULL|'[^']*')\s*\)",
    re.IGNORECASE,
)

LEVELS_RE = re.compile(
    r"VALUES\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(NULL|'[^']*')\s*\)",
    re.IGNORECASE,
)

LEVEL_SQL_FILES = [
    "level-0.sql",
    "level-1.sql",
    "level-2.sql",
    "level-3.sql",
    "level-4.sql",
]


def parse_level_name_by_id() -> dict[str, str]:
    """Read g2p_geo_levels.sql and return {level_id_uuid: level_mnemonic}."""
    text = (MASTER_DATA / "g2p_geo_levels.sql").read_text()
    return {
        level_id: mnemonic
        for level_id, mnemonic, _parent in LEVELS_RE.findall(text)
    }


def parse_value_rows(sql_path: Path, level_name_by_id: dict[str, str]) -> list[dict]:
    text = sql_path.read_text()
    rows = []
    for m in VALUE_ROW_RE.finditer(text):
        level_value_id, level_id, mnemonic, parent = m.groups()
        parent_value = None if parent.upper() == "NULL" else parent.strip("'")
        rows.append(
            {
                "level_value_id": level_value_id,
                "level": level_name_by_id.get(level_id, level_id),
                "mnemonic": mnemonic,
                "parent_level_value_id": parent_value,
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    level_name_by_id = parse_level_name_by_id()

    all_rows: list[dict] = []
    for sql_name in LEVEL_SQL_FILES:
        all_rows.extend(parse_value_rows(MASTER_DATA / sql_name, level_name_by_id))

    columns = ["level_value_id", "level", "mnemonic", "parent_level_value_id"]
    write_csv(OUT_DIR / "geo.csv", columns, all_rows)

    by_level: dict[str, int] = {}
    for r in all_rows:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
    print(f"Wrote geo.csv: {len(all_rows)} rows")
    for level, n in by_level.items():
        print(f"  {level}: {n}")


if __name__ == "__main__":
    main()
