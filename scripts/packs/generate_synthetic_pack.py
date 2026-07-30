#!/usr/bin/env python3
"""Generate a synthetic country pack — a fictitious country, shaped like a real one.

    python generate_synthetic_pack.py --preset kamuntu --out ../../packs/XKM

Why a fictitious country
------------------------
Demos and default installs need geography that looks and behaves like a real
country without *being* one. Shipping a real country's boundaries as the default
demo dataset puts a real place's name against invented poverty figures, and
relabelling that country's outline does not fix it — the silhouette is the
identification. So the default pack is a country that does not exist: invented
boundaries, invented names, invented P-codes, no attribution owed to anyone.

Real country packs remain first-class (see fetch_country_pack.py) — they are
just no longer the thing an operator gets by accident.

Codes that can never collide with a real country
------------------------------------------------
ISO 3166 reserves ranges for private use: XA-XZ for alpha-2 and XAA-XZZ for
alpha-3. Kamuntu takes XKM / XK from those ranges, so its codes are guaranteed
never to clash with a country ISO assigns later, and the leading X is a standing
signal that the place is not real. P-codes nest exactly as COD-AB's do
(XK -> XK01 -> XK0101 -> ...), two digits per level, so nothing downstream can
tell a synthetic pack from a real one structurally.

How the geometry is built
-------------------------
Not a grid of rectangles. Rectangles read as a placeholder, and a placeholder
map teaches an administrator nothing about whether drill-down works.

1. The national outline is a lopsided skeleton plus fractal midpoint
   displacement, which gives it real bays and peninsulas. A radial curve was
   tried first and always came out convex — every bay is limited to what stays
   visible from the centre — so the country read as a potato.
2. Each level partitions its parent by Voronoi cells around relaxed random
   sites, clipped to the parent polygon. Because clipping only ever *removes*
   area from the parent, children nest inside their parent exactly, and their
   areas sum back to it.
3. Internal borders are then perturbed into wandering polylines. Both sides of a
   shared border get the identical polyline, so the partition stays exact while
   the borders stop looking machine-drawn.

Names come from the country's existing place names first (kamuntu_names.json),
topped up in the same phonology, so the pack inherits an identity rather than
inventing a second one.

The invariant that matters for drill-down — every child strictly inside its
parent, siblings covering it without gaps or overlaps — is checked by
validate_pack.py, not merely intended here.

Deterministic
-------------
Same --seed and --version produce byte-identical output, so a regenerated pack
shows an empty diff and a real change is visible in review.

Output is the same contract as any other pack:
    levels.json                 level definitions, root -> leaf
    values.json                 every unit: pcode, name, parent, level
    boundaries/<level>.geojson  geometry, keyed by pcode
    manifest.json               provenance, license, counts
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from datetime import date

from _geometry import (
    centroid,
    clip_halfplane,
    dist,
    point_in_ring,
    polygon_area,
    ring_area,
    ring_is_simple,
)

# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------
# Unit counts are chosen so the *default* demo reads honestly at every level.
# A percentage needs a decent denominator: with the shipped sample size a
# village here holds roughly a hundred households, where a single household
# moves a rate by ~1 point. Mirroring a real country's granularity instead
# (Ethiopia has 1,148 woredas) leaves about a dozen households per unit, where
# one household moves a rate by 8 points and every leaf-level percentage has to
# be suppressed as unreliable. The synthetic pack is free of that constraint,
# so it is sized to be reportable all the way down.
PRESETS = {
    "kamuntu": {
        "alpha3": "XKM",
        "alpha2": "XK",
        "country_name": "Kamuntu",
        # Level names below country. Deliberately a different depth and
        # different vocabulary from the Ethiopia pack — having two packs whose
        # hierarchies do not match is what keeps the platform honest about
        # being depth- and name-agnostic rather than accidentally hardcoded.
        "level_names": ["region", "district", "ward", "village"],
        # (min, max) children per parent, per level. Ranges rather than a fixed
        # fan-out: real hierarchies are lumpy, and the uniform 4x4x4x4 tree the
        # legacy CSV used made every "largest/smallest unit" chart come out
        # suspiciously flat. Nine regions rather than that CSV's four, because a
        # four-area choropleth is not worth drilling into.
        "fanout": [(9, 9), (3, 6), (3, 5), (3, 5)],
        # Kamuntu's existing place names, kept so the pack inherits the identity
        # the country already had in this repo. Topped up by the generator when
        # the pool runs out — see kamuntu_names.json.
        "name_pool": "kamuntu_names.json",
        # South Atlantic — open ocean. If a basemap is ever shown behind the
        # map, a landmass here is unmistakably not a real place rather than
        # quietly overlapping someone's territory.
        "centre": (-22.0, -16.0),
        "radius": 4.6,
        "aspect": 1.15,
        # Matched to the existing names (Kilima, Chakula, Mkutani, Baraka): open
        # CV syllables only, with the prenasalised onsets that give them their
        # character. No codas — a coda-bearing inventory produced "Wukwingnkan",
        # four consonants deep, which reads as a password rather than a place and
        # would not sit beside the names already in the pool.
        "phonology": {
            "onsets": ["b", "ch", "d", "f", "g", "h", "j", "k", "l", "m", "n",
                       "p", "r", "s", "t", "v", "w", "y", "z",
                       "mb", "nd", "ng", "ny", "nj", "mw", "sh", "k", "m", "n"],
            "vowels": ["a", "e", "i", "o", "u", "a", "i", "a"],
        },
    },
}


# ---------------------------------------------------------------------------
# national outline
# ---------------------------------------------------------------------------
def _skeleton(centre, radius, aspect, rng, lobes):
    """A coarse, deliberately lopsided polygon — the country's basic massing.

    Radii swing widely and then get one smoothing pass, which correlates
    neighbours enough to read as broad peninsulas and gulfs rather than as
    spikes. Wide swings are the point: a radial curve with modest harmonics
    comes out convex, and a convex country looks like a potato.
    """
    raw = [rng.uniform(0.52, 1.30) for _ in range(lobes)]
    r = [0.5 * raw[i] + 0.25 * (raw[i - 1] + raw[(i + 1) % lobes])
         for i in range(lobes)]
    cx, cy = centre
    pts = []
    for i in range(lobes):
        # Jitter the spacing too, so lobes are not evenly distributed.
        th = 2 * math.pi * (i + rng.uniform(-0.28, 0.28)) / lobes
        pts.append((cx + radius * aspect * r[i] * math.cos(th),
                    cy + radius * r[i] * math.sin(th)))
    pts.append(pts[0])
    return pts


def _displace(ring, rng, roughness, depth):
    """Fractal midpoint displacement — the coastline detail.

    Each pass inserts a midpoint on every edge and pushes it sideways, with the
    displacement shrinking geometrically per pass. This is what produces inlets
    and headlands at several scales, the thing a smooth radial curve cannot do
    because every point of it is visible from the centre.
    """
    for level in range(depth):
        amp = roughness * (0.55 ** level)
        out = []
        for i in range(len(ring) - 1):
            p, q = ring[i], ring[i + 1]
            dx, dy = q[0] - p[0], q[1] - p[1]
            length = math.hypot(dx, dy)
            out.append(p)
            if length == 0:
                continue
            nx, ny = -dy / length, dx / length
            off = rng.gauss(0, amp) * length
            out.append(((p[0] + q[0]) / 2 + nx * off,
                        (p[1] + q[1]) / 2 + ny * off))
        out.append(out[0])
        ring = out
    return ring


def country_outline(centre, radius, aspect, rng, lobes=9, depth=4, roughness=0.26):
    """An irregular closed coastline with genuine bays and peninsulas.

    Built as a lopsided skeleton plus fractal detail rather than as a radial
    curve, because a radial curve is star-shaped by construction — every bay is
    limited to what stays visible from the centre, and the result always reads
    as a blob. This gives real concavity.

    The price of concavity is that validity is no longer free, so a candidate
    that folds through itself is simply redrawn. Ten attempts is plenty in
    practice; if none is simple the roughness is wrong and failing loudly beats
    shipping a country whose coastline crosses itself, since every level below
    is clipped against it.
    """
    for attempt in range(10):
        ring = _displace(_skeleton(centre, radius, aspect, rng, lobes),
                         rng, roughness * (0.85 ** attempt), depth)
        if ring_is_simple(ring):
            # Counter-clockwise, matching the GeoJSON right-hand rule.
            return ring if ring_area(ring) > 0 else ring[::-1]
    raise SystemExit("could not draw a simple national outline — lower --roughness")


# ---------------------------------------------------------------------------
# partitioning
# ---------------------------------------------------------------------------
def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def sample_sites(ring, n, rng, relax_iters=2):
    """n well-spread points inside `ring`.

    Rejection-sample, then Lloyd-relax against a grid of interior points.
    Relaxation is stopped early on purpose: run to convergence it equalises the
    cells into a honeycomb, and real administrative units are not equal-sized.
    Two passes is enough to remove slivers while leaving the areas varied.
    """
    x0, y0, x1, y1 = _ring_bbox(ring)
    sites = []
    guard = 0
    while len(sites) < n and guard < n * 4000:
        guard += 1
        p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        if point_in_ring(p, ring):
            sites.append(p)
    if len(sites) < n:
        raise SystemExit(f"could not place {n} sites inside a polygon "
                         f"(placed {len(sites)}) — parent is too thin")

    # Interior grid used as a stand-in for integrating over the polygon.
    steps = max(12, int(math.sqrt(n) * 9))
    grid = [(x0 + (i + 0.5) * (x1 - x0) / steps, y0 + (j + 0.5) * (y1 - y0) / steps)
            for i in range(steps) for j in range(steps)]
    grid = [p for p in grid if point_in_ring(p, ring)]

    for _ in range(relax_iters):
        buckets = [[] for _ in sites]
        for g in grid:
            best, bi = None, 0
            for i, s in enumerate(sites):
                d = (g[0] - s[0]) ** 2 + (g[1] - s[1]) ** 2
                if best is None or d < best:
                    best, bi = d, i
            buckets[bi].append(g)
        for i, b in enumerate(buckets):
            if b:
                sites[i] = (sum(p[0] for p in b) / len(b),
                            sum(p[1] for p in b) / len(b))
    return sites


def voronoi_cells(parent_ring, sites):
    """Clip the parent by each site's Voronoi region.

    Every cell is the parent minus a set of half-planes, so cells are subsets of
    the parent by construction and together they tile it exactly. That is the
    whole reason for choosing Voronoi over any prettier scheme: nesting is a
    property of the method, not something to test for afterwards.
    """
    cells = []
    for i, si in enumerate(sites):
        ring = parent_ring
        for j, sj in enumerate(sites):
            if i == j:
                continue
            # Perpendicular bisector, keeping the side closer to si.
            a = 2 * (sj[0] - si[0])
            b = 2 * (sj[1] - si[1])
            c = (sj[0] ** 2 + sj[1] ** 2) - (si[0] ** 2 + si[1] ** 2)
            ring = clip_halfplane(ring, a, b, c)
            if not ring:
                break
        cells.append(ring)
    return cells


# ---------------------------------------------------------------------------
# organic borders
# ---------------------------------------------------------------------------
def _key(p, q):
    """Direction-independent key for an edge, so the two cells that share a
    border look up the same perturbation."""
    a = (round(p[0], 9), round(p[1], 9))
    b = (round(q[0], 9), round(q[1], 9))
    return (a, b) if a <= b else (b, a)


def _wiggle(p, q, rng, amp, parent_ring, segments=4):
    """Replace the straight edge p->q with a wandering polyline from p to q.

    Interior points only are moved, and only perpendicular to the edge, so the
    endpoints stay exactly where the clip put them — that is what keeps
    neighbouring cells welded together. Each candidate point is tested against
    the parent ring and pulled back if it would escape, so an organic border can
    never breach the boundary it lives inside.
    """
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return [p, q]
    nx, ny = -dy / length, dx / length
    out = [p]
    for i in range(1, segments):
        t = i / segments
        # Taper towards the endpoints: a bulge in the middle of a border looks
        # natural, a kink right at a junction does not.
        taper = math.sin(math.pi * t)
        off = rng.uniform(-amp, amp) * taper * length
        base = (p[0] + t * dx, p[1] + t * dy)
        cand = (base[0] + nx * off, base[1] + ny * off)
        while off and not point_in_ring(cand, parent_ring):
            off *= 0.5
            cand = (base[0] + nx * off, base[1] + ny * off)
            if abs(off) < 1e-9:
                cand = base
                break
        out.append(cand)
    out.append(q)
    return out


def organify(cells, parent_ring, rng, amp=0.10):
    """Turn the straight Voronoi borders between siblings into natural ones.

    Only *internal* borders are touched. An edge that is not shared with a
    sibling is part of the parent's own boundary and must be left alone, or the
    child would stop tracing its parent.

    A cell whose ring self-intersects after perturbation is reverted to its
    straight form rather than shipped broken — cheap insurance for the thin
    cells where a wandering border has no room to wander.
    """
    shared = {}
    for ring in cells:
        for i in range(len(ring) - 1):
            k = _key(ring[i], ring[i + 1])
            shared[k] = shared.get(k, 0) + 1

    # One deterministic polyline per shared edge, generated once from the
    # canonical endpoint order and reused by both owners, so the two cells
    # cannot drift apart no matter which direction each traverses it.
    paths = {}
    out = []
    for ring in cells:
        new = []
        for i in range(len(ring) - 1):
            p, q = ring[i], ring[i + 1]
            k = _key(p, q)
            if shared.get(k, 0) < 2:
                new.append(p)
                continue
            if k not in paths:
                paths[k] = _wiggle(k[0], k[1], rng, amp, parent_ring)
            path = paths[k]
            forward = (round(p[0], 9), round(p[1], 9)) == k[0]
            # Each edge contributes its points from p up to but excluding q —
            # q arrives as the next edge's p, and the ring is closed at the end.
            walk = path if forward else path[::-1]
            new.extend(walk[:-1])
        new.append(new[0])
        out.append(new if ring_is_simple(new) else ring)
    return out


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------
class NameMinter:
    """Place names: the country's existing ones first, then more in the same style.

    Reusing the real pool matters for continuity — Kamuntu's regions have been
    Kilima, Chakula, Jasiri and Faraja in this repo since before packs existed,
    and a demo country that silently renames itself is a demo country nobody
    recognises. The pool is drawn per level, so the recognisable upper tiers keep
    their own names rather than being handed village names.

    Beyond the pool, names are minted from one syllable inventory for the whole
    country, so an invented name sits beside a pooled one without standing out.
    That coherence is what makes a fictitious map read as a place rather than as
    random strings.
    """

    def __init__(self, phonology, rng, pool=None):
        self.p = phonology
        self.rng = rng
        self.used = set()
        self.simple = [o for o in phonology["onsets"] if len(o) == 1]
        self.pool = {k: list(v) for k, v in (pool or {}).items()}
        self.from_pool = 0

    def _word(self, syllables):
        """Build a word, forbidding a consonant cluster straight after a coda.

        Without that rule the inventory happily produces "Wukwingnkan" — four
        consonants in a row, which reads as a password rather than a place and
        is unpronounceable to whoever is demoing.
        """
        codas = self.p.get("codas") or [""]
        out, prev_coda = [], ""
        for _ in range(syllables):
            onsets = self.simple if prev_coda else self.p["onsets"]
            coda = self.rng.choice(codas)
            out.append(self.rng.choice(onsets) + self.rng.choice(self.p["vowels"]) + coda)
            prev_coda = coda
        return "".join(out)

    def mint(self, level):
        while self.pool.get(level):
            name = self.pool[level].pop(0)
            if name not in self.used:
                self.used.add(name)
                self.from_pool += 1
                return name
        for _ in range(10000):
            name = self._word(self.rng.choice([2, 2, 3]))
            name = name[0].upper() + name[1:]
            # Long names get truncated in map labels and table columns, so they
            # are rejected here rather than dealt with at every display site.
            if 4 <= len(name) <= 11 and name not in self.used:
                self.used.add(name)
                return name
        raise SystemExit("ran out of distinct names — widen the phonology")


# ---------------------------------------------------------------------------
# pack assembly
# ---------------------------------------------------------------------------
def load_name_pool(preset):
    """Per-level name pools, read from beside this script so a regenerated pack
    is reproducible even after the legacy geo.csv it came from is retired."""
    fn = preset.get("name_pool")
    if not fn:
        return {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fn)
    if not os.path.exists(path):
        print(f"[pack] NOTE: {fn} not found — all names will be minted")
        return {}
    with open(path) as fh:
        return json.load(fh).get("levels", {})


def build(preset, seed):
    rng = random.Random(seed)
    names = NameMinter(preset["phonology"], random.Random(seed ^ 0x5EED),
                       pool=load_name_pool(preset))
    a2, a3 = preset["alpha2"], preset["alpha3"]
    level_names = ["country"] + preset["level_names"]

    levels = [{
        "level_id": f"l{d}",
        "level_mnemonic": name,
        "parent_level_id": f"l{d - 1}" if d else None,
    } for d, name in enumerate(level_names)]

    outline = country_outline(preset["centre"], preset["radius"],
                              preset["aspect"], rng)

    values = [{
        "level_value_id": a2,
        "level_id": "l0",
        "level_value_mnemonic": preset["country_name"],
        "parent_level_value_id": None,
        "pcode": a2,
        "pcode_source": "synthetic",
        "display_name": preset["country_name"],
    }]
    geometry = {"country": {a2: outline}}

    parents = {a2: outline}
    for depth, mnemonic in enumerate(preset["level_names"], start=1):
        lo, hi = preset["fanout"][depth - 1]
        this_level, geoms = {}, {}
        for parent_pcode, parent_ring in sorted(parents.items()):
            n = rng.randint(lo, hi)
            sites = sample_sites(parent_ring, n, rng)
            cells = voronoi_cells(parent_ring, sites)
            # Order children by position rather than by draw order, so pcode 01
            # is consistently the westernmost child. Codes then read as a map
            # instead of as a shuffle.
            cells = [c for c in cells if c]
            cells.sort(key=lambda c: centroid(c))
            cells = organify(cells, parent_ring, rng)
            for i, cell in enumerate(cells, start=1):
                pcode = f"{parent_pcode}{i:02d}"
                name = names.mint(mnemonic)
                values.append({
                    "level_value_id": pcode,
                    "level_id": f"l{depth}",
                    "level_value_mnemonic": name,
                    "parent_level_value_id": parent_pcode,
                    "pcode": pcode,
                    "pcode_source": "synthetic",
                    "display_name": name,
                })
                this_level[pcode] = cell
                geoms[pcode] = cell
        geometry[mnemonic] = geoms
        parents = this_level

    return levels, values, geometry


def write_pack(out, preset, levels, values, geometry, seed, version):
    os.makedirs(os.path.join(out, "boundaries"), exist_ok=True)
    by_pcode = {v["pcode"]: v for v in values}
    level_of = {lv["level_id"]: lv["level_mnemonic"] for lv in levels}
    counts = {}

    for mnemonic, geoms in geometry.items():
        feats = []
        for pcode, ring in sorted(geoms.items()):
            v = by_pcode[pcode]
            feats.append({
                "type": "Feature",
                # Deliberately NOT "name": Evidence backs each map input with a
                # callable proxy, and a field called `name` collides with the
                # read-only Function.name — the map then dies with
                # "Cannot assign to read only property 'name'".
                "properties": {
                    "pcode": pcode,
                    "area_name": v["display_name"],
                    "parent_pcode": v["parent_level_value_id"],
                    "level": mnemonic,
                },
                "geometry": {"type": "Polygon",
                             "coordinates": [[list(p) for p in ring]]},
            })
        path = os.path.join(out, "boundaries", f"{mnemonic}.geojson")
        with open(path, "w") as fh:
            json.dump({"type": "FeatureCollection", "features": feats}, fh)
        counts[mnemonic] = len(feats)
        print(f"[pack] {mnemonic:<10} {len(feats):>5} units  "
              f"{os.path.getsize(path) / 1e6:6.2f} MB")

    with open(os.path.join(out, "levels.json"), "w") as fh:
        json.dump(levels, fh, indent=2)
    with open(os.path.join(out, "values.json"), "w") as fh:
        json.dump(values, fh, indent=2)
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump({
            "country": preset["alpha3"],
            "country_name": preset["country_name"],
            "synthetic": True,
            "source": "Synthetic — openg2p-data/scripts/packs/generate_synthetic_pack.py",
            "source_title": f"{preset['country_name']} — synthetic country pack",
            "license": "CC0-1.0",
            "license_note": (
                f"{preset['country_name']} is a fictitious country. Its boundaries, "
                "place names and P-codes are invented and describe no real place, so "
                "no attribution is owed and nothing here states a position on any "
                "real border. Codes are drawn from the ISO 3166 user-assigned ranges "
                "(XAA-XZZ, XA-XZ) and cannot collide with a real country."
            ),
            "version": version,
            "generated_by": os.path.basename(__file__),
            "seed": seed,
            "identifier": "P-code used as level_value_id",
            "levels": [lv["level_mnemonic"] for lv in levels],
            "unit_counts": counts,
        }, fh, indent=2)
    print(f"[pack] wrote {len(values)} units to {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--preset", default="kamuntu", choices=sorted(PRESETS))
    p.add_argument("--out", required=True, help="pack output directory")
    p.add_argument("--seed", type=int, default=20260730,
                   help="changing this redraws the whole country")
    p.add_argument("--version", default=None,
                   help="pack version recorded in the manifest (default: today). "
                        "Pinned explicitly when regenerating so an unchanged pack "
                        "produces an empty diff.")
    args = p.parse_args()

    preset = PRESETS[args.preset]
    version = args.version or date.today().isoformat()
    print(f"[pack] {preset['country_name']} ({preset['alpha3']}) — synthetic, seed {args.seed}")

    levels, values, geometry = build(preset, args.seed)
    write_pack(args.out, preset, levels, values, geometry, args.seed, version)

    country_area = polygon_area({"type": "Polygon",
                                 "coordinates": [geometry["country"][preset["alpha2"]]]})
    for mnemonic in list(geometry)[1:]:
        tot = sum(abs(ring_area(r)) for r in geometry[mnemonic].values())
        print(f"[pack] {mnemonic:<10} area coverage {100 * tot / country_area:6.2f}% of country")


if __name__ == "__main__":
    main()
