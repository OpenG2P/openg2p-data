"""Planar geometry helpers shared by the pack tools.

Deliberately pure Python — no shapely, no GEOS. The pack tools are the only
thing in this repo that touches geometry, they run occasionally on a developer
machine rather than in a hot loop, and a native dependency would make the
generators harder to run than the data is worth. Everything here works on
GeoJSON-shaped nested lists.

Rings follow the GeoJSON convention: a closed list of [lon, lat] pairs whose
first and last points are equal.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Douglas-Peucker simplification
# ---------------------------------------------------------------------------

# A unit is never thinned by more than this fraction of its own size, however
# generous the level tolerance is.
MAX_RELATIVE_TOLERANCE = 0.02


def _perp_distance(pt, start, end):
    (x, y), (x1, y1), (x2, y2) = pt, start, end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    px, py = x1 + t * dx, y1 + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def simplify_ring(ring, tol):
    """Douglas-Peucker, iterative so a 10k-vertex woreda can't blow the stack.

    A ring must keep at least 4 points (3 distinct + closing point) or it stops
    being a polygon and renderers silently drop it.
    """
    if len(ring) <= 4:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        worst, worst_i = tol, -1
        for i in range(lo + 1, hi):
            d = _perp_distance(ring[i], ring[lo], ring[hi])
            if d > worst:
                worst, worst_i = d, i
        if worst_i != -1:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    out = [p for p, k in zip(ring, keep) if k]
    if len(out) < 4:
        # Too aggressive for this ring — fall back to an evenly-spaced sample
        # rather than emitting a degenerate polygon.
        step = max(1, len(ring) // 4)
        out = ring[::step]
        if out[0] != out[-1]:
            out.append(out[0])
    return out


def geom_extent(geom):
    """Largest bbox dimension, in degrees."""
    pts = all_points(geom)
    if not pts:
        return 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def simplify_ring_valid(ring, tol, attempts=5):
    """Simplify without letting the ring fold through itself.

    Douglas-Peucker preserves shape but not validity: dropping vertices from a
    deeply convoluted boundary can pull one part of the ring across another. On
    Ethiopia's COD-AB that happened to 5 of 1,271 units, and a self-intersecting
    polygon does not fail loudly — it renders with the overlap filled or holed
    depending on the renderer's winding rule, so a choropleth quietly shows the
    wrong shape.

    Retrying with a progressively finer tolerance keeps most of the saving while
    guaranteeing what came out is drawable. A ring that is already
    self-intersecting upstream can never be made simple, so the original is
    returned unchanged and validate_pack.py reports it rather than this quietly
    looping.
    """
    for i in range(attempts):
        out = simplify_ring(ring, tol / (2 ** i))
        if ring_is_simple(out):
            return out
    return ring


def simplify_geometry(geom, tol):
    """Simplify with a tolerance capped relative to the feature's own extent.

    A flat tolerance in degrees treats a 200 km region and a 5 km urban woreda
    identically, so the small one gets flattened into a sliver — area error at
    the 90th percentile was 27% (worst case 85%) before this cap, which is
    plainly visible as wrong shapes on a choropleth.
    """
    eff = min(tol, geom_extent(geom) * MAX_RELATIVE_TOLERANCE) or tol
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Polygon":
        return {"type": t, "coordinates": [simplify_ring_valid(r, eff) for r in c]}
    if t == "MultiPolygon":
        return {"type": t,
                "coordinates": [[simplify_ring_valid(r, eff) for r in poly]
                                for poly in c]}
    return geom


# ---------------------------------------------------------------------------
# GeoJSON traversal
# ---------------------------------------------------------------------------
def rings(geom):
    """Every ring in a Polygon or MultiPolygon, outer rings and holes alike."""
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return list(c)
    if t == "MultiPolygon":
        return [r for poly in c for r in poly]
    return []


def outer_rings(geom):
    """Just the outer ring of each part — holes excluded."""
    t, c = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return [c[0]] if c else []
    if t == "MultiPolygon":
        return [poly[0] for poly in c if poly]
    return []


def all_points(geom):
    return [p for r in rings(geom) for p in r]


def count_points(geom):
    return sum(len(r) for r in rings(geom))


def bbox(geom):
    pts = all_points(geom)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Area, containment
# ---------------------------------------------------------------------------
def ring_area(ring):
    """Signed shoelace area. Positive for counter-clockwise rings."""
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def polygon_area(geom):
    """Unsigned area in square degrees, holes subtracted.

    Square degrees, not square kilometres — every use here is a ratio
    (does this child's area sum match its parent's), so the projection
    distortion cancels out and there is nothing to gain from converting.
    """
    t, c = geom.get("type"), geom.get("coordinates") or []
    total = 0.0
    parts = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    for poly in parts:
        for i, r in enumerate(poly):
            a = abs(ring_area(r))
            total += a if i == 0 else -a
    return total


def point_in_ring(pt, ring):
    """Ray casting. Points exactly on the boundary are undefined — callers here
    only ever test points that are meant to be comfortably inside or outside."""
    x, y = pt
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xint:
                inside = not inside
    return inside


def point_in_polygon(pt, geom):
    """Inside an outer ring and not inside any of its holes."""
    t, c = geom.get("type"), geom.get("coordinates") or []
    parts = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    for poly in parts:
        if not poly or not point_in_ring(pt, poly[0]):
            continue
        if any(point_in_ring(pt, h) for h in poly[1:]):
            continue
        return True
    return False


def centroid(ring):
    """Area-weighted centroid, falling back to the vertex mean for a
    degenerate (zero-area) ring."""
    a = ring_area(ring)
    if abs(a) < 1e-15:
        pts = ring[:-1] or ring
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    cx = cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6 * a), cy / (6 * a))


# ---------------------------------------------------------------------------
# Clipping
# ---------------------------------------------------------------------------
def clip_halfplane(ring, a, b, c):
    """Sutherland-Hodgman clip of `ring` to the half-plane a*x + b*y <= c.

    The subject ring may be concave; only the *clip* region has to be convex,
    and a half-plane always is. Successively clipping by several half-planes
    therefore yields an exact intersection with any convex region — which is
    what makes Voronoi cells usable as an exact partition here.

    A concave subject whose intersection with the half-plane would fall into
    two disconnected pieces comes back as one ring joined by a zero-width
    bridge along the cut. That is a rendering curiosity rather than a
    correctness problem (area and containment are unaffected), and the
    generator keeps parents close to convex so it stays rare.
    """
    if not ring:
        return []
    pts = ring[:-1] if ring[0] == ring[-1] else ring[:]
    if not pts:
        return []
    out = []
    n = len(pts)
    for i in range(n):
        cur, nxt = pts[i], pts[(i + 1) % n]
        dc = a * cur[0] + b * cur[1] - c
        dn = a * nxt[0] + b * nxt[1] - c
        if dc <= 0:
            out.append(cur)
        if (dc <= 0) != (dn <= 0):
            t = dc / (dc - dn)
            out.append((cur[0] + t * (nxt[0] - cur[0]),
                        cur[1] + t * (nxt[1] - cur[1])))
    if len(out) < 3:
        return []
    out.append(out[0])
    return out


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------
def _seg_intersect(p1, p2, p3, p4):
    """Proper crossing of two open segments. Shared endpoints and collinear
    touching do not count — adjacent ring edges always share an endpoint."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-14 else (1 if v > 0 else -1)

    o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
    o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4 and 0 not in (o1, o2, o3, o4)


def ring_is_simple(ring):
    """True when no two non-adjacent edges of the ring cross.

    O(n^2), which is fine: the rings this is called on have tens of vertices,
    not thousands.
    """
    pts = ring[:-1] if ring and ring[0] == ring[-1] else ring
    n = len(pts)
    if n < 4:
        return True
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue
            if _seg_intersect(a1, a2, pts[j], pts[(j + 1) % n]):
                return False
    return True


def dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])
