#!/usr/bin/env python3
"""Check that a directory is a valid country pack.

    python validate_pack.py ../../packs/ETH ../../packs/XKM

Exits non-zero on the first pack that fails, so this works as a CI gate and as a
pre-commit check after regenerating a pack.

Why this exists
---------------
A pack is a contract between three things that never run together: a generator
(fetch_country_pack.py or generate_synthetic_pack.py) writes it, MDS's
load_geo_pack.py seeds a database from it, and the Evidence map surface draws it.
A pack that is subtly wrong does not fail loudly — it seeds a hierarchy with
orphans, or draws a choropleth with holes in it, and the damage surfaces days
later as figures that do not add up.

The checks below are the ones that have actually caught bugs:

  - children escaping their parent. The first synthetic boundary generator inset
    each child at *render* time rather than at assignment, so every single child
    at every level poked outside its parent. Drill-down looked fine until you
    zoomed in.
  - geometry and hierarchy disagreeing. A unit with no polygon vanishes from the
    map while still counting in the totals, so the map and the KPI above it tell
    different stories.
  - non-hierarchical P-codes. The whole reason for using P-codes as identifiers
    is that a child's code contains its parent's; code that relies on that
    breaks silently when it stops being true.
  - unit_counts drifting from reality after a hand edit.

Checks are grouped as errors (the pack is unusable) and warnings (the pack works
but something is off).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from _geometry import (
    outer_rings,
    point_in_polygon,
    polygon_area,
    ring_is_simple,
    rings,
)

# A child's area may fall this far short of a clean partition of its parent
# before it counts as a gap. Simplification moves vertices, so an exact match is
# not achievable for a real pack; COD-AB levels are independently simplified and
# routinely differ by a few percent.
AREA_TOLERANCE = 0.06

# Fraction of a child's sampled interior points that must land inside the parent.
# Not 100%: a simplified child and a simplified parent disagree along their
# shared boundary, so points near an edge legitimately fall outside.
NESTING_THRESHOLD = 0.90

REQUIRED_MANIFEST = ["country", "source", "license", "levels", "unit_counts"]


class Report:
    def __init__(self, name):
        self.name = name
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def ok(self):
        return not self.errors


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def sample_interior(geom, n=12):
    """A handful of points genuinely inside `geom`, for the nesting test.

    Vertices are the wrong thing to test: a child's vertices sit *on* the shared
    boundary, where inside/outside is ambiguous once either polygon has been
    simplified. So candidates are drawn inwards from the vertices instead — and
    then each is confirmed to be inside the child before it is used.

    That confirmation is the part that matters. Without it a doughnut-shaped
    unit fails its own test: Ethiopia's ET0420 (Shager City) encircles Addis
    Ababa, its vertex mean falls in the hole, and every point drawn towards that
    mean lands outside the unit — and therefore outside its parent too, which
    reads as a nesting violation when nothing is wrong. Only points inside the
    child can say anything about whether the child is inside its parent.
    """
    out = []
    for ring in outer_rings(geom):
        pts = ring[:-1] if ring and ring[0] == ring[-1] else ring
        if not pts:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        step = max(1, len(pts) // n)
        for cand in [(cx, cy)] + [(p[0] + 0.7 * (cx - p[0]), p[1] + 0.7 * (cy - p[1]))
                                  for p in pts[::step]]:
            if point_in_polygon(cand, geom):
                out.append(cand)
    return out


def validate(pack_dir):
    r = Report(os.path.basename(pack_dir.rstrip("/")))

    for f in ("levels.json", "values.json", "manifest.json"):
        if not os.path.exists(os.path.join(pack_dir, f)):
            r.error(f"missing {f}")
    if not r.ok():
        return r

    levels = read_json(os.path.join(pack_dir, "levels.json"))
    values = read_json(os.path.join(pack_dir, "values.json"))
    manifest = read_json(os.path.join(pack_dir, "manifest.json"))

    # -- manifest ----------------------------------------------------------
    for k in REQUIRED_MANIFEST:
        if k not in manifest:
            r.error(f"manifest is missing '{k}'")
    # load_geo_pack.py stamps every seeded row with this, so a pack without one
    # produces rows whose provenance cannot be told apart from a later reseed.
    if not any(k in manifest for k in ("version", "upstream_last_modified", "fetched_on")):
        r.error("manifest carries no version, upstream_last_modified or fetched_on")
    if manifest.get("synthetic") and "attribution" in str(manifest.get("license", "")).lower():
        r.warn("pack is marked synthetic but its license mentions attribution")

    # -- levels: one root, single chain, no cycles --------------------------
    by_id = {lv["level_id"]: lv for lv in levels}
    if len(by_id) != len(levels):
        r.error("duplicate level_id in levels.json")
    roots = [lv for lv in levels if not lv["parent_level_id"]]
    if len(roots) != 1:
        r.error(f"expected exactly 1 root level, found {len(roots)}")
    for lv in levels:
        p = lv["parent_level_id"]
        if p and p not in by_id:
            r.error(f"level {lv['level_id']} references unknown parent {p}")

    order, seen = [], set()
    cur = roots[0]["level_id"] if roots else None
    while cur:
        if cur in seen:
            r.error("cycle in the level hierarchy")
            break
        seen.add(cur)
        order.append(cur)
        kids = [lv["level_id"] for lv in levels if lv["parent_level_id"] == cur]
        if len(kids) > 1:
            r.error(f"level {cur} has {len(kids)} child levels — the hierarchy "
                    f"must be a single chain, not a tree")
            break
        cur = kids[0] if kids else None
    if len(order) != len(levels):
        r.error(f"{len(levels) - len(order)} level(s) are not reachable from the root")

    depth_of = {lid: d for d, lid in enumerate(order)}
    mnemonic = {lv["level_id"]: lv["level_mnemonic"] for lv in levels}
    if manifest.get("levels") and manifest["levels"] != [mnemonic[l] for l in order]:
        r.error(f"manifest levels {manifest.get('levels')} do not match levels.json "
                f"{[mnemonic[l] for l in order]}")

    # -- values: identity, parentage, P-code hierarchy ----------------------
    vals = {}
    for v in values:
        vid = v.get("level_value_id")
        if vid in vals:
            r.error(f"duplicate level_value_id {vid}")
        vals[vid] = v
        if v.get("level_id") not in by_id:
            r.error(f"unit {vid} is at unknown level {v.get('level_id')}")
        if v.get("pcode") and v["pcode"] != vid:
            r.error(f"unit {vid} has pcode {v['pcode']} — the pack contract is "
                    f"that the P-code IS the level_value_id")

    n_roots = 0
    for v in values:
        vid, parent = v.get("level_value_id"), v.get("parent_level_value_id")
        d = depth_of.get(v.get("level_id"))
        if parent is None:
            n_roots += 1
            if d not in (0, None):
                r.error(f"unit {vid} at depth {d} has no parent")
            continue
        if parent not in vals:
            r.error(f"unit {vid} references unknown parent {parent}")
            continue
        pd = depth_of.get(vals[parent].get("level_id"))
        if d is not None and pd is not None and pd != d - 1:
            r.error(f"unit {vid} (depth {d}) has a parent at depth {pd}")
        if not str(vid).startswith(str(parent)):
            r.error(f"P-code {vid} does not extend its parent {parent}")
    if n_roots != 1:
        r.error(f"expected exactly 1 root unit, found {n_roots}")

    counts = {}
    for v in values:
        counts[mnemonic.get(v.get("level_id"), "?")] = \
            counts.get(mnemonic.get(v.get("level_id"), "?"), 0) + 1
    if manifest.get("unit_counts") and manifest["unit_counts"] != counts:
        r.error(f"manifest unit_counts {manifest.get('unit_counts')} "
                f"do not match values.json {counts}")

    # -- boundaries --------------------------------------------------------
    bdir = os.path.join(pack_dir, "boundaries")
    if not os.path.isdir(bdir):
        r.warn("no boundaries/ directory — the pack seeds a hierarchy but draws no map")
        return r

    geoms = {}
    for lid in order:
        m = mnemonic[lid]
        path = os.path.join(bdir, f"{m}.geojson")
        if not os.path.exists(path):
            r.error(f"no boundaries/{m}.geojson for level {m}")
            continue
        fc = read_json(path)
        level_units = {v["level_value_id"] for v in values if v.get("level_id") == lid}
        seen_pcodes = set()
        for f in fc.get("features", []):
            pr = f.get("properties") or {}
            pcode = pr.get("pcode")
            geom = f.get("geometry") or {}
            if not pcode:
                r.error(f"{m}.geojson has a feature with no pcode")
                continue
            if pcode in seen_pcodes:
                r.error(f"{m}.geojson has two features for {pcode}")
            seen_pcodes.add(pcode)
            if pcode not in vals:
                r.error(f"{m}.geojson draws {pcode}, which is not in values.json")
                continue
            # The map surface reads `area_name`, never `name` — a property
            # called `name` collides with the read-only Function.name on
            # Evidence's callable input proxy and kills the map outright.
            if "name" in pr:
                r.error(f"{m}.geojson feature {pcode} carries a 'name' property; "
                        f"use 'area_name'")
            if not pr.get("area_name"):
                r.warn(f"{m}.geojson feature {pcode} has no area_name")
            if geom.get("type") not in ("Polygon", "MultiPolygon"):
                r.error(f"{pcode} has geometry type {geom.get('type')}")
                continue
            for ring in rings(geom):
                if len(ring) < 4:
                    r.error(f"{pcode} has a ring with {len(ring)} points")
                elif ring[0] != ring[-1]:
                    r.error(f"{pcode} has an unclosed ring")
            for ring in outer_rings(geom):
                if not ring_is_simple(ring):
                    r.error(f"{pcode} has a self-intersecting outer ring")
            geoms[pcode] = geom

        missing = level_units - seen_pcodes
        if missing:
            r.error(f"{len(missing)} {m} unit(s) have no geometry, e.g. "
                    f"{sorted(missing)[:3]}")

    # -- nesting and coverage ---------------------------------------------
    # The invariant drill-down depends on: a child lies inside its parent, and a
    # parent's children account for its area.
    escaped, checked = [], 0
    for v in values:
        parent = v.get("parent_level_value_id")
        vid = v["level_value_id"]
        if not parent or vid not in geoms or parent not in geoms:
            continue
        pts = sample_interior(geoms[vid])
        if not pts:
            continue
        checked += 1
        inside = sum(1 for p in pts if point_in_polygon(p, geoms[parent]))
        if inside < NESTING_THRESHOLD * len(pts):
            escaped.append((vid, parent, inside, len(pts)))
    if escaped:
        pct = 100 * len(escaped) / checked
        detail = ", ".join(f"{c} outside {p} ({i}/{n} inside)"
                           for c, p, i, n in escaped[:4])
        msg = f"{len(escaped)}/{checked} units ({pct:.1f}%) are not inside their parent: {detail}"
        # A handful of strays is simplification noise on a real pack; a large
        # share means the generator's nesting is broken.
        (r.error if pct > 2.0 else r.warn)(msg)

    kids_area = {}
    for v in values:
        parent = v.get("parent_level_value_id")
        if parent and v["level_value_id"] in geoms:
            kids_area[parent] = kids_area.get(parent, 0.0) + polygon_area(geoms[v["level_value_id"]])
    bad = []
    for parent, area in kids_area.items():
        if parent not in geoms:
            continue
        pa = polygon_area(geoms[parent])
        if pa <= 0:
            r.error(f"{parent} has zero area")
            continue
        ratio = area / pa
        if abs(ratio - 1.0) > AREA_TOLERANCE:
            bad.append((parent, ratio))
    if bad:
        worst = sorted(bad, key=lambda t: abs(t[1] - 1))[-3:]
        detail = ", ".join(f"{p} children cover {100 * rt:.0f}%" for p, rt in worst)
        pct = 100 * len(bad) / max(1, len(kids_area))
        msg = (f"{len(bad)}/{len(kids_area)} parents are not cleanly partitioned "
               f"by their children ({pct:.0f}%): {detail}")
        (r.error if pct > 25.0 else r.warn)(msg)

    return r


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("packs", nargs="+", help="pack directories to validate")
    p.add_argument("--strict", action="store_true",
                   help="treat warnings as failures")
    args = p.parse_args()

    failed = False
    for pack in args.packs:
        r = validate(pack)
        for w in r.warnings:
            print(f"[{r.name}] WARN  {w}")
        for e in r.errors:
            print(f"[{r.name}] ERROR {e}")
        bad = r.errors or (args.strict and r.warnings)
        print(f"[{r.name}] {'FAIL' if bad else 'ok'}"
              f"  ({len(r.errors)} errors, {len(r.warnings)} warnings)")
        failed = failed or bool(bad)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
