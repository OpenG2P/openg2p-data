#!/usr/bin/env python3
"""Generate a large, realistic NSR sample at the canonical schema and bulk-load it.

Why this exists alongside load_sample_data.py
---------------------------------------------
`load_sample_data.py` ingests a hand-enumerated ~500-row demo set from
`seed-data/*.json` — every row, including its ids, is written out by hand. That
is the right shape for a smoke-test fixture but cannot scale. This script
generates at arbitrary scale (default 1,000,000 individuals) and loads with
COPY instead of row batches.

Two things it deliberately does NOT do
--------------------------------------
1. It does not invent a geography. Places are read from the deployment's own
   Master Data geo hierarchy (`g2p_geo_levels` / `g2p_geo_level_values`), so
   nothing is hardcoded to a country, a set of level names, or a depth — and
   drill-down keeps working because every generated record points at a geo node
   that MDS actually knows about.

2. It does not hardcode the register columns. Each table's column list is read
   from `information_schema` at run time and intersected with the fields this
   script knows how to produce. A column the local schema doesn't have is
   skipped rather than crashing the load. (NSR's schema has drifted before:
   older deployments carry `household_size_total` where current ones carry
   `size_total`, and several wide columns have since been normalised into
   sub-tables.)

Attribute distributions come from `--distributions` (see
`extract_nsr_distributions.sql`), sampled off a real 20M-row registry, so marginals
like gender split, age structure, livelihood mix and disability prevalence match
observed reality rather than uniform noise.

Usage
-----
    export PGHOST=localhost PGPORT=5434 PGUSER=postgres PGPASSWORD=...
    python generate_nsr_bulk_sample.py \
        --db nsr --geo-db master_data \
        --individuals 1000000 \
        --distributions distributions.json

    # rehearse without writing anything
    python generate_nsr_bulk_sample.py --individuals 1000 --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
import uuid
from datetime import date, datetime, timedelta

import psycopg2

SEEDER = "bulk-seeder"
CREATED_AT = "2026-04-01 00:00:00"
ACTIVE = "ACTIVE"

# Average household size. 20M individuals over 5M households in the reference
# registry, so households = individuals / 4.
MEMBERS_PER_HOUSEHOLD = 4

# Share of household heads that are male. Female-headed households are a common
# targeting criterion, so this wants to land near the ~1-in-3 seen in practice
# rather than the ~50% a blind draw produces.
HEAD_MALE_SHARE = 0.66
# Male share among everyone else, chosen so that heads plus non-heads together
# reproduce the reference registry's overall split at ~4 members per household.
NON_HEAD_MALE_SHARE = 0.45

# Rows per COPY chunk. Large enough to amortise round-trips, small enough that
# the buffer stays well clear of memory pressure at 1M+ rows.
CHUNK = 50_000


# --------------------------------------------------------------------------
# distributions
# --------------------------------------------------------------------------
class Weighted:
    """Weighted sampler over (value, count) pairs from the distributions file.

    Nulls in the source are dropped: a real registry has missing values, but
    reproducing that here would just hide bugs in the dashboards we're seeding
    data *for*. Callers add missingness explicitly where it's meaningful.
    """

    def __init__(self, rows, key, rng):
        pairs = []
        for r in rows or []:
            value = r.get(key)
            count = r.get("c") or 0
            if value is None or count <= 0:
                continue
            pairs.append((value, count))
        if not pairs:
            raise ValueError(f"no usable values for '{key}' in distributions")
        self._values = [p[0] for p in pairs]
        self._weights = [p[1] for p in pairs]
        self._rng = rng

    def pick(self):
        return self._rng.choices(self._values, weights=self._weights, k=1)[0]


def load_distributions(path, rng):
    with open(path) as fh:
        raw = json.load(fh)
    return {
        "gender": Weighted(raw.get("gender"), "gender", rng),
        "age_band": Weighted(raw.get("age_band"), "age_band", rng),
        "livelihood": Weighted(raw.get("livelihood"), "primary_livelihood", rng),
        "education": Weighted(raw.get("education"), "educational_status", rng),
        "employment": Weighted(raw.get("employment"), "employment_status", rng),
        "disability": Weighted(raw.get("disability"), "disability_status", rng),
        "_raw": raw,
    }


# Age band -> (min, max). The reference registry stores bands, not ages, so we
# draw a concrete age uniformly inside the sampled band.
AGE_BANDS = {
    "Under 5": (0, 4),
    "School age (5-17)": (5, 17),
    "Working age (18-64)": (18, 64),
    "Elderly (65+)": (65, 92),
}

# Housing / WASH options, ordered worst -> best. Deprivation is correlated with
# the household's poverty score rather than drawn independently, so the seeded
# data supports the "are the poorest also the worst-served?" questions a social
# protection dashboard is built to answer.
HOUSING = {
    "dwelling_type": ["TRADITIONAL_HUT", "SEMI_PERMANENT", "PERMANENT", "APARTMENT"],
    "roof_material": ["THATCH", "IRON_SHEET", "TILE", "CONCRETE"],
    "wall_material": ["MUD", "WOOD", "BRICK", "CONCRETE"],
    "floor_material": ["EARTH", "WOOD", "CEMENT", "TILE"],
    # The four service ladders are enum-backed (WaterSourceTypeEnum,
    # SanitationTypeEnum, LightingSourceEnum, CookingFuelEnum), so every rung has
    # to be a real member. They previously read UNPROTECTED_WELL, PIPED_DWELLING,
    # OPEN_DEFECATION, IMPROVED_LATRINE, GRID_ELECTRICITY and LPG — none of which
    # exist in those enums. The reporting views matched these invented names, so
    # the dashboards looked right while the rows themselves were invalid: the API
    # rejects them on read and the attribute metadata has no matching value_id.
    #
    # Order still runs worst -> best, which is what makes the deprivation ladder
    # correlate with poverty score.
    "water_source_type": ["SURFACE_WATER", "WELL", "PUBLIC_TAP", "PIPED"],
    "sanitation_type": ["OPEN", "PIT_LATRINE", "COMPOSTING_TOILET", "FLUSH_TOILET"],
    "lighting_source": ["NONE", "KEROSENE", "SOLAR", "GRID"],
    "cooking_fuel_type": ["FIREWOOD", "CHARCOAL", "GAS", "ELECTRICITY"],
}
# Enum-backed columns. Every list below must hold members of the matching enum in
# nsr-extension/.../register_domain/models/enums.py.
#
# These columns are plain String, not native PG enums, so an invalid value INSERTs
# without complaint and only bites later: the API rejects the row on read, the
# attribute metadata has no matching value_id, and dashboards group by a category
# that does not exist. Name and weight are paired throughout so the two cannot
# drift out of step.
TENURE = [("OWNED", 62), ("RENTED", 18), ("HOSTED", 12), ("TEMPORARY", 8)]
HEADSHIP = ["MALE_HEADED", "FEMALE_HEADED", "CHILD_HEADED", "ELDERLY_HEADED"]
# Must be members of the extension's ProgramEnum
# (nsr-extension/.../register_domain/models/enums.py), which in turn matches the
# PROGRAM_NAME value_id rows in g2p_attribute_values.
#
# program_name is a plain String column, not a native PG enum, so an invalid
# value INSERTs happily and only surfaces later — the API rejects the row on
# read, the attribute metadata has no matching entry, and the dashboards' program
# breakdown shows a programme that does not exist. This list previously carried
# URBAN_PSNP and DIRECT_SUPPORT, which G2P-5412's enum rework dropped.
#
# Name and weight are paired so the two cannot drift: they used to be a list plus
# a separate weights=[...] literal that had to stay the same length, and adding a
# programme raised inside rng.choices.
PROGRAMS = [
    ("PROG_CASH_TRANSFER", 30),
    ("PROG_FOOD_SUPPORT", 20),
    ("PROG_SCHOOL_FEEDING", 14),
    ("PROG_PUBLIC_WORKS", 12),
    ("PROG_ELDERLY_PENSION", 8),
    ("PROG_DISABILITY_ALLOWANCE", 6),
    ("PROG_HEALTH_INSURANCE", 5),
    ("UPSNP", 3),
    ("RPSNP", 2),
]


def names_and_weights(pairs):
    return [n for n, _ in pairs], [w for _, w in pairs]


PROGRAM_NAMES, PROGRAM_WEIGHTS = names_and_weights(PROGRAMS)
TENURE_NAMES, TENURE_WEIGHTS = names_and_weights(TENURE)
DISPLACEMENT_NAMES, DISPLACEMENT_WEIGHTS = names_and_weights(DISPLACEMENT)
PASTORALIST_NAMES, PASTORALIST_WEIGHTS = names_and_weights(PASTORALIST)
DISPLACEMENT = [("HOST_COMMUNITY", 88), ("IDP", 7), ("RETURNEE", 3), ("REFUGEE", 2)]
PASTORALIST = [("SETTLED", 80), ("SEMI_PASTORALIST", 13), ("PASTORALIST", 7)]
# SELF, not HEAD — RelationshipToHeadEnum names the head's own row SELF.
RELATIONSHIPS = ["SELF", "SPOUSE", "CHILD", "PARENT", "SIBLING", "OTHER_RELATIVE"]
MARITAL = ["SINGLE", "MARRIED", "WIDOWED", "DIVORCED", "SEPARATED"]


def deprived_pick(options, poverty, rng):
    """Pick from a worst->best ordered list, skewed by `poverty` in [0,1].

    poverty 1.0 leans hard to the worst option, 0.0 to the best. The jitter
    keeps every level populated so charts don't show artificial gaps.
    """
    span = len(options) - 1
    target = (1.0 - poverty) * span
    idx = int(round(rng.gauss(target, 0.8)))
    return options[max(0, min(span, idx))]


# --------------------------------------------------------------------------
# geo, from the deployment's own MDS
# --------------------------------------------------------------------------
def load_geo(conn, rng):
    """Return (leaves, levels) where each leaf carries its full ancestor chain.

    Level names, depth and id format all come from MDS. Leaves are given
    lognormal weights so some places are much more populous than others — a
    uniform spread makes every choropleth look flat and hides real problems.
    """
    with conn.cursor() as cur:
        cur.execute("select level_id, level_mnemonic, parent_level_id from g2p_geo_levels")
        levels = {r[0]: {"mnemonic": r[1], "parent": r[2]} for r in cur.fetchall()}
        cur.execute(
            "select level_id, level_value_id, level_value_mnemonic, parent_level_value_id"
            " from g2p_geo_level_values"
        )
        values = [
            {"level_id": r[0], "id": r[1], "name": r[2], "parent": r[3]}
            for r in cur.fetchall()
        ]

    if not levels or not values:
        raise SystemExit(
            "MDS geo hierarchy is empty — seed g2p_geo_levels/g2p_geo_level_values first."
        )

    by_id = {v["id"]: v for v in values}
    # Deepest level = the one no other level claims as parent.
    parents = {lv["parent"] for lv in levels.values() if lv["parent"]}
    leaf_levels = [lid for lid in levels if lid not in parents]
    leaf_level = sorted(leaf_levels)[-1]

    # Poverty is spatially clustered: neighbouring places resemble each other,
    # and a poor region tends to contain poor districts. Drawing it per
    # household independently of place makes every area average out to the same
    # number — a choropleth then renders one flat colour and drilling into it
    # shows nothing. So give each node an offset inherited from its parent plus
    # its own smaller deviation, with the deviation shrinking as we go deeper.
    # Depth comes from the level hierarchy, not from the shape of the id. Path
    # ids ("kamuntu/kilima") happen to encode depth as separators; P-codes
    # ("ET0101") do not, and counting separators there would put every unit at
    # depth 0 and flatten the clustering back out.
    depth_of_level, seen = {}, set()
    for lid, lv in levels.items():
        d, cur = 0, lid
        while levels.get(cur, {}).get("parent") and cur not in seen:
            seen.add(cur)
            cur = levels[cur]["parent"]
            d += 1
        depth_of_level[lid] = d
        seen.clear()

    offsets = {}
    for v in sorted(values, key=lambda x: depth_of_level.get(x["level_id"], 0)):
        depth = depth_of_level.get(v["level_id"], 0)
        sigma = max(0.03, 0.16 / (depth + 1))
        offsets[v["id"]] = offsets.get(v["parent"], 0.0) + rng.gauss(0, sigma)

    leaves = []
    for v in values:
        if v["level_id"] != leaf_level:
            continue
        chain, node = [], v
        while node:
            chain.append(node)
            node = by_id.get(node["parent"]) if node["parent"] else None
        chain.reverse()
        leaves.append(
            {
                "chain": chain,
                "lowest": v["id"],
                "weight": rng.lognormvariate(0, 0.7),
                "poverty_offset": offsets.get(v["id"], 0.0),
            }
        )
    if not leaves:
        raise SystemExit("no leaf geo nodes found in MDS")
    return leaves, levels


def geo_json(chain, levels):
    """Build geo_code_hierarchy_json in the shape this deployment already uses."""
    return {
        "hierarchy": [
            {
                "level_mnemonic": levels[n["level_id"]]["mnemonic"],
                "level_value_id": n["id"],
                "level_value_mnemonic": n["name"],
            }
            for n in chain
        ],
        "lowest_level_value_id": chain[-1]["id"],
    }


# --------------------------------------------------------------------------
# schema introspection + COPY
# --------------------------------------------------------------------------
def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema='public' and table_name=%s",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def required_columns(conn, table):
    """NOT NULL columns with no default — every one must be supplied.

    Postgres reports a violation only once COPY is already streaming, as
    "null value in column X violates not-null constraint" against an opaque
    row. Checking up front turns a mid-load failure that leaves the register
    half-written into a clear message before anything is written.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select a.attname
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            join pg_attribute a on a.attrelid = c.oid
            left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
            where n.nspname = 'public' and c.relname = %s
              and a.attnum > 0 and not a.attisdropped
              and a.attnotnull and d.adbin is null
            """,
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


class Loader:
    """Buffers dict rows and COPYs them, using only columns the table has."""

    def __init__(self, conn, table, fields, dry_run=False):
        self.conn = conn
        self.table = table
        self.dry_run = dry_run
        self.available = table_columns(conn, table)
        if not self.available and not dry_run:
            raise SystemExit(f"table {table} not found in target schema")
        # Preserve caller order so the COPY column list is stable/readable.
        self.cols = [f for f in fields if f in self.available] or list(fields)
        self.skipped = [f for f in fields if f not in self.available]

        # Fail before writing anything if the schema demands a column this
        # script has no value for — otherwise the load dies partway through
        # and has to be purged before it can be retried.
        if self.available and not dry_run:
            missing = required_columns(conn, table) - set(self.cols)
            if missing:
                raise SystemExit(
                    f"[bulk-seed] ABORT: {table} requires column(s) "
                    f"{sorted(missing)} (NOT NULL, no default) but this "
                    f"generator supplies no value for them. Add them to the "
                    f"field list for this table, or give the column a default."
                )
        self.buf = io.StringIO()
        self.pending = 0
        self.total = 0

    def add(self, row):
        out = []
        for c in self.cols:
            v = row.get(c)
            if v is None:
                out.append("")
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            elif isinstance(v, bool):
                v = "true" if v else "false"
            else:
                v = str(v)
            # Minimal CSV quoting; every field is emitted quoted-safe.
            out.append('"' + v.replace('"', '""') + '"')
        self.buf.write(",".join(out) + "\n")
        self.pending += 1
        self.total += 1
        if self.pending >= CHUNK:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        if not self.dry_run:
            self.buf.seek(0)
            cols = ", ".join(f'"{c}"' for c in self.cols)
            with self.conn.cursor() as cur:
                cur.execute("set local synchronous_commit = off")
                cur.copy_expert(
                    f'COPY "public"."{self.table}" ({cols}) '
                    "FROM STDIN WITH (FORMAT csv, NULL '')",
                    self.buf,
                )
            self.conn.commit()
        self.buf = io.StringIO()
        self.pending = 0


def envelope(kind, seq, link=None):
    """The record envelope every NSR register row carries."""
    return {
        "functional_record_id": f"{kind}-B{seq:08d}",
        "link_internal_record_id": link,
        "created_by": SEEDER,
        "created_at": CREATED_AT,
        "last_approved_at": CREATED_AT,
        "last_approved_by": SEEDER,
        "record_status": ACTIVE,
    }


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def generate(conn, geo_conn, args, dist, rng):
    leaves, levels = load_geo(geo_conn, rng)
    leaf_weights = [lf["weight"] for lf in leaves]
    print(f"[bulk-seed] geo: {len(leaves)} leaf nodes, depth {len(levels)}")

    root = leaves[0]["chain"][0] if leaves and leaves[0]["chain"] else None
    root_id = root["id"] if root else "?"
    print(f"[bulk-seed] geo root: {root_id} ({root['name'] if root else '?'})")
    if args.expect_country and root_id != args.expect_country:
        raise SystemExit(
            f"[bulk-seed] ABORT: MDS holds geography for '{root_id}' but this "
            f"deployment expects '{args.expect_country}'. Seeding would create "
            f"records pointing at the wrong country's places. Check the "
            f"master-data chart's geoSeed.countryPack.")

    n_ind = args.individuals
    n_hh = max(1, n_ind // MEMBERS_PER_HOUSEHOLD)

    tables = {
        "hh": Loader(conn, "g2p_register_households", [
            "internal_record_id", "functional_record_id", "link_internal_record_id",
            "created_by", "created_at", "last_approved_at", "last_approved_by",
            "record_status", "record_name", "search_text",
            "geo_lowest_level_value_id", "geo_code_hierarchy_json",
            "household_head_internal_record_id", "household_head_name", "headship_type",
            "size_total", "size_adults", "size_children_u5", "size_school_age",
            "size_elderly", "number_of_female_members", "number_of_male_members",
            "elderly_member_present", "rooms_count", "overcrowding_indicator",
            "dwelling_type", "roof_material", "wall_material", "floor_material",
            "tenure_status", "water_source_type", "water_distance_minutes",
            "sanitation_type", "lighting_source", "cooking_fuel_type",
            "latitude", "longitude", "country_code",
            # legacy names, filled only if this schema still has them
            "household_size_total", "household_size_adults", "household_head_person_id",
        ], args.dry_run),
        "ind": Loader(conn, "g2p_register_individuals", [
            "internal_record_id", "functional_record_id", "link_internal_record_id",
            "created_by", "created_at", "last_approved_at", "last_approved_by",
            "record_status", "record_name", "search_text",
            "first_name", "last_name", "full_name", "gender", "birth_date",
            "estimated_age", "age_method", "marital_status", "education_level",
            "occupation", "registration_date", "relationship_to_head",
            "citizenship_category", "residency_status", "dependency_indicator",
            "disability_status", "plw_status", "orphanhood_flag",
            "chronic_illness_flag", "displacement_status",
            "pastoralist_classification", "high_mobility_indicator",
            "primary_livelihood", "secondary_livelihood", "employment_status",
            "coping_strategies_index", "phone_numbers",
            "foundational_id", "foundational_id_verification_status",
            "foundational_id_masked", "identity_evidence_type",
            "geo_lowest_level_value_id", "geo_code_hierarchy_json", "country_code",
            "educational_status", "is_head", "has_national_id",
        ], args.dry_run),
        "vuln": Loader(conn, "g2p_register_individual_vulnerability", [
            "internal_record_id", "functional_record_id", "link_internal_record_id",
            "created_by", "created_at", "last_approved_at", "last_approved_by",
            "record_status", "record_name", "search_text",
            "disability_status", "orphanhood_flag", "chronic_illness_flag",
            "displacement_status", "pastoralist_classification",
            "high_mobility_indicator", "plw_status",
        ], args.dry_run),
        "liv": Loader(conn, "g2p_register_individual_livelihoods", [
            "internal_record_id", "functional_record_id", "link_internal_record_id",
            "created_by", "created_at", "last_approved_at", "last_approved_by",
            "record_status", "record_name", "search_text",
            "primary_livelihood", "secondary_livelihood", "employment_status",
            "coping_strategies_index",
        ], args.dry_run),
        "house": Loader(conn, "g2p_register_household_housing_and_services", [
            "internal_record_id", "functional_record_id", "link_internal_record_id",
            "created_by", "created_at", "last_approved_at", "last_approved_by",
            "record_status", "record_name", "search_text",
            "dwelling_type", "roof_material", "wall_material", "floor_material",
            "tenure_status", "water_source_type", "water_distance_minutes",
            "sanitation_type", "lighting_source", "cooking_fuel_type",
        ], args.dry_run),
        "hhprog": Loader(conn, "g2p_register_household_programs", [
            "internal_record_id", "functional_record_id", "link_internal_record_id",
            "created_by", "created_at", "last_approved_at", "last_approved_by",
            "record_status", "record_name", "search_text",
            "program_name", "program_start_date", "program_exit_date",
        ], args.dry_run),
        # Scores carry no audit columns; every column below except
        # triggered_by_submission_id is NOT NULL, including triggered_by_cr_id
        # (the change request that caused the recompute).
        "score": Loader(conn, "g2p_register_scores", [
            "internal_record_id", "register_id", "score_type", "score_definition_id",
            "link_internal_record_id", "triggered_by_cr_id",
            "computed_score", "computed_at",
        ], args.dry_run),
    }
    for key, ld in tables.items():
        if ld.skipped:
            print(f"[bulk-seed] {ld.table}: skipping absent columns {ld.skipped}")

    register_id = args.register_id
    base = datetime.strptime(CREATED_AT, "%Y-%m-%d %H:%M:%S").date()

    def rid():
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))

    ind_seq = 0
    for h in range(n_hh):
        leaf = rng.choices(leaves, weights=leaf_weights, k=1)[0]
        gj = geo_json(leaf["chain"], levels)
        hh_id = rid()

        # Poverty drives score, deprivation and programme enrolment together, so
        # the seeded data has the correlations a targeting dashboard looks for.
        poverty = min(1.0, max(0.0, rng.betavariate(2, 3) + leaf["poverty_offset"]))

        size = max(1, min(14, int(rng.gauss(MEMBERS_PER_HOUSEHOLD + poverty * 2, 1.8))))
        size = min(size, max(1, n_ind - ind_seq))  # don't overshoot the target

        # Draw the members up front, then derive the household totals from them.
        # Deriving (rather than drawing both independently) is what keeps
        # size_* / number_of_*_members reconcilable against the individual rows —
        # a dashboard that cross-checks the two would otherwise show them
        # disagreeing. It also means the head's id exists before the household
        # row is serialised.
        members = []
        for m in range(size):
            band = dist["age_band"].pick()
            lo, hi = AGE_BANDS.get(band, (0, 80))
            members.append({
                "id": rid(),
                "age": rng.randint(lo, hi),
                # Non-heads are drawn with a compensating female skew. Heads are
                # deliberately male-biased below and are ~1 in 4 of everyone, so
                # drawing the rest at the raw population split would push the
                # overall marginal to ~54/46 and no longer match the reference
                # registry this data is meant to resemble.
                "gender": "MALE" if rng.random() < NON_HEAD_MALE_SHARE else "FEMALE",
                "is_head": False,
            })
        # Nearly every household has an adult in it. Drawing ages independently
        # produces all-minor households far too often (~8% at these band
        # weights, against roughly 1% child-headed in reality), so promote one
        # member to working age unless this is one of the rare genuine
        # child-headed cases.
        if not any(x["age"] >= 18 for x in members) and rng.random() > 0.01:
            members[0]["age"] = rng.randint(18, 64)
        head = next((x for x in members if x["age"] >= 18), members[0])
        head["is_head"] = True
        # Heads skew male in practice. Setting it here rather than post-hoc on
        # headship_type keeps the household's derived counts agreeing with its
        # members, while landing female-headed near the ~1-in-3 that social
        # protection targeting actually sees (a straight draw gives ~50%, which
        # would overstate a common targeting criterion).
        head["gender"] = "MALE" if rng.random() < HEAD_MALE_SHARE else "FEMALE"

        u5 = sum(1 for x in members if x["age"] < 5)
        school = sum(1 for x in members if 5 <= x["age"] < 18)
        elderly = sum(1 for x in members if x["age"] >= 65)
        adults = sum(1 for x in members if x["age"] >= 18)
        females = sum(1 for x in members if x["gender"] == "FEMALE")
        rooms = max(1, int(rng.gauss(3 - poverty, 1)) or 1)

        head_name = f"Head {h + 1}"
        hh = {
            "internal_record_id": hh_id,
            "record_name": head_name + " household",
            "search_text": f"HH-B{h + 1:08d} {leaf['chain'][-1]['name']}",
            "geo_lowest_level_value_id": leaf["lowest"],
            "geo_code_hierarchy_json": gj,
            "household_head_internal_record_id": head["id"],
            "household_head_name": head_name,
            "household_head_person_id": head["id"],
            # Headship follows the actual head, so "female-headed household"
            # counts agree with the individual records behind them.
            "headship_type": ("CHILD_HEADED" if head["age"] < 18
                              else "ELDERLY_HEADED" if head["age"] >= 65
                              else "FEMALE_HEADED" if head["gender"] == "FEMALE"
                              else "MALE_HEADED"),
            "size_total": size, "household_size_total": size,
            "size_adults": adults, "household_size_adults": adults,
            "size_children_u5": u5, "size_school_age": school, "size_elderly": elderly,
            "number_of_female_members": females,
            "number_of_male_members": size - females,
            "elderly_member_present": elderly > 0,
            "rooms_count": rooms,
            "overcrowding_indicator": round(size / rooms, 2),
            "tenure_status": rng.choices(TENURE_NAMES, weights=TENURE_WEIGHTS, k=1)[0],
            "water_distance_minutes": int(abs(rng.gauss(poverty * 45, 15))),
            "country_code": args.country_code,
        }
        for col, opts in HOUSING.items():
            hh[col] = deprived_pick(opts, poverty, rng)
        hh.update(envelope("HH", h + 1))
        hh["internal_record_id"] = hh_id  # envelope must not clobber the id

        # Housing detail also lives in its own sub-table on current schemas.
        house = {"internal_record_id": rid(),
                 "record_name": "Housing and services",
                 "search_text": hh["dwelling_type"]}
        house.update({c: hh[c] for c in HOUSING})
        house["tenure_status"] = hh["tenure_status"]
        house["water_distance_minutes"] = hh["water_distance_minutes"]
        house.update(envelope("HHS", h + 1, link=hh_id))

        score = {
            "internal_record_id": rid(),
            "register_id": register_id,
            "score_type": args.score_type,
            "score_definition_id": args.score_definition_id,
            "link_internal_record_id": hh_id,
            "triggered_by_cr_id": f"crB{h + 1:08d}",
            "computed_score": round(poverty * 100, 2),
            "computed_at": CREATED_AT,
        }

        tables["hh"].add(hh)
        tables["house"].add(house)
        tables["score"].add(score)

        # Poorer households are likelier to be enrolled — that's the point of
        # targeting, and a flat rate would make the dashboards look broken.
        if rng.random() < 0.05 + poverty * 0.12:
            prog = {
                "internal_record_id": rid(),
                "record_name": "Programme enrolment",
                "program_name": rng.choices(PROGRAM_NAMES, weights=PROGRAM_WEIGHTS, k=1)[0],
                "program_start_date": base - timedelta(days=rng.randint(30, 1400)),
                "program_exit_date": None,
            }
            prog["search_text"] = prog["program_name"]
            prog.update(envelope("HHP", h + 1, link=hh_id))
            tables["hhprog"].add(prog)

        for member in members:
            ind_seq += 1
            i_id = member["id"]
            age = member["age"]
            gender = member["gender"]
            is_head = member["is_head"]
            adult = age >= 18

            disability = dist["disability"].pick()
            plw = bool(gender == "FEMALE" and 15 <= age <= 49 and rng.random() < 0.13)
            orphan = bool(age < 18 and rng.random() < 0.05)
            chronic = bool(rng.random() < 0.07 + poverty * 0.05)
            displacement = rng.choices(DISPLACEMENT_NAMES, weights=DISPLACEMENT_WEIGHTS, k=1)[0]
            pastoral = rng.choices(PASTORALIST_NAMES, weights=PASTORALIST_WEIGHTS, k=1)[0]
            livelihood = dist["livelihood"].pick() if adult else None
            employment = dist["employment"].pick() if adult else None
            education = dist["education"].pick() if age >= 5 else None
            cs_index = int(min(20, max(0, rng.gauss(poverty * 12, 3))))

            # Foundational ID: the gate on whether someone can actually be paid.
            # Coverage falls with poverty and is lower for women — the exclusion
            # pattern G2P readiness dashboards exist to surface. Children are
            # enrolled far less often.
            id_chance = 0.88 - poverty * 0.30
            if gender == "FEMALE":
                id_chance -= 0.09
            if not adult:
                id_chance -= 0.45
            has_fid = rng.random() < max(0.02, id_chance)
            fid = f"FID{ind_seq:010d}" if has_fid else None
            fid_status = None
            if has_fid:
                fid_status = rng.choices(
                    ["VERIFIED", "PENDING", "FAILED"], weights=[78, 18, 4], k=1)[0]

            ind = {
                "internal_record_id": i_id,
                "record_name": f"Person {ind_seq}",
                "search_text": f"IND-B{ind_seq:08d} {leaf['chain'][-1]['name']} {gender}",
                "first_name": f"First{ind_seq}",
                "last_name": f"Last{h + 1}",
                "full_name": f"First{ind_seq} Last{h + 1}",
                "gender": gender,
                "birth_date": date(base.year - age, rng.randint(1, 12), rng.randint(1, 28)),
                "estimated_age": age,
                # AgeMethodEnum is DOCUMENTED/ESTIMATED — there is no DECLARED.
                "age_method": "DOCUMENTED" if rng.random() < 0.8 else "ESTIMATED",
                "marital_status": (rng.choices(MARITAL, weights=[30, 52, 9, 6, 3], k=1)[0]
                                   if adult else "SINGLE"),
                "education_level": education,
                "educational_status": education,
                "occupation": livelihood,
                "registration_date": base,
                "relationship_to_head": RELATIONSHIPS[0] if is_head else rng.choice(RELATIONSHIPS[1:]),
                "is_head": is_head,
                # CitizenshipCategoryEnum has no NON_CITIZEN; RESIDENT is the
                # non-citizen-but-settled category.
                "citizenship_category": "CITIZEN" if rng.random() < 0.97 else "RESIDENT",
                "residency_status": "RESIDENT",
                "dependency_indicator": (not adult) or age >= 65,
                "disability_status": disability,
                "plw_status": plw,
                "orphanhood_flag": orphan,
                "chronic_illness_flag": chronic,
                "displacement_status": displacement,
                "pastoralist_classification": pastoral,
                "high_mobility_indicator": pastoral == "PASTORALIST",
                "primary_livelihood": livelihood,
                "secondary_livelihood": (dist["livelihood"].pick()
                                         if adult and rng.random() < 0.25 else None),
                "employment_status": employment,
                "coping_strategies_index": cs_index,
                "foundational_id": fid,
                "foundational_id_verification_status": fid_status,
                "foundational_id_masked": (f"FID******{ind_seq % 10000:04d}"
                                           if has_fid else None),
                # IdentityEvidenceTypeEnum: a verified foundational id is
                # FOUNDATIONAL_ID_VERIFIED, not NATIONAL_ID.
                "identity_evidence_type": ("FOUNDATIONAL_ID_VERIFIED" if has_fid else "NONE"),
                # Digital-inclusion signal: phone ownership tracks wealth.
                "has_national_id": has_fid,
                "phone_numbers": ([{"phone_no": f"+2519{rng.randint(10**7, 10**8 - 1)}"}]
                                  if adult and rng.random() < (0.75 - poverty * 0.3) else None),
                "geo_lowest_level_value_id": leaf["lowest"],
                "geo_code_hierarchy_json": gj,
                "country_code": args.country_code,
            }
            ind.update(envelope("IND", ind_seq, link=hh_id))
            ind["internal_record_id"] = i_id
            tables["ind"].add(ind)

            vul = {
                "internal_record_id": rid(),
                "record_name": "Vulnerability indicators",
                "search_text": f"{disability} {displacement} {pastoral}",
                "disability_status": disability,
                "orphanhood_flag": orphan,
                "chronic_illness_flag": chronic,
                "displacement_status": displacement,
                "pastoralist_classification": pastoral,
                "high_mobility_indicator": pastoral == "PASTORALIST",
                "plw_status": plw,
            }
            vul.update(envelope("VUL", ind_seq, link=i_id))
            tables["vuln"].add(vul)

            if adult:
                lv = {
                    "internal_record_id": rid(),
                    "record_name": "Livelihoods",
                    "search_text": f"{livelihood} {employment}",
                    "primary_livelihood": livelihood,
                    "secondary_livelihood": ind["secondary_livelihood"],
                    "employment_status": employment,
                    "coping_strategies_index": cs_index,
                }
                lv.update(envelope("LIV", ind_seq, link=i_id))
                tables["liv"].add(lv)

        if (h + 1) % 20_000 == 0:
            print(f"[bulk-seed] {h + 1:,}/{n_hh:,} households, {ind_seq:,} individuals")

    for ld in tables.values():
        ld.flush()
    return {ld.table: ld.total for ld in tables.values()}


# Child-before-parent, so a purge can't be interrupted into a dangling state.
PURGE_ORDER = [
    "g2p_register_scores",
    "g2p_register_individual_livelihoods",
    "g2p_register_individual_vulnerability",
    "g2p_register_individual_programs",
    "g2p_register_household_programs",
    "g2p_register_household_housing_and_services",
    "g2p_register_individuals",
    "g2p_register_households",
]


def purge(conn):
    """Remove only what this script wrote, identified by created_by.

    Most generated rows carry created_by = SEEDER, which is what makes a bulk
    load reversible without touching the hand-written demo fixture or real data.
    `g2p_register_scores` has no audit columns at all, so its rows are matched
    through the household they point at instead — which is why scores are purged
    first, while those households still exist.
    """
    total = 0
    for table in PURGE_ORDER:
        cols = table_columns(conn, table)
        if not cols:
            continue
        if "created_by" in cols:
            sql = f'delete from "public"."{table}" where created_by = %s'
            params = (SEEDER,)
        elif "link_internal_record_id" in cols:
            sql = (f'delete from "public"."{table}" where link_internal_record_id in '
                   f'(select internal_record_id from "public"."g2p_register_households" '
                   f'where created_by = %s union all '
                   f'select internal_record_id from "public"."g2p_register_individuals" '
                   f'where created_by = %s)')
            params = (SEEDER, SEEDER)
        else:
            print(f"[bulk-seed] cannot identify seeded rows in {table} — skipping")
            continue
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
        total += n
        if n:
            print(f"[bulk-seed] purged {n:>10,} from {table}")
    print(f"[bulk-seed] purged {total:,} rows written by '{SEEDER}'")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=os.environ.get("NSR_DB", "nsr"),
                   help="target registry database (default: nsr)")
    p.add_argument("--geo-db", default=os.environ.get("MDS_DB", "master_data"),
                   help="database holding the MDS geo hierarchy (default: master_data)")
    p.add_argument("--individuals", type=int, default=1_000_000)
    # Data lives in the repo's data directories, not next to the scripts.
    p.add_argument("--distributions", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "national-social-registry", "distributions.json"))
    p.add_argument("--seed", type=int, default=20260401,
                   help="RNG seed; same seed + scale reproduces the same data")
    p.add_argument("--score-type", default="POVERTY")
    p.add_argument("--register-id", default=None,
                   help="register_id for generated scores; defaults to the one "
                        "already present in g2p_register_scores")
    p.add_argument("--score-definition-id", default=None,
                   help="score_definition_id for generated scores (NOT NULL in "
                        "the schema); defaults to the one already in use")
    p.add_argument("--country-code", default=None)
    p.add_argument("--expect-country", default=None,
                   help="Guard, not a selector. Fails if the MDS root geo node "
                        "is not this code (e.g. ET). MDS remains the single "
                        "place a country is chosen; this only catches a "
                        "registry pointed at the wrong environment.")
    p.add_argument("--dry-run", action="store_true",
                   help="generate and report counts without writing")
    p.add_argument("--purge", action="store_true",
                   help="delete rows previously written by this script "
                        f"(created_by='{SEEDER}') and exit. Leaves any other "
                        "seed or real data untouched.")
    args = p.parse_args()

    rng = random.Random(args.seed)

    dsn = dict(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )
    # The geo hierarchy lives in the Master Data database, which in a real
    # deployment is owned by a different role than the registry — the registry
    # user gets "permission denied for table g2p_geo_levels". Fall back to the
    # registry credentials so single-user setups (local, CI) still work.
    geo_dsn = dict(
        dsn,
        host=os.environ.get("MDS_PGHOST") or dsn["host"],
        port=os.environ.get("MDS_PGPORT") or dsn["port"],
        user=os.environ.get("MDS_PGUSER") or dsn["user"],
        password=os.environ.get("MDS_PGPASSWORD") or dsn["password"],
    )
    conn = psycopg2.connect(dbname=args.db, **dsn)
    geo_conn = psycopg2.connect(dbname=args.geo_db, **geo_dsn)

    if args.purge:
        # Before loading distributions: a purge only deletes, so requiring the
        # data file would make cleanup fail in environments that have the
        # script but not the pack.
        purge(conn)
        return 0

    dist = load_distributions(args.distributions, rng)

    # These two are NOT NULL / FK-ish on scores, so reuse whatever the existing
    # seed data already references rather than inventing values.
    with conn.cursor() as cur:
        if args.register_id is None:
            cur.execute("select register_id from g2p_register_scores"
                        " where register_id is not null limit 1")
            row = cur.fetchone()
            args.register_id = row[0] if row else str(uuid.uuid4())
        if args.score_definition_id is None:
            cur.execute("select score_definition_id from g2p_register_scores"
                        " where score_definition_id is not null limit 1")
            row = cur.fetchone()
            if not row:
                raise SystemExit(
                    "no score_definition_id found in g2p_register_scores; pass "
                    "--score-definition-id explicitly (the column is NOT NULL)")
            args.score_definition_id = row[0]
    print(f"[bulk-seed] register_id           = {args.register_id}")
    print(f"[bulk-seed] score_definition_id   = {args.score_definition_id}")

    started = datetime.now()
    print(f"[bulk-seed] target {args.db} @ {dsn['host']}:{dsn['port']}"
          f"{' (DRY RUN)' if args.dry_run else ''}")
    totals = generate(conn, geo_conn, args, dist, rng)
    elapsed = (datetime.now() - started).total_seconds()

    print(f"\n[bulk-seed] done in {elapsed:.0f}s")
    for t, n in sorted(totals.items()):
        print(f"    {t:<52} {n:>10,}")
    if args.dry_run:
        print("[bulk-seed] DRY RUN — nothing written")


if __name__ == "__main__":
    sys.exit(main())
