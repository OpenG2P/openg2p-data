"""Generate household-info/{individuals,households}.csv: consolidated wide CSVs.

Reads:
- demography/individuals.csv, demography/households.csv (core+geo)
- NSR sub-table JSONs from the NSR repo seed-data/ folder

Writes:
- household-info/individuals.csv (ind + livelihoods + livestock + land + shocks
  + disabilities + vulnerability + ind_programs)
- household-info/households.csv (hh + housing + assets + hh_programs + scores)

Sub-tables that are 1:N per entity are JSON-encoded into a single CSV cell.
"""

import csv
import json
from collections import defaultdict
from pathlib import Path

from _csv_utils import write_csv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demography"
NSR_SEED_DIR = Path(
    "/Volumes/Work/OpenG2P/national-social-registry/docker/db-seed/seed-data"
)
OUT_DIR = REPO_ROOT / "household-info"


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {k: (None if v == "" else v) for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def _read_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def _index_one(rows: list[dict]) -> dict:
    return {r["link_internal_record_id"]: r for r in rows}


def _index_many(rows: list[dict]) -> dict:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["link_internal_record_id"]].append(r)
    return dict(out)


def _slim(rec: dict, fields: list[str]) -> dict:
    return {k: rec.get(k) for k in fields}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    individuals = _read_csv(DEMO_DIR / "individuals.csv")
    households = _read_csv(DEMO_DIR / "households.csv")

    livelihoods = _index_one(_read_json(NSR_SEED_DIR / "individual_livelihoods.json"))
    vulnerability = _index_one(_read_json(NSR_SEED_DIR / "individual_vulnerability.json"))
    livestock = _index_many(_read_json(NSR_SEED_DIR / "individual_livestock.json"))
    land = _index_many(_read_json(NSR_SEED_DIR / "individual_land.json"))
    shocks = _index_many(_read_json(NSR_SEED_DIR / "individual_shocks.json"))
    disabilities = _index_many(_read_json(NSR_SEED_DIR / "individual_disabilities.json"))
    ind_programs = _index_many(_read_json(NSR_SEED_DIR / "individual_programs.json"))

    housing = _index_one(_read_json(NSR_SEED_DIR / "household_housing_and_services.json"))
    assets = _index_many(_read_json(NSR_SEED_DIR / "household_assets.json"))
    hh_programs = _index_many(_read_json(NSR_SEED_DIR / "household_programs.json"))
    scores = {
        r["link_internal_record_id"]: r
        for r in _read_json(NSR_SEED_DIR / "scores.json")
    }

    LIVELIHOOD_FIELDS = [
        "primary_livelihood", "secondary_livelihood", "employment_status",
        "coping_strategies_index", "mobile_phone_type",
    ]
    VULN_FIELDS = [
        "disability_status", "orphanhood_flag", "chronic_illness_flag",
        "displacement_status", "pastoralist_classification",
        "high_mobility_indicator", "plw_status", "plw_status_date",
    ]
    LIVESTOCK_FIELDS = ["livestock_species", "livestock_counts"]
    LAND_FIELDS = ["land_access", "land_size", "productive_assets"]
    SHOCK_FIELDS = ["shock_type", "shock_date", "shock_period", "coping_strategy"]
    DIS_FIELDS = ["disability_domain", "disability_severity"]
    IND_PROG_FIELDS = ["program_name", "program_start_date", "program_exit_date"]

    ind_rows = []
    for ind in individuals:
        rid = ind["internal_record_id"]
        liv = livelihoods.get(rid)
        vul = vulnerability.get(rid)
        row = dict(ind)
        for f in LIVELIHOOD_FIELDS:
            row[f] = liv.get(f) if liv else None
        for f in VULN_FIELDS:
            row[f] = vul.get(f) if vul else None
        row["livestock"] = [_slim(r, LIVESTOCK_FIELDS) for r in livestock.get(rid, [])]
        row["land"] = [_slim(r, LAND_FIELDS) for r in land.get(rid, [])]
        row["shocks"] = [_slim(r, SHOCK_FIELDS) for r in shocks.get(rid, [])]
        row["disabilities"] = [_slim(r, DIS_FIELDS) for r in disabilities.get(rid, [])]
        row["programs"] = [_slim(r, IND_PROG_FIELDS) for r in ind_programs.get(rid, [])]
        ind_rows.append(row)

    individual_columns = list(individuals[0].keys()) + (
        LIVELIHOOD_FIELDS + VULN_FIELDS
        + ["livestock", "land", "shocks", "disabilities", "programs"]
    )

    HOUSING_FIELDS = [
        "dwelling_type", "roof_material", "wall_material", "floor_material",
        "tenure_status", "water_source_type", "water_distance_minutes",
        "sanitation_type", "lighting_source", "cooking_fuel_type",
    ]
    ASSET_FIELDS = [
        "asset_type", "asset_category", "quantity",
        "size_value", "size_unit", "size_band", "details",
    ]
    HH_PROG_FIELDS = ["program_name", "program_start_date", "program_exit_date"]
    SCORE_FIELDS = [
        "score_type", "score_definition_id", "triggered_by_cr_id",
        "triggered_by_submission_id", "computed_score", "computed_at",
    ]

    hh_rows = []
    for hh in households:
        rid = hh["internal_record_id"]
        hsg = housing.get(rid)
        sc = scores.get(rid)
        row = dict(hh)
        for f in HOUSING_FIELDS:
            row[f] = hsg.get(f) if hsg else None
        row["assets"] = [_slim(r, ASSET_FIELDS) for r in assets.get(rid, [])]
        row["programs"] = [_slim(r, HH_PROG_FIELDS) for r in hh_programs.get(rid, [])]
        for f in SCORE_FIELDS:
            row[f] = sc.get(f) if sc else None
        hh_rows.append(row)

    household_columns = list(households[0].keys()) + (
        HOUSING_FIELDS + ["assets", "programs"] + SCORE_FIELDS
    )

    write_csv(OUT_DIR / "individuals.csv", individual_columns, ind_rows)
    write_csv(OUT_DIR / "households.csv", household_columns, hh_rows)
    print(f"Wrote household-info/individuals.csv: {len(ind_rows)} rows")
    print(f"Wrote household-info/households.csv: {len(hh_rows)} rows")


if __name__ == "__main__":
    main()
