"""Generate NSR sub-table JSON files from demography data.

Reads demography CSVs from openg2p-data/demography/ and writes JSON sub-table
files into the NSR repo's docker/db-seed/seed-data/ folder.
"""

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demography"
OUT_DIR = Path(
    "/Volumes/Work/OpenG2P/national-social-registry/docker/db-seed/seed-data"
)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            out = {}
            for k, v in row.items():
                if v == "":
                    out[k] = None
                else:
                    out[k] = v
            rows.append(out)
        return rows


def _parse_demo_individual(row: dict) -> dict:
    row = dict(row)
    if row.get("estimated_age") is not None:
        row["estimated_age"] = int(row["estimated_age"])
    return row


def _parse_demo_household(row: dict) -> dict:
    row = dict(row)
    for col in (
        "size_total", "size_adults", "size_children_u5",
        "size_school_age", "size_elderly",
        "number_of_female_members", "number_of_male_members",
    ):
        if row.get(col) is not None:
            row[col] = int(row[col])
    return row

SEED = 1337
random.seed(SEED)

# UUID prefixes per sub-table
PREFIXES = {
    "household_asset": "50000000-0000-4000-8000-",
    "household_housing": "55000000-0000-4000-8000-",
    "household_program": "56000000-0000-4000-8000-",
    "individual_shock": "60000000-0000-4000-8000-",
    "individual_land": "61000000-0000-4000-8000-",
    "individual_livelihood": "62000000-0000-4000-8000-",
    "individual_livestock": "63000000-0000-4000-8000-",
    "individual_vulnerability": "64000000-0000-4000-8000-",
    "individual_program": "30000000-0000-4000-8000-",
    "individual_disability": "a0000000-0000-4000-8000-",
    "score": "70000000-0000-4000-8000-",
}

SCORE_DEFINITION_ID = "e0000000-0000-4000-8000-000000000001"
CREATED_AT = "2026-04-01 00:00:00"
APPROVED_AT = "2026-04-01 00:00:00"
SEEDER = "seeder"

LIVELIHOODS = [
    "AGRICULTURE",
    "LIVESTOCK",
    "WAGE_LABOR",
    "BUSINESS_TRADE",
    "GOVT_EMPLOYMENT",
    "SERVICES",
    "CRAFT",
    "UNEMPLOYED",
]
EMPLOYMENT_STATUS = ["EMPLOYED", "SELF_EMPLOYED", "UNEMPLOYED", "RETIRED"]
PHONE_TYPES = ["SMARTPHONE", "BASIC", "NONE"]

LIVESTOCK_SPECIES = ["CATTLE", "GOATS", "SHEEP", "CHICKEN", "CAMEL", "DONKEY"]
LIVESTOCK_BANDS = ["BAND_1_5", "BAND_6_10", "BAND_11_20", "BAND_21_PLUS"]

PRODUCTIVE_ASSETS = ["PLOUGH", "TRACTOR", "OTHER"]

SHOCK_TYPES = ["JOB_LOSS", "ILLNESS", "DROUGHT", "FLOOD", "CONFLICT"]
COPING_STRATEGIES = ["CS_BORROW", "CS_SEEK_AID", "CS_REDUCE_MEALS", "CS_SELL_ASSETS"]

DISABILITY_DOMAINS = [
    "VISION",
    "HEARING",
    "MOBILITY",
    "COGNITION",
    "SELF_CARE",
    "COMMUNICATION",
]
DISABILITY_SEVERITY = [
    "NO_DIFFICULTY",
    "SOME_DIFFICULTY",
    "A_LOT_OF_DIFFICULTY",
    "CANNOT_DO_AT_ALL",
]

INDIVIDUAL_PROGRAMS = [
    "PROG_CASH_TRANSFER",
    "PROG_HEALTH_INSURANCE",
    "PROG_FOOD_AID",
    "PROG_EDUCATION_SUPPORT",
]
HOUSEHOLD_PROGRAMS = [
    "PROG_HH_SAFETY_NET",
    "PROG_HH_NUTRITION",
    "PROG_HH_LIVELIHOOD",
]

ASSET_CATEGORIES = {
    "CONSUMER_DURABLE": ["Television", "Radio", "Refrigerator", "Mobile Phone"],
    "VEHICLE": ["Bicycle", "Motorcycle", "Car"],
    "PRODUCTIVE": ["Plough", "Sewing Machine", "Water Pump"],
}

