"""Parse master-data SQL files into multiple geo CSVs.

Outputs:
- geo/geo_hierarchy.csv         level definitions: level_id, level_mnemonic, parent_level_id
- geo/<Level>.csv (one per level, e.g. Country.csv, Region.csv, District.csv, Ward.csv,
  Village.csv): level_value_id, level_value_mnemonic, parent_level_value_id

The set of levels (and therefore the per-level CSV files) is derived from
g2p_geo_levels.sql; nothing is hard-coded.
"""

import re
from pathlib import Path

from _csv_utils import write_csv

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DATA = Path("/Volumes/Work/OpenG2P/master-data")
OUT_DIR = REPO_ROOT / "geo"

# A level value row: (level_value_id, level_id, level_value_mnemonic, parent_level_value_id)
VALUE_ROW_RE = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(NULL|'[^']*')\s*\)",
    re.IGNORECASE,
)

# A level definition row: (level_id, level_mnemonic, parent_level_id)
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


def _unquote(token: str) -> str | None:
    return None if token.upper() == "NULL" else token.strip("'")


def parse_levels() -> list[dict]:
    """Read g2p_geo_levels.sql -> ordered list of level definitions (root first)."""
    text = (MASTER_DATA / "g2p_geo_levels.sql").read_text()
    levels = [
        {
            "level_id": level_id,
            "level_mnemonic": mnemonic,
            "parent_level_id": _unquote(parent),
        }
        for level_id, mnemonic, parent in LEVELS_RE.findall(text)
    ]
    # Order root -> leaf by walking the parent chain.
    by_id = {lv["level_id"]: lv for lv in levels}
    ordered: list[dict] = []
    roots = [lv for lv in levels if not lv["parent_level_id"]]
    frontier = roots
    seen = set()
    while frontier:
        nxt = []
        for lv in frontier:
            if lv["level_id"] in seen:
                continue
            seen.add(lv["level_id"])
            ordered.append(lv)
            nxt.extend(
                c for c in levels if c["parent_level_id"] == lv["level_id"]
            )
        frontier = nxt
    # Append any orphans not reachable from a root, preserving file order.
    for lv in levels:
        if lv["level_id"] not in seen:
            ordered.append(lv)
    return ordered


def parse_values() -> dict[str, list[dict]]:
    """Read level-*.sql -> {level_id: [value rows]}."""
    by_level: dict[str, list[dict]] = {}
    for sql_name in LEVEL_SQL_FILES:
        path = MASTER_DATA / sql_name
        if not path.is_file():
            continue
        for m in VALUE_ROW_RE.finditer(path.read_text()):
            level_value_id, level_id, mnemonic, parent = m.groups()
            by_level.setdefault(level_id, []).append(
                {
                    "level_value_id": level_value_id,
                    "level_value_mnemonic": mnemonic,
                    "parent_level_value_id": _unquote(parent),
                }
            )
    return by_level


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    levels = parse_levels()
    values_by_level = parse_values()

    # Short, neat, unique IDs. These geo IDs are internal to openg2p-data's
    # generation pipeline (they flow into demography); they are not loaded into
    # any database directly, so they can be remapped freely as long as parent
    # links stay consistent.
    #   level_id      -> l0..l4 (by depth, root first)
    #   level_value_id-> <mnemonic-initial><n>  e.g. c1, r1..r4, d01.., w01.., v001..
    vid_map: dict[str, str] = {}
    value_rows_by_mnemonic: dict[str, list[dict]] = {}
    for lv in levels:
        vals = values_by_level.get(lv["level_id"], [])
        prefix = lv["level_mnemonic"][0]
        width = max(1, len(str(len(vals))))
        for i, v in enumerate(vals, start=1):
            vid_map[v["level_value_id"]] = f"{prefix}{i:0{width}d}"
        value_rows_by_mnemonic[lv["level_mnemonic"]] = [
            {
                "level_value_id": vid_map[v["level_value_id"]],
                "level_value_mnemonic": v["level_value_mnemonic"],
                "parent_level_value_id": (
                    vid_map.get(v["parent_level_value_id"])
                    if v["parent_level_value_id"]
                    else None
                ),
            }
            for v in vals
        ]

    # geo_hierarchy.csv — level definitions with short level ids (l0..l4).
    hierarchy_rows = [
        {
            "level_id": f"l{depth}",
            "level_mnemonic": lv["level_mnemonic"],
            "parent_level_id": f"l{depth - 1}" if depth > 0 else None,
        }
        for depth, lv in enumerate(levels)
    ]
    write_csv(
        OUT_DIR / "geo_hierarchy.csv",
        ["level_id", "level_mnemonic", "parent_level_id"],
        hierarchy_rows,
    )
    print(f"Wrote geo_hierarchy.csv: {len(hierarchy_rows)} levels")

    # One CSV per level, named by the (capitalised) level mnemonic.
    value_columns = ["level_value_id", "level_value_mnemonic", "parent_level_value_id"]
    for lv in levels:
        rows = value_rows_by_mnemonic[lv["level_mnemonic"]]
        fname = f"{lv['level_mnemonic'].capitalize()}.csv"
        write_csv(OUT_DIR / fname, value_columns, rows)
        print(f"Wrote {fname}: {len(rows)} {lv['level_mnemonic']} values")


if __name__ == "__main__":
    main()
