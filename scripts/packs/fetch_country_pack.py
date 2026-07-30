#!/usr/bin/env python3
"""Build a country geo pack from OCHA COD-AB.

A "country pack" is the single artifact that MDS is seeded from. It carries the
administrative hierarchy, official P-codes, and simplified boundary geometry —
everything the platform needs to know about a country's geography.

    python fetch_country_pack.py --country ETH \\
        --level-names region,zone,woreda --out ../../packs/ETH

Why COD-AB
----------
OCHA's Common Operational Datasets are the humanitarian standard: UN-published,
versioned per country, and explicitly *operational* rather than a legal or
political statement about borders — which is what makes them safe for a global
public good to adopt. Crucially each unit carries a hierarchical P-code
(ET -> ET01 -> ET0101 -> ET010101) with its parents embedded, so the hierarchy
falls out of the data instead of being reconstructed by name matching.

P-code IS the identifier
------------------------
The pack uses the P-code as `level_value_id` rather than minting a separate
opaque id alongside it. One identifier, stable across releases, meaningful to
anyone who works with humanitarian data, and already the natural join key for
analytics. Nothing downstream has to reconcile two id spaces.

Licensing
---------
COD-AB is CC BY-IGO — attribution required. The pack records the source, license
and version in manifest.json, and boundaries are fetched at build time rather
than committed, so nothing here redistributes the upstream files.

Output
------
    levels.json                 level definitions, root -> leaf
    values.json                 every unit: pcode, name, parent, level
    boundaries/<level>.geojson  simplified geometry, keyed by pcode
    manifest.json               source, license, version, counts, tolerances
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from datetime import date

from _geometry import count_points, simplify_geometry

HDX_API = "https://data.humdata.org/api/3/action/package_show?id=cod-ab-{iso3}"

# Simplification tolerance in degrees, per level. Deeper levels are drawn
# smaller on screen, so they tolerate more aggressive thinning; the top level
# is what people look at first and stays crispest. Roughly: 0.01 deg ~ 1 km.
DEFAULT_TOLERANCE = {0: 0.005, 1: 0.005, 2: 0.008, 3: 0.012, 4: 0.015}


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# COD-AB extraction
# ---------------------------------------------------------------------------
def download_cod(iso3, workdir):
    meta = fetch_json(HDX_API.format(iso3=iso3.lower()))
    if not meta.get("success"):
        raise SystemExit(f"no COD-AB dataset on HDX for {iso3}")
    res = meta["result"]
    geo = next((r for r in res.get("resources", []) if r.get("format") == "GeoJSON"), None)
    if not geo:
        raise SystemExit(f"COD-AB for {iso3} has no GeoJSON resource")

    print(f"[pack] {res.get('title')}")
    print(f"[pack] license: {res.get('license_title')}")
    zip_path = os.path.join(workdir, "cod.zip")
    print(f"[pack] downloading {geo['url'].split('/')[-1]} ...")
    with urllib.request.urlopen(geo["url"], timeout=600) as r, open(zip_path, "wb") as f:
        shutil.copyfileobj(r, f)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(workdir)
    return {
        "title": res.get("title"),
        "license": res.get("license_title"),
        "hdx_id": res.get("id"),
        "last_modified": res.get("last_modified") or geo.get("last_modified"),
    }


def find_level_files(workdir):
    """Locate eth_admin1.geojson / admin2 / ... whatever the country prefix is."""
    found = {}
    for root, _dirs, files in os.walk(workdir):
        for fn in files:
            if not fn.endswith(".geojson"):
                continue
            stem = fn[:-len(".geojson")].lower()
            for depth in range(0, 6):
                if stem.endswith(f"admin{depth}") or stem.endswith(f"adm{depth}"):
                    found[depth] = os.path.join(root, fn)
    return dict(sorted(found.items()))


def prop(props, *candidates):
    lower = {k.lower(): v for k, v in props.items()}
    for c in candidates:
        if c.lower() in lower and lower[c.lower()] not in (None, ""):
            return lower[c.lower()]
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--country", required=True, help="ISO3 code, e.g. ETH")
    p.add_argument("--out", required=True, help="pack output directory")
    p.add_argument("--level-names", default="",
                   help="comma-separated names below country, e.g. region,zone,woreda. "
                        "Defaults to adm1,adm2,... — purely cosmetic labels, the "
                        "hierarchy itself comes from the data.")
    p.add_argument("--max-depth", type=int, default=3,
                   help="deepest admin level to include (default 3)")
    args = p.parse_args()

    names = [n.strip() for n in args.level_names.split(",") if n.strip()]
    workdir = tempfile.mkdtemp(prefix="codab-")
    try:
        src = download_cod(args.country, workdir)
        level_files = find_level_files(workdir)
        level_files = {d: f for d, f in level_files.items() if d <= args.max_depth}
        if not level_files:
            raise SystemExit("no admN.geojson files found in the COD-AB bundle")

        os.makedirs(os.path.join(args.out, "boundaries"), exist_ok=True)

        levels, values, counts = [], [], {}
        for depth, path in level_files.items():
            mnemonic = ("country" if depth == 0
                        else names[depth - 1] if depth - 1 < len(names)
                        else f"adm{depth}")
            level_id = f"l{depth}"
            levels.append({
                "level_id": level_id,
                "level_mnemonic": mnemonic,
                "parent_level_id": f"l{depth - 1}" if depth else None,
            })

            data = json.load(open(path))
            tol = DEFAULT_TOLERANCE.get(depth, 0.01)
            feats, before, after = [], 0, 0
            for f in data["features"]:
                pr = f["properties"]
                pcode = prop(pr, f"adm{depth}_pcode", f"ADM{depth}_PCODE")
                name = prop(pr, f"adm{depth}_en", f"ADM{depth}_EN",
                            f"adm{depth}_name", "name")
                parent = (prop(pr, f"adm{depth-1}_pcode", f"ADM{depth-1}_PCODE")
                          if depth else None)
                if not pcode:
                    continue
                values.append({
                    # P-code IS the id — see module docstring.
                    "level_value_id": pcode,
                    "level_id": level_id,
                    "level_value_mnemonic": name or pcode,
                    "parent_level_value_id": parent,
                    "pcode": pcode,
                    "pcode_source": "OCHA COD-AB",
                    "display_name": name or pcode,
                })
                geom = f.get("geometry") or {}
                before += count_points(geom)
                simple = simplify_geometry(geom, tol)
                after += count_points(simple)
                feats.append({
                    "type": "Feature",
                    # Deliberately NOT "name": Evidence backs each map input
                    # with a callable proxy, and a field called `name` collides
                    # with the read-only Function.name — the map then dies with
                    # "Cannot assign to read only property 'name'".
                    "properties": {"pcode": pcode, "area_name": name or pcode,
                                   "parent_pcode": parent, "level": mnemonic},
                    "geometry": simple,
                })

            out_file = os.path.join(args.out, "boundaries", f"{mnemonic}.geojson")
            with open(out_file, "w") as fh:
                json.dump({"type": "FeatureCollection", "features": feats}, fh)
            size_mb = os.path.getsize(out_file) / 1e6
            counts[mnemonic] = len(feats)
            pct = (100 * after / before) if before else 100
            print(f"[pack] {mnemonic:<10} {len(feats):>5} units  "
                  f"{size_mb:6.2f} MB  vertices {before:>8,} -> {after:>7,} ({pct:.0f}%)")

        with open(os.path.join(args.out, "levels.json"), "w") as fh:
            json.dump(levels, fh, indent=2)
        with open(os.path.join(args.out, "values.json"), "w") as fh:
            json.dump(values, fh, indent=2)
        with open(os.path.join(args.out, "manifest.json"), "w") as fh:
            json.dump({
                "country": args.country,
                "synthetic": False,
                "source": "OCHA COD-AB via HDX",
                "source_title": src["title"],
                "license": src["license"],
                "license_note": "Attribution required. Boundaries are operational, "
                                "not a statement on legal or political status.",
                "upstream_last_modified": src["last_modified"],
                "fetched_on": date.today().isoformat(),
                # Every pack carries a `version`, whatever produced it, so
                # load_geo_pack.py stamps seeded rows from one field rather than
                # guessing per pack flavour.
                "version": src["last_modified"] or date.today().isoformat(),
                "identifier": "P-code used as level_value_id",
                "levels": [lv["level_mnemonic"] for lv in levels],
                "unit_counts": counts,
                "simplification_tolerance_deg": {
                    lv["level_mnemonic"]: DEFAULT_TOLERANCE.get(i, 0.01)
                    for i, lv in enumerate(levels)
                },
            }, fh, indent=2)
        print(f"[pack] wrote {len(values)} units to {args.out}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