DWELLING_TYPES = ["PERMANENT", "SEMI_PERMANENT", "TEMPORARY"]
ROOF_MATERIALS = ["METAL_SHEET", "THATCH", "TILE", "TARPAULIN"]
WALL_MATERIALS = ["BRICK", "MUD_BRICK", "STONE", "BAMBOO"]
FLOOR_MATERIALS = ["CEMENT", "EARTH", "TILE"]
TENURE_STATUSES = ["OWNED", "RENTED", "HOSTED"]
WATER_SOURCES = ["PIPED", "WELL", "SPRING", "PUBLIC_TAP"]
SANITATION_TYPES = ["FLUSH_TOILET", "PIT_LATRINE", "SHARED"]
LIGHTING_SOURCES = ["GRID", "SOLAR", "KEROSENE"]
COOKING_FUELS = ["GAS", "ELECTRICITY", "FIREWOOD", "CHARCOAL", "OTHER"]

DISPLACEMENT_STATUSES = ["HOST_COMMUNITY", "IDP"]
PASTORALIST_CLASSIFICATIONS = ["SETTLED", "PASTORALIST"]


def uuid_for(table_key: str, seq: int) -> str:
    return f"{PREFIXES[table_key]}{seq:012d}"


def base_record(
    internal_id: str,
    functional_id: str,
    link_id: str,
    record_name: str,
    search_text: str,
) -> dict:
    return {
        "internal_record_id": internal_id,
        "functional_record_id": functional_id,
        "link_internal_record_id": link_id,
        "link_foundational_id": None,
        "record_name": record_name,
        "record_image_storage_id": None,
        "created_by": SEEDER,
        "created_at": CREATED_AT,
        "last_approved_at": APPROVED_AT,
        "last_approved_by": SEEDER,
        "search_text": search_text,
        "record_status": "ACTIVE",
        "record_status_reason": None,
    }


def gen_livelihoods(individuals: list[dict]) -> list[dict]:
    rows = []
    for i, ind in enumerate(individuals, start=1):
        if ind["estimated_age"] < 15:
            continue
        primary = random.choice(LIVELIHOODS)
        secondary = random.choice(LIVELIHOODS) if random.random() < 0.4 else None
        employment = random.choice(EMPLOYMENT_STATUS)
        phone_type = random.choice(PHONE_TYPES)
        coping_idx = random.randint(0, 7)
        rec = base_record(
            uuid_for("individual_livelihood", i),
            f"LIV-NSR-{i:04d}",
            ind["internal_record_id"],
            f"{primary} livelihood",
            f"LIV-NSR-{i:04d} {primary} {phone_type}",
        )
        rec.update(
            {
                "primary_livelihood": primary,
                "secondary_livelihood": secondary,
                "employment_status": employment,
                "coping_strategies_index": coping_idx,
                "mobile_phone_type": phone_type,
            }
        )
        rows.append(rec)
    return rows


