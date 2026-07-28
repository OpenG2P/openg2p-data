#!/usr/bin/env python3
"""Generate nested GeoJSON boundaries for the deployment's MDS geo hierarchy.

Why this exists
---------------
A choropleth needs polygons, and MDS carries only codes and names — there is no
`boundary_uri` or geometry column in the deployed schema. For a real country you
would load actual boundary files (geoBoundaries, national GIS) and join them on
pcode. For the shipped sample the geography is synthetic, so no real boundary
file exists for it at all, and the map surface would have nothing to draw.

This produces a tessellation instead: the country is one rectangle, and each
level's children subdivide their parent's rectangle. That is not real geography,
but it is *correct* geography for drill-down purposes — children nest exactly
inside parents, every node has exactly one polygon, and zooming from region to
district to ward behaves the way it will with real boundaries. Swap in real
files and the Evidence pages need no change.

Output: one GeoJSON FeatureCollection per level, named by that level's mnemonic,
so nothing downstream hardcodes a country's level names.

    python generate_geo_boundaries.py --out ../../evidence/static/geo

Each feature carries:
    level_value_id          join key back to the register's geo_N_id columns
    level_value_mnemonic    display label
    level_id / depth        which tier it belongs to
    parent_level_value_id   for building drill-down links
"""

from __future__ import annotations

import argparse
import json
import math
import os

import psycopg2

# Neutral bounding box. Deliberately not a real country's extent — this is
# synthetic geography and shouldn't be mistaken for a real place.
WORLD = {"min_lon": 0.0, "min_lat": 0.0, "max_lon": 10.0, "max_lat": 10.0}

# Shrink each child slightly inside its parent so borders are visible and
# adjacent polygons don't z-fight when rendered.
INSET = 0.012


def fetch_hierarchy(conn):
    with conn.cursor() as cur:
        cur.execute("select level_id, level_mnemonic, parent_level_id from g2p_geo_levels")
        levels = {r[0]: {"mnemonic": r[1], "parent": r[2]} for r in cur.fetchall()}
        cur.execute(
            "select level_id, level_value_id, level_value_mnemonic, parent_level_value_id"
            " from g2p_geo_level_values order by level_id, level_value_id"
        )
        values = [
            {"level_id": r[0], "id": r[1], "name": r[2], "parent": r[3]}
            for r in cur.fetchall()
        ]
    if not levels or not values:
        raise SystemExit("MDS geo hierarchy is empty — nothing to generate.")

    # Order levels root -> leaf by walking the parent chain.
    roots = [lid for lid, lv in levels.items() if not lv["parent"]]
    order, frontier = [], roots
    while frontier:
        order.extend(frontier)
        frontier = [lid for lid, lv in levels.items() if lv["parent"] in frontier]
    return levels, values, order


def subdivide(box, n):
    """Split a box into n tiles, kept as square-ish as possible."""
    if n <= 1:
        return [box]
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = max(1, int(math.ceil(n / cols)))
    w = (box["max_lon"] - box["min_lon"]) / cols
    h = (box["max_lat"] - box["min_lat"]) / rows
    tiles = []
    for i in range(n):
        r, c = divmod(i, cols)
        tiles.append({
            "min_lon": box["min_lon"] + c * w,
            "max_lon": box["min_lon"] + (c + 1) * w,
            "min_lat": box["min_lat"] + r * h,
            "max_lat": box["min_lat"] + (r + 1) * h,
        })
    return tiles


def inset(box):
    """Shrink a tile slightly so borders are visible between neighbours.

    Applied when the box is ASSIGNED, not when it is rendered, so that a
    child subdivides its parent's already-shrunk area. Insetting at render
    time instead lets children spill outside the parent's drawn polygon,
    which breaks drill-down: zooming into a region would show districts
    poking out of it.
    """
    dx = (box["max_lon"] - box["min_lon"]) * INSET
    dy = (box["max_lat"] - box["min_lat"]) * INSET
    return {
        "min_lon": box["min_lon"] + dx, "max_lon": box["max_lon"] - dx,
        "min_lat": box["min_lat"] + dy, "max_lat": box["max_lat"] - dy,
    }


def to_polygon(box):
    x0, y0 = box["min_lon"], box["min_lat"]
    x1, y1 = box["max_lon"], box["max_lat"]
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--geo-db", default=os.environ.get("MDS_DB", "master_data"))
    p.add_argument("--out", required=True, help="directory to write GeoJSON into")
    args = p.parse_args()

    conn = psycopg2.connect(
        dbname=args.geo_db,
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )
    levels, values, order = fetch_hierarchy(conn)
    by_parent = {}
    for v in values:
        by_parent.setdefault(v["parent"], []).append(v)

    os.makedirs(args.out, exist_ok=True)
    boxes = {}          # level_value_id -> box
    written = []

    for depth, level_id in enumerate(order, start=1):
        nodes = [v for v in values if v["level_id"] == level_id]
        if not nodes:
            continue

        # Root tier occupies the whole extent; every other tier tiles its parent.
        if depth == 1:
            for i, tile in enumerate(subdivide(WORLD, len(nodes))):
                boxes[nodes[i]["id"]] = inset(tile)
        else:
            for parent_id, siblings in by_parent.items():
                if not siblings or siblings[0]["level_id"] != level_id:
                    continue
                parent_box = boxes.get(parent_id)
                if parent_box is None:
                    continue
                for i, tile in enumerate(subdivide(parent_box, len(siblings))):
                    boxes[siblings[i]["id"]] = inset(tile)

        features = []
        for n in nodes:
            box = boxes.get(n["id"])
            if box is None:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "level_value_id": n["id"],
                    "level_value_mnemonic": n["name"],
                    "level_id": n["level_id"],
                    "depth": depth,
                    "parent_level_value_id": n["parent"],
                },
                "geometry": {"type": "Polygon", "coordinates": to_polygon(box)},
            })

        name = levels[level_id]["mnemonic"] or level_id
        path = os.path.join(args.out, f"{name}.geojson")
        with open(path, "w") as fh:
            json.dump({"type": "FeatureCollection", "features": features}, fh)
        written.append((name, len(features), path))
        print(f"[geo] {name:<12} {len(features):>5} features -> {path}")

    index = os.path.join(args.out, "levels.json")
    with open(index, "w") as fh:
        json.dump([
            {"depth": d, "level_id": lid,
             "level_name": levels[lid]["mnemonic"],
             "file": f"{levels[lid]['mnemonic'] or lid}.geojson"}
            for d, lid in enumerate(order, start=1)
            if any(v["level_id"] == lid for v in values)
        ], fh, indent=2)
    print(f"[geo] wrote level index -> {index}")


if __name__ == "__main__":
    main()
