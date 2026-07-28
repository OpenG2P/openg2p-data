"""Generate farmer-registry sub-table JSON files from shared demography data.

Reads demography CSVs from openg2p-data/demography/ (the same synthetic
population used by NSR) and writes JSON sub-table files into the
farmer-registry repo's docker/db-seed/seed-data/ folder.

Mapping to farmer registers:
- every individual becomes a Farmer (g2p_register_farmers reuses the individual
  internal_record_id; functional id FR-####)
- every individual that belongs to a household also becomes a HouseholdMember
- realistic land hierarchy: farmer -> land -> {crops, livestock, farm_inputs}
- ~25% of farmers get membership_details
- one poverty score per household
"""

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demography"
OUT_DIR = Path("/Volumes/Work/OpenG2P/farmer-registry/docker/db-seed/seed-data")

SEED = 2025
random.seed(SEED)

CREATED_AT = "2026-04-01 00:00:00"
APPROVED_AT = "2026-04-01 00:00:00"
SEEDER = "seeder"

# Score IDs from farmer-extension meta_data/register-metadata.
HOUSEHOLD_REGISTER_ID = "9055ab43-c85d-4833-bd00-ca657bb72644"
SCORE_DEFINITION_ID = "e7269b21-f234-411a-bb4d-16ca8b5f3cd3"

# Short, neat ID prefixes — distinct per table.
PREFIXES = {
    "household_member": "hhm",
    "land": "land",
    "crop": "crop",
    "livestock": "lvst",
    "farm_input": "fin",
    "membership": "mem",
    "score": "fsc",
    "triggered_by_cr": "fcr",
}

# ── enum option pools ────────────────────────────────────────────────────
DISABILITY_TYPES = ["VISION", "HEARING", "MOBILITY", "COGNITION", "SELF_CARE", "COMMUNICATION"]
DISABILITY_SEVERITY = ["SOME_DIFFICULTY", "A_LOT_OF_DIFFICULTY", "CANNOT_DO_AT_ALL"]
SOURCES_OF_INCOME = [
    "CROP_FARMING", "LIVESTOCK", "WAGE_LABOR", "BUSINESS_TRADE",
    "GOVERNMENT_NGO_SUPPORT", "REMITTANCES", "OTHERS",
]
LANGUAGES_SPOKEN = ["ENGLISH", "FRENCH", "SWAHILI", "HINDI", "LOCAL"]

LAND_OWNERSHIP = ["OWNER", "TENANT", "CROP_SHARE"]
LAND_UNITS = ["HECTARE", "ACRE", "SQUARE_METER"]
SOIL_FERTILITY = ["HIGH", "MEDIUM", "LOW"]
LAND_USE = ["AGRICULTURAL", "RESIDENTIAL", "GRAZING", "FOREST"]
FARMING_TYPE = ["CROP", "LIVESTOCK", "MIXED", "AQUACULTURE", "AGROFORESTRY"]
MEANS_OF_ACQUISITION = ["EXPROPRIATION", "RENTING_LEASING", "INHERITANCE"]
SHAPE_TYPES = ["POLYGON", "POINT"]

COMMODITIES = ["WHEAT", "MAIZE", "SOYBEAN", "OTHER"]
SEASONS = ["SUMMER", "MONSOON", "WINTER"]
END_USES = ["FOOD_HUMAN_CONSUMPTION", "FEED_ANIMALS", "BIOFUELS_NONFOOD", "OTHER"]

LIVESTOCK_TYPES = ["CATTLE", "SHEEP", "GOAT", "CHICKEN"]
BREEDS = ["LOCAL", "IMPROVED", "HYBRID"]
LIVESTOCK_SYSTEMS = ["NOMADIC_PASTORAL", "SEMI_NOMADIC", "SEDENTARY_PASTORAL", "MIXED", "INDUSTRIAL"]

WATER_SOURCES = [
    "RAINFED", "IRRIGATION_GROUND_WATER", "IRRIGATION_SURFACE_WATER",
    "SURFACE_WATER", "WATER_HARVESTING", "WELL_GROUND_WATER",
]
CLUSTER_ROLES = ["LEAD", "DEPUTY", "SECRETARY", "ACCOUNTANT", "MEMBER"]
COOPERATIVE_NAMES = [
    "District Farmers Union", "Raghunath Producer Group", "Regional Agri Union",
    "Green Valley Cooperative", "Sunrise Growers Society",
]


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            rows.append({k: (None if v == "" else v) for k, v in row.items()})
        return rows


def _seq_of(functional_id: str) -> int:
    return int(functional_id.split("-")[1])


def uuid_for(table_key: str, seq: int) -> str:
    return f"{PREFIXES[table_key]}{seq:04d}"