def gen_livestock(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        if ind["estimated_age"] < 18:
            continue
        if random.random() > 0.30:
            continue
        n = random.randint(1, 3)
        for _ in range(n):
            seq += 1
            species = random.choice(LIVESTOCK_SPECIES)
            band = random.choice(LIVESTOCK_BANDS)
            rec = base_record(
                uuid_for("individual_livestock", seq),
                f"LST-NSR-{seq:04d}",
                ind["internal_record_id"],
                f"{species.title()} {band}",
                f"LST-NSR-{seq:04d} {species} {band}",
            )
            rec.update({"livestock_species": species, "livestock_counts": band})
            rows.append(rec)
    return rows


def gen_land(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        if ind["estimated_age"] < 18:
            continue
        if random.random() > 0.40:
            continue
        n = random.randint(1, 3)
        for _ in range(n):
            seq += 1
            land_access = random.random() < 0.85
            land_size = round(random.uniform(0.1, 10.0), 2) if land_access else None
            assets = random.sample(PRODUCTIVE_ASSETS, k=random.randint(0, 2))
            rec = base_record(
                uuid_for("individual_land", seq),
                f"LND-NSR-{seq:04d}",
                ind["internal_record_id"],
                f"Land plot {seq}",
                f"LND-NSR-{seq:04d} ACCESS {land_size or ''}".strip(),
            )
            rec.update(
                {
                    "land_access": str(land_access).upper(),
                    "land_size": land_size,
                    "productive_assets": json.dumps(assets) if assets else None,
                }
            )
            rows.append(rec)
    return rows


def gen_shocks(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        if ind["estimated_age"] < 18:
            continue
        if random.random() > 0.30:
            continue
        n = random.randint(1, 2)
        for _ in range(n):
            seq += 1
            shock = random.choice(SHOCK_TYPES)
            shock_date = date(2025, random.randint(1, 12), random.randint(1, 28))
            q = (shock_date.month - 1) // 3 + 1
            period = f"{shock_date.year}-Q{q}"
            cs = random.choice(COPING_STRATEGIES)
            rec = base_record(
                uuid_for("individual_shock", seq),
                f"SHK-NSR-{seq:04d}",
                ind["internal_record_id"],
                f"{shock} {period}",
                f"SHK-NSR-{seq:04d} {shock} {period} {cs.replace('CS_', '')}",
            )
            rec.update(
                {
                    "shock_type": shock,
                    "shock_date": shock_date.isoformat(),
                    "shock_period": period,
                    "coping_strategy": cs,
                }
            )
            rows.append(rec)
    return rows


def gen_disabilities(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        if random.random() > 0.15:
            continue
        domains = random.sample(DISABILITY_DOMAINS, k=random.randint(1, 2))
        for domain in domains:
            seq += 1
            severity = random.choice(DISABILITY_SEVERITY[1:])
            rec = base_record(
                uuid_for("individual_disability", seq),
                f"DIS-NSR-{seq:04d}",
                ind["internal_record_id"],
                f"{domain} {severity}",
                f"DIS-NSR-{seq:04d} {domain} {severity}",
            )
            rec.update(
                {"disability_domain": domain, "disability_severity": severity}
            )
            rows.append(rec)
    return rows


def gen_vulnerability(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        is_vulnerable = (
            ind["estimated_age"] < 18
            or ind["estimated_age"] >= 60
            or random.random() < 0.20
        )
        if not is_vulnerable:
            continue
        seq += 1
        disability_status = "YES" if random.random() < 0.15 else "NO"
        orphanhood = ind["estimated_age"] < 18 and random.random() < 0.10
        chronic = ind["estimated_age"] >= 50 and random.random() < 0.25
        displacement = random.choices(
            DISPLACEMENT_STATUSES, weights=[0.85, 0.15]
        )[0]
        pastoralist = random.choices(
            PASTORALIST_CLASSIFICATIONS, weights=[0.85, 0.15]
        )[0]
        high_mobility = pastoralist == "PASTORALIST"
        plw = (
            ind["gender"] == "FEMALE"
            and 18 <= ind["estimated_age"] < 45
            and random.random() < 0.10
        )
        plw_date = (
            date(2025, random.randint(1, 12), random.randint(1, 28)).isoformat()
            if plw
            else None
        )
        rec = base_record(
            uuid_for("individual_vulnerability", seq),
            f"VUL-NSR-{seq:04d}",
            ind["internal_record_id"],
            f"Vulnerability indicators",
            f"VUL-NSR-{seq:04d} {disability_status} {displacement} {pastoralist}",
        )
        rec.update(
            {
                "disability_status": disability_status,
                "orphanhood_flag": str(orphanhood).upper(),
                "chronic_illness_flag": str(chronic).upper(),
                "displacement_status": displacement,
                "pastoralist_classification": pastoralist,
                "high_mobility_indicator": str(high_mobility).upper(),
                "plw_status": "YES" if plw else "NO",
                "plw_status_date": plw_date,
            }
        )
        rows.append(rec)
    return rows


def gen_individual_programs(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        if ind["estimated_age"] < 18:
            continue
        if random.random() > 0.40:
            continue
        n = random.randint(1, 2)
        programs = random.sample(INDIVIDUAL_PROGRAMS, k=min(n, len(INDIVIDUAL_PROGRAMS)))
        for prog in programs:
            seq += 1
            start_date = date(
                random.randint(2023, 2025),
                random.randint(1, 12),
                random.randint(1, 28),
            )
            exit_date = None
            rec = base_record(
                uuid_for("individual_program", seq),
                f"PP-NSR-{seq:04d}",
                ind["internal_record_id"],
                prog.replace("PROG_", "").replace("_", " ").title(),
                f"PP-NSR-{seq:04d} {prog}",
            )
            rec.update(
                {
                    "program_name": prog,
                    "program_start_date": start_date.isoformat(),
                    "program_exit_date": exit_date,
                }
            )
            rows.append(rec)
    return rows


def gen_household_assets(households: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for hh in households:
        n = random.randint(0, 5)
        for _ in range(n):
            seq += 1
            asset_type = random.choice(list(ASSET_CATEGORIES.keys()))
            category = random.choice(ASSET_CATEGORIES[asset_type])
            quantity = random.randint(1, 5)
            rec = base_record(
                uuid_for("household_asset", seq),
                f"AST-NSR-{seq:04d}",
                hh["internal_record_id"],
                category,
                f"AST-NSR-{seq:04d} {asset_type} {category}",
            )
            rec.update(
                {
                    "asset_type": asset_type,
                    "asset_category": category,
                    "quantity": quantity,
                    "size_value": None,
                    "size_unit": None,
                    "size_band": None,
                    "details": json.dumps({"condition": "working"})
                    if random.random() < 0.3
                    else None,
                }
            )
            rows.append(rec)
    return rows


def gen_household_housing(households: list[dict]) -> list[dict]:
    rows = []
    for i, hh in enumerate(households, start=1):
        dwelling = random.choice(DWELLING_TYPES)
        roof = random.choice(ROOF_MATERIALS)
        wall = random.choice(WALL_MATERIALS)
        floor = random.choice(FLOOR_MATERIALS)
        tenure = random.choice(TENURE_STATUSES)
        water = random.choice(WATER_SOURCES)
        water_dist = random.randint(2, 45)
        sanitation = random.choice(SANITATION_TYPES)
        lighting = random.choice(LIGHTING_SOURCES)
        cooking = random.choice(COOKING_FUELS)
        rec = base_record(
            uuid_for("household_housing", i),
            f"HHS-NSR-{i:04d}",
            hh["internal_record_id"],
            f"Dwelling for {hh['functional_record_id']}",
            f"HHS-NSR-{i:04d} {dwelling} {tenure} {water}",
        )
        rec.update(
            {
                "dwelling_type": dwelling,
                "roof_material": roof,
                "wall_material": wall,
                "floor_material": floor,
                "tenure_status": tenure,
                "water_source_type": water,
                "water_distance_minutes": water_dist,
                "sanitation_type": sanitation,
                "lighting_source": lighting,
                "cooking_fuel_type": cooking,
            }
        )
        rows.append(rec)
    return rows


def gen_household_programs(households: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for hh in households:
        if random.random() > 0.50:
            continue
        n = random.randint(1, 2)
        programs = random.sample(HOUSEHOLD_PROGRAMS, k=min(n, len(HOUSEHOLD_PROGRAMS)))
        for prog in programs:
            seq += 1
            start_date = date(
                random.randint(2023, 2025),
                random.randint(1, 12),
                random.randint(1, 28),
            )
            rec = base_record(
                uuid_for("household_program", seq),
                f"HHP-NSR-{seq:04d}",
                hh["internal_record_id"],
                prog.replace("PROG_HH_", "").replace("_", " ").title(),
                f"HHP-NSR-{seq:04d} {prog}",
            )
            rec.update(
                {
                    "program_name": prog,
                    "program_start_date": start_date.isoformat(),
                    "program_exit_date": None,
                }
            )
            rows.append(rec)
    return rows


CR_UUID_PREFIX = "e0100000-0000-4000-8000-"


def gen_scores(households: list[dict]) -> list[dict]:
    rows = []
    for i, hh in enumerate(households, start=1):
        score = round(random.uniform(1.0, 5.0), 2)
        computed_at = (
            datetime(2026, 4, 1, 10, 0, 0) + timedelta(seconds=i)
        ).isoformat(sep=" ")
        rec = {
            "internal_record_id": uuid_for("score", i),
            "register_id": "a0000000-0000-4000-8000-000000000002",
            "score_type": "POVERTY",
            "score_definition_id": SCORE_DEFINITION_ID,
            "link_internal_record_id": hh["internal_record_id"],
            "triggered_by_cr_id": f"{CR_UUID_PREFIX}{i:012d}",
            "triggered_by_submission_id": None,
            "computed_score": score,
            "computed_at": computed_at,
        }
        rows.append(rec)
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    individuals = [_parse_demo_individual(r) for r in _read_csv(DEMO_DIR / "individuals.csv")]
    households = [_parse_demo_household(r) for r in _read_csv(DEMO_DIR / "households.csv")]

    generators = {
        "individual_livelihoods.json": gen_livelihoods(individuals),
        "individual_livestock.json": gen_livestock(individuals),
        "individual_land.json": gen_land(individuals),
        "individual_shocks.json": gen_shocks(individuals),
        "individual_disabilities.json": gen_disabilities(individuals),
        "individual_vulnerability.json": gen_vulnerability(individuals),
        "individual_programs.json": gen_individual_programs(individuals),
        "household_assets.json": gen_household_assets(households),
        "household_housing_and_services.json": gen_household_housing(households),
        "household_programs.json": gen_household_programs(households),
        "scores.json": gen_scores(households),
    }

    for fname, rows in generators.items():
        (OUT_DIR / fname).write_text(json.dumps(rows, indent=2) + "\n")
        print(f"Wrote {fname}: {len(rows)} records")


if __name__ == "__main__":
    main()