def _bool(p: float) -> str:
    return "TRUE" if random.random() < p else "FALSE"


def base_record(internal_id, functional_id, link_id, record_name, search_text) -> dict:
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


def gen_farmers(individuals: list[dict]) -> list[dict]:
    """Farmer-specific extras keyed by the individual internal_record_id.
    Person/geo fields are joined from individuals.csv at load time."""
    rows = []
    for ind in individuals:
        disabled = random.random() < 0.12
        source = random.choice(SOURCES_OF_INCOME)
        rows.append(
            {
                "internal_record_id": ind["internal_record_id"],
                "disabled": "TRUE" if disabled else "FALSE",
                "disability_type": random.choice(DISABILITY_TYPES) if disabled else None,
                "disability_severity": random.choice(DISABILITY_SEVERITY) if disabled else None,
                "source_of_income": source,
                "source_of_income_other": "Seasonal work" if source == "OTHERS" else None,
                "has_personal_phone": _bool(0.8),
                "language_spoken": random.choice(LANGUAGES_SPOKEN),
            }
        )
    return rows


def gen_household_members(individuals: list[dict]) -> list[dict]:
    """One HouseholdMember per individual that belongs to a household. Person
    fields are joined from individuals.csv (via member_individual_id) at load."""
    rows = []
    seq = 0
    for ind in individuals:
        if not ind.get("household_id"):
            continue
        seq += 1
        rows.append(
            {
                "internal_record_id": uuid_for("household_member", seq),
                "functional_record_id": f"HHM-{seq:04d}",
                "link_internal_record_id": ind["household_id"],
                "member_individual_id": ind["internal_record_id"],
                "is_disabled": _bool(0.1),
            }
        )
    return rows


def gen_lands(individuals: list[dict]) -> list[dict]:
    """Farmer -> land. ~65% of farmers own 1-2 plots. Carries geo/address copied
    from the farmer's record so the land sits in the same locality."""
    rows = []
    seq = 0
    for ind in individuals:
        if random.random() > 0.65:
            continue
        n = random.randint(1, 2)
        for _ in range(n):
            seq += 1
            ownership = random.choice(LAND_OWNERSHIP)
            rec = base_record(
                uuid_for("land", seq),
                f"LAND-{seq:04d}",
                ind["internal_record_id"],
                f"Land Plot {seq}",
                f"LAND-{seq:04d} {ownership} {ind['full_name']}",
            )
            rec.update(
                {
                    "land_ownership_type": ownership,
                    "certificate_storage_id": None,
                    "land_size": str(round(random.uniform(0.5, 12.0), 2)),
                    "unit": random.choice(LAND_UNITS),
                    "soil_fertility": random.choice(SOIL_FERTILITY),
                    "current_land_use": random.choice(LAND_USE),
                    "farming_type": random.choice(FARMING_TYPE),
                    "year_of_acquisition": random.randint(1985, 2024),
                    "means_of_acquisition": random.choice(MEANS_OF_ACQUISITION),
                    "latitude": ind["latitude"],
                    "longitude": ind["longitude"],
                    "altitude": ind["altitude"],
                    "plus_code": ind["plus_code"],
                    "address_line_1": ind["address_line_1"],
                    "address_line_2": ind["address_line_2"],
                    "postal_code": ind["postal_code"],
                    "country_code": ind["country_code"],
                    "country": ind["country"],
                    "region": ind["region"],
                    "district": ind["district"],
                    "ward": ind["ward"],
                    "village": ind["village"],
                    "shape_type": random.choice(SHAPE_TYPES),
                    "shape_coordinates_json": {
                        "type": "Point",
                        "coordinates": [
                            round(float(ind["longitude"]) + random.uniform(-0.02, 0.02), 6),
                            round(float(ind["latitude"]) + random.uniform(-0.02, 0.02), 6),
                        ],
                    },
                }
            )
            rows.append(rec)
    return rows


def gen_crops(lands: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for land in lands:
        for _ in range(random.randint(0, 3)):
            seq += 1
            commodity = random.choice(COMMODITIES)
            season = random.choice(SEASONS)
            end_use = random.choice(END_USES)
            rec = base_record(
                uuid_for("crop", seq),
                f"CROP-{seq:04d}",
                land["internal_record_id"],
                f"{commodity.title()} {season.title()}",
                f"CROP-{seq:04d} {commodity} {season} {end_use}",
            )
            rec.update(
                {
                    "commodity": commodity,
                    "planted_date": None,
                    "season": season,
                    "end_use": end_use,
                }
            )
            rows.append(rec)
    return rows


def gen_livestocks(lands: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for land in lands:
        for _ in range(random.randint(0, 2)):
            seq += 1
            ltype = random.choice(LIVESTOCK_TYPES)
            breed = random.choice(BREEDS)
            system = random.choice(LIVESTOCK_SYSTEMS)
            rec = base_record(
                uuid_for("livestock", seq),
                f"LIVESTOCK-{seq:04d}",
                land["internal_record_id"],
                f"{ltype.title()} {breed.title()}",
                f"LIVESTOCK-{seq:04d} {ltype} {breed} {system}",
            )
            rec.update(
                {
                    "livestock_type": ltype,
                    "breed": breed,
                    "head_count": random.randint(1, 60),
                    "livestock_system": system,
                }
            )
            rows.append(rec)
    return rows


def gen_farm_inputs(lands: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for land in lands:
        if random.random() > 0.85:
            continue
        seq += 1
        water = random.choice(WATER_SOURCES)
        rec = base_record(
            uuid_for("farm_input", seq),
            f"FINPUT-{seq:04d}",
            land["internal_record_id"],
            water,
            f"FINPUT-{seq:04d} {water}",
        )
        rec.update(
            {
                "fertilizer_use": _bool(0.6),
                "pesticide_use": _bool(0.5),
                "insecticide_use": _bool(0.5),
                "improved_seed_use": _bool(0.5),
                "water_source": water,
                "access_to_machinery": _bool(0.4),
                "access_to_finance": _bool(0.35),
            }
        )
        rows.append(rec)
    return rows


def gen_membership_details(individuals: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for ind in individuals:
        if random.random() > 0.25:
            continue
        seq += 1
        is_coop = random.random() < 0.6
        is_union = random.random() < 0.5
        is_cluster = random.random() < 0.4
        rec = base_record(
            uuid_for("membership", seq),
            f"MEMB-{seq:04d}",
            ind["internal_record_id"],
            f"Membership - {ind['full_name']}",
            f"MEMB-{seq:04d} {ind['full_name']}",
        )
        rec.update(
            {
                "is_primary_cooperative_member": "TRUE" if is_coop else "FALSE",
                "primary_cooperative_name": random.choice(COOPERATIVE_NAMES) if is_coop else None,
                "is_cooperative_union_member": "TRUE" if is_union else "FALSE",
                "cooperative_union_name": random.choice(COOPERATIVE_NAMES) if is_union else None,
                "is_farmer_cluster_member": "TRUE" if is_cluster else "FALSE",
                "farmer_cluster_role": random.choice(CLUSTER_ROLES) if is_cluster else None,
            }
        )
        rows.append(rec)
    return rows


def gen_scores(households: list[dict]) -> list[dict]:
    """One poverty score per household, matching the score definition weights:
    score = size_of_group * 0.45 + number_of_children * 0.55."""
    rows = []
    for i, hh in enumerate(households, start=1):
        size_of_group = int(hh["size_total"])
        num_children = int(hh["size_children_u5"]) + int(hh["size_school_age"])
        score = round(size_of_group * 0.45 + num_children * 0.55, 2)
        computed_at = (datetime(2026, 4, 1, 10, 0, 0) + timedelta(seconds=i)).isoformat(sep=" ")
        rows.append(
            {
                "internal_record_id": uuid_for("score", i),
                "register_id": HOUSEHOLD_REGISTER_ID,
                "score_type": "POVERTY",
                "score_definition_id": SCORE_DEFINITION_ID,
                "link_internal_record_id": hh["internal_record_id"],
                "triggered_by_cr_id": uuid_for("triggered_by_cr", i),
                "triggered_by_submission_id": None,
                "computed_score": score,
                "computed_at": computed_at,
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    individuals = _read_csv(DEMO_DIR / "individuals.csv")
    households = _read_csv(DEMO_DIR / "households.csv")

    farmers = gen_farmers(individuals)
    household_members = gen_household_members(individuals)
    lands = gen_lands(individuals)
    crops = gen_crops(lands)
    livestocks = gen_livestocks(lands)
    farm_inputs = gen_farm_inputs(lands)
    membership_details = gen_membership_details(individuals)
    scores = gen_scores(households)

    outputs = {
        "farmers.json": farmers,
        "household_members.json": household_members,
        "lands.json": lands,
        "crops.json": crops,
        "livestocks.json": livestocks,
        "farm_inputs.json": farm_inputs,
        "membership_details.json": membership_details,
        "scores.json": scores,
    }

    for fname, rows in outputs.items():
        (OUT_DIR / fname).write_text(json.dumps(rows, indent=2) + "\n")
        print(f"Wrote {fname}: {len(rows)} records")


if __name__ == "__main__":
    main()
